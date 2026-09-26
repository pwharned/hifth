package com.github.pwharned.hifth.backend.modules
import cats.effect.{IO, Ref, Resource}
import fs2.{Pipe, Stream}
import fs2.concurrent.Topic
import org.http4s.websocket.WebSocketFrame
import com.github.plokhotnyuk.jsoniter_scala.core.*
import com.github.plokhotnyuk.jsoniter_scala.macros.*
import com.github.pwharned.hifth.shared.protocol.*
import com.github.pwharned.hifth.backend.MessageHandler
import cats.effect.std.Queue
class WsModule(
    handler: MessageHandler[IO, ClientMessage, ServerMessage],
    topic: Topic[IO, ServerMessage]
):
  def connection
      : IO[(Stream[IO, WebSocketFrame], Pipe[IO, WebSocketFrame, Unit])] =
    Queue.unbounded[IO, ServerMessage].map { queue =>
      val send: Stream[IO, WebSocketFrame] =
        topic
          .subscribe(100)
          .merge(Stream.fromQueueUnterminated(queue))
          .map(msg => WebSocketFrame.Text(writeToString(msg)))
      val receive: Pipe[IO, WebSocketFrame, Unit] = _.evalMap:
        case WebSocketFrame.Text(text, _) =>
          IO(readFromString[ClientMessage](text))
            .flatMap(handler.handle)
            .flatMap:
              case Delivery.Unicast(msg)   => queue.offer(msg)
              case Delivery.Broadcast(msg) => topic.publish1(msg).void
            .handleErrorWith(e => IO(println(s"WS error: ${e.getMessage}")))
        case _ => IO.unit
      (send, receive)
    }

object WsModule:
  def make(
      handler: MessageHandler[IO, ClientMessage, ServerMessage]
  ): Resource[IO, WsModule] =
    for topic <- Resource.eval(Topic[IO, ServerMessage])
    yield WsModule(handler, topic)
