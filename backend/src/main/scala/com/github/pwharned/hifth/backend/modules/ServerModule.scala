package com.github.pwharned.hifth.backend.modules
import cats.effect.{IO, Resource}
import com.comcast.ip4s.*
import org.http4s.*
import org.http4s.dsl.io.*
import org.http4s.ember.server.EmberServerBuilder
import org.http4s.server.websocket.WebSocketBuilder2
import org.http4s.server.Server
import org.http4s.StaticFile
import org.http4s.headers.`Content-Type`
import org.http4s.ServerSentEvent
import fs2.Stream
import fs2.io.file.Path as Fs2Path
import scala.concurrent.duration.Duration
import org.typelevel.ci.CIStringSyntax
import cats.MonoidK.ops.toAllMonoidKOps
import com.github.pwharned.hifth.backend.{AssetMode, Config}
class ServerModule(config: Config, ws: WsModule):
  private def wsRoutes(wsb: WebSocketBuilder2[IO]): HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case GET -> Root / "ws" =>
        ws.connection.flatMap { (send, receive) =>
          wsb.build(send, receive)
        }
  private def devReloadRoute: HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case GET -> Root / "dev-reload" =>
        Ok(
          Stream.never[IO].as(ServerSentEvent(None)),
          Headers(`Content-Type`(MediaType.`text/event-stream`))
        )
  private def assetRoutes: HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case req @ GET -> Root =>
        serveAsset("index.html", req)
      case req @ GET -> Root / "js" / filename =>
        serveJs(filename, req)
      // Alignment JSON files served from /data/surah/:filename
      // e.g. GET /data/surah/001_aligned.json
      case req @ GET -> Root / "data" / "audio" / filename =>
        println("Serving audio")
        StaticFile
          .fromPath(Fs2Path(s"./data/raw_audio/$filename"), Some(req))
          .getOrElseF(NotFound())
          .map(x =>
            x.putHeaders(
              Header.Raw(
                ci"Access-Control-Expose-Headers",
                "Accept-Ranges, Content-Range, Content-Length"
              )
            ).putHeaders(Header.Raw(ci"Accept-Ranges", "bytes"))
          )

      case req @ GET -> Root / "data" / "surah" / filename =>
        serveDataAsset(filename, req)
      case req @ GET -> path =>
        serveAsset(path.toString.stripPrefix("/"), req)
  private def serveAsset(name: String, req: Request[IO]): IO[Response[IO]] =
    StaticFile
      .fromResource(s"static/$name", Some(req))
      .getOrElseF(NotFound())
  private def serveJs(filename: String, req: Request[IO]): IO[Response[IO]] =
    config.assetMode match
      case AssetMode.Dev =>
        StaticFile
          .fromPath(Fs2Path(s"./static/js/$filename"), Some(req))
          .getOrElseF(NotFound())
          .map(
            _.putHeaders(
              Header
                .Raw(ci"Cache-Control", "no-store, no-cache, must-revalidate"),
              Header.Raw(ci"Pragma", "no-cache")
            )
          )
      case AssetMode.Prod =>
        StaticFile
          .fromResource(s"static/js/$filename", Some(req))
          .getOrElseF(NotFound())
  // Alignment JSON files live in resources/static/data/surah/
  private def serveDataAsset(
      filename: String,
      req: Request[IO]
  ): IO[Response[IO]] =
    StaticFile
      .fromResource(s"static/data/surah/$filename", Some(req))
      .getOrElseF(NotFound())
      .map(x =>
        x.putHeaders(
          Header.Raw(
            ci"Access-Control-Expose-Headers",
            "Accept-Ranges, Content-Range, Content-Length"
          )
        )
      )
  private def routes(wsb: WebSocketBuilder2[IO]): HttpRoutes[IO] =
    val base = wsRoutes(wsb) <+> assetRoutes
    config.assetMode match
      case AssetMode.Dev  => devReloadRoute <+> base
      case AssetMode.Prod => base
object ServerModule:
  def make(config: Config, ws: WsModule): Resource[IO, Server] =
    EmberServerBuilder
      .default[IO]
      .withHost(Host.fromString(config.host).getOrElse(host"0.0.0.0"))
      .withPort(Port.fromInt(config.port).getOrElse(port"8080"))
      .withShutdownTimeout(Duration.Zero)
      .withHttpWebSocketApp(wsb =>
        ServerModule(config, ws).routes(wsb).orNotFound
      )
      .build
