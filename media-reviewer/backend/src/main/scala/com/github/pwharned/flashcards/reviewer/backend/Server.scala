package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.{IO, Resource}
import cats.effect.syntax.all.*
import cats.effect.std.{Queue, Semaphore}
import cats.syntax.all.*
import com.github.plokhotnyuk.jsoniter_scala.core.{readFromString, writeToString}
import com.github.pwharned.flashcards.shared.protocol.{ClientMessage, ServerMessage}
import fs2.{Pipe, Stream}
import fs2.io.readInputStream
import org.http4s.*
import org.http4s.dsl.io.*
import org.http4s.ember.server.EmberServerBuilder
import org.http4s.implicits.*
import org.http4s.server.Server as Http4sServer
import org.http4s.server.websocket.WebSocketBuilder2
import org.http4s.websocket.WebSocketFrame
import org.typelevel.ci.CIStringSyntax
import java.security.SecureRandom
import java.util.HexFormat
import scala.concurrent.duration.*

final class Server(
    config: Config,
    source: LoadedArtifact,
    google: GoogleGateway,
    anki: AnkiGateway,
    preparations: EphemeralAudioStore,
    workGate: Semaphore[IO],
    sessionToken: String
):
  private val CookieName = "hifth_media_reviewer"
  private val allowedOrigins = Set(
    s"http://127.0.0.1:${config.port.value}",
    s"http://localhost:${config.port.value}",
    s"http://[::1]:${config.port.value}"
  )

  def resource: Resource[IO, Http4sServer] =
    val cleanup = Resource.make(
      (IO.sleep(1.minute) *> preparations.pruneExpired).foreverM.start
    )(_.cancel)
    cleanup *> EmberServerBuilder
        .default[IO]
        .withHost(config.host)
        .withPort(config.port)
        .withIdleTimeout(35.minutes)
        .withShutdownTimeout(Duration.Zero)
        .withHttpWebSocketApp(builder => routes(builder).orNotFound)
        .build

  private def routes(builder: WebSocketBuilder2[IO]): HttpRoutes[IO] =
    websocketRoutes(builder) <+> preparedAudioRoutes <+> mediaRoutes <+> staticRoutes

  private def websocketRoutes(builder: WebSocketBuilder2[IO]): HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case request @ GET -> Root / "ws" =>
        if hasSession(request) && hasAllowedOrigin(request) then websocket(builder)
        else Forbidden("reviewer WebSocket authorization failed")

  private def websocket(builder: WebSocketBuilder2[IO]): IO[Response[IO]] =
    randomOwnerId.flatMap: ownerId =>
      val reviewer = Reviewer(
        config,
        source,
        google,
        anki,
        preparations,
        ownerId,
        workGate
      )
      Queue.bounded[IO, ServerMessage](64).flatMap: queue =>
        val send = Stream
          .fromQueueUnterminated(queue)
          .map(message => WebSocketFrame.Text(writeToString(message)))
          .onFinalize(reviewer.close)
        val receive: Pipe[IO, WebSocketFrame, Unit] =
          _.evalMap:
            case WebSocketFrame.Text(text, _) =>
              IO.delay(readFromString[ClientMessage](text))
                .flatMap(reviewer.start)
                .map(_.map(Some(_)))
                .handleError: error =>
                  IO.pure(
                    Some(
                      ServerMessage.RequestFailed(
                        "",
                        s"invalid client message: ${errorMessage(error)}"
                      )
                    )
                  )
            case _: WebSocketFrame.Binary =>
              IO.pure(
                IO.pure(
                  Some(
                    ServerMessage.RequestFailed(
                      "",
                      "binary WebSocket messages are not supported"
                    )
                  )
                )
              )
            case _ => IO.pure(IO.pure(None))
          .parEvalMapUnordered(4)(identity)
          .unNone
          .evalMap(queue.offer)
          .onFinalize(reviewer.close)
        builder.build(send, receive)

  private def preparedAudioRoutes: HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case request @ GET -> Root / "prepared-audio" / audioId =>
        if !hasSession(request) then Forbidden("reviewer session required")
        else
          preparations.find(audioId).flatMap:
            case Some(prepared) => servePreparedAudio(request, prepared)
            case None           => NotFound("prepared audio is unavailable")

  private def mediaRoutes: HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case request @ GET -> Root / "media" / id =>
        if !hasSession(request) then Forbidden("reviewer session required")
        else source.mediaById.get(id) match
          case None => NotFound(s"unknown media: $id")
          case Some(media) =>
            IO.blocking(media.verifyMetadata()).attempt.flatMap:
              case Left(error) => Conflict(errorMessage(error))
              case Right(_) => serveMedia(request, media)

  private def staticRoutes: HttpRoutes[IO] =
    HttpRoutes.of[IO]:
      case request @ GET -> Root =>
        serveResource("index.html", request).map(
          _.putHeaders(
            Header.Raw(
              ci"Set-Cookie",
              s"$CookieName=$sessionToken; Path=/; HttpOnly; SameSite=Strict"
            ),
            Header.Raw(ci"Cache-Control", "no-store")
          )
        )
      case request @ GET -> path =>
        val name = path.toString.stripPrefix("/")
        if safeResourceName(name) then serveResource(name, request) else NotFound()

  private def serveResource(name: String, request: Request[IO]): IO[Response[IO]] =
    StaticFile.fromResource(s"static/$name", Some(request)).getOrElseF(NotFound())

  private def servePreparedAudio(
      request: Request[IO],
      prepared: StoredPreparation
  ): IO[Response[IO]] =
    val bytes = prepared.bytes
    val size = bytes.length.toLong
    val commonHeaders = Headers(
      Header.Raw(ci"Accept-Ranges", "bytes"),
      Header.Raw(ci"Cache-Control", "private, no-store"),
      Header.Raw(ci"Content-Type", prepared.audio.contentType),
      Header.Raw(ci"ETag", s"\"${prepared.audio.sha256}\""),
      Header.Raw(ci"X-Content-Type-Options", "nosniff")
    )
    header(request, ci"Range") match
      case None =>
        IO.pure(
          Response[IO](
            status = Status.Ok,
            headers = commonHeaders.put(Header.Raw(ci"Content-Length", size.toString)),
            body = Stream.emits(bytes).covary[IO]
          )
        )
      case Some(value) =>
        parseRange(value, size) match
          case Left(_) =>
            IO.pure(
              Response[IO](Status.RangeNotSatisfiable).withHeaders(
                commonHeaders.put(Header.Raw(ci"Content-Range", s"bytes */$size"))
              )
            )
          case Right((start, end)) =>
            val length = end - start + 1
            val selected = java.util.Arrays.copyOfRange(bytes, start.toInt, end.toInt + 1)
            IO.pure(
              Response[IO](
                status = Status.PartialContent,
                headers = commonHeaders
                  .put(Header.Raw(ci"Content-Range", s"bytes $start-$end/$size"))
                  .put(Header.Raw(ci"Content-Length", length.toString)),
                body = Stream.emits(selected).covary[IO]
              )
            )

  private def serveMedia(request: Request[IO], media: ResolvedMedia): IO[Response[IO]] =
    header(request, ci"Range") match
      case None =>
        val body = readInputStream(openMedia(media, 0), 64 * 1024, closeAfterUse = true)
        IO.pure(
          Response[IO](Status.Ok, body = body).putHeaders(
            Header.Raw(ci"Accept-Ranges", "bytes"),
            Header.Raw(ci"Content-Length", media.size.toString),
            Header.Raw(ci"Content-Type", mediaContentType(media))
          )
        )
      case Some(value) =>
        parseRange(value, media.size) match
          case Left(_) =>
            IO.pure(
              Response[IO](Status.RangeNotSatisfiable).putHeaders(
                Header.Raw(ci"Content-Range", s"bytes */${media.size}"),
                Header.Raw(ci"Accept-Ranges", "bytes")
              )
            )
          case Right((start, end)) =>
            val length = end - start + 1
            val body = readInputStream(
              openMedia(media, start),
              chunkSize = 64 * 1024,
              closeAfterUse = true
            ).take(length)
            IO.pure(
              Response[IO](Status.PartialContent, body = body).putHeaders(
                Header.Raw(ci"Accept-Ranges", "bytes"),
                Header.Raw(ci"Content-Range", s"bytes $start-$end/${media.size}"),
                Header.Raw(ci"Content-Length", length.toString),
                Header.Raw(ci"Content-Type", mediaContentType(media))
              )
            )

  private def openMedia(media: ResolvedMedia, start: Long): IO[java.io.InputStream] =
    IO.blocking:
      media.verifyMetadata()
      val input = java.nio.file.Files.newInputStream(media.path)
      try
        media.verifyMetadata()
        input.skipNBytes(start)
        input
      catch
        case error: Throwable =>
          input.close()
          throw error

  private def mediaContentType(media: ResolvedMedia): String =
    Option(java.nio.file.Files.probeContentType(media.path)).getOrElse("application/octet-stream")

  private def parseRange(value: String, size: Long): Either[String, (Long, Long)] =
    val prefix = "bytes="
    val range = value.trim
    if !range.startsWith(prefix) || range.contains(',') then Left("unsupported range")
    else
      range.drop(prefix.length).split("-", -1).toList match
        case startText :: endText :: Nil if startText.isEmpty =>
          endText.toLongOption
            .filter(_ > 0)
            .map(suffix => math.max(0L, size - suffix) -> (size - 1))
            .filter((start, end) => size > 0 && start <= end)
            .toRight("invalid suffix range")
        case startText :: endText :: Nil =>
          for
            start <- startText.toLongOption.filter(_ >= 0).toRight("invalid range start")
            requestedEnd <-
              if endText.isEmpty then Right(size - 1)
              else endText.toLongOption.filter(_ >= 0).toRight("invalid range end")
            end = math.min(requestedEnd, size - 1)
            result <- Either.cond(start < size && start <= end, start -> end, "unsatisfiable range")
          yield result
        case _ => Left("invalid range")

  private def safeResourceName(name: String): Boolean =
    name.nonEmpty && name
      .split("/", -1)
      .forall(segment => segment.nonEmpty && segment != "." && segment != ".." && !segment.contains('\\'))

  private def hasSession(request: Request[IO]): Boolean =
    header(request, ci"Cookie").exists:
      _.split(';').exists(_.trim == s"$CookieName=$sessionToken")

  private def hasAllowedOrigin(request: Request[IO]): Boolean =
    header(request, ci"Origin").exists(allowedOrigins.contains)

  private def header(request: Request[IO], name: org.typelevel.ci.CIString): Option[String] =
    request.headers.headers.find(_.name == name).map(_.value)

  private def errorMessage(error: Throwable): String =
    Option(error.getMessage)
      .map(_.trim.replaceAll("\\s+", " "))
      .filter(_.nonEmpty)
      .getOrElse(error.getClass.getSimpleName)
      .take(500)

  private def randomOwnerId: IO[String] = IO.blocking:
    val bytes = Array.ofDim[Byte](32)
    SecureRandom().nextBytes(bytes)
    HexFormat.of().formatHex(bytes)
