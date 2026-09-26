package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.effect.std.Semaphore
import cats.effect.unsafe.implicits.global
import com.github.plokhotnyuk.jsoniter_scala.core.writeToArray
import com.github.pwharned.flashcards.shared.domain.{
  Enrichment,
  MediaArtifact,
  MediaSource,
  SelectionSpan,
  Utterance
}
import com.comcast.ip4s.Port

import java.io.{BufferedReader, InputStreamReader, OutputStreamWriter}
import java.net.{ServerSocket, URI}
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.nio.file.{Files, Path}
import scala.jdk.CollectionConverters.*

class ServerSecuritySuite extends munit.FunSuite:
  test("media requires a session and supports byte ranges"):
    withFixture: (config, loaded, mediaId) =>
      val ownerId = "server-security-owner"
      val preparedBytes = Array.tabulate[Byte](24)(index => (index + 64).toByte)
      val program =
        for
          gate <- Semaphore[IO](1)
          preparations <- EphemeralAudioStore.create
          generation <- preparations.begin(ownerId)
          prepared <- preparations
            .complete(
              ownerId,
              generation,
              "utterance",
              SelectionSpan(0, 4),
              Enrichment("meaning", "test"),
              "en",
              "prepared.mp3",
              preparedBytes
            )
            .flatMap:
              case Some(value) => IO.pure(value)
              case None =>
                IO.raiseError(new AssertionError("prepared audio was not stored"))
          server = Server(
            config,
            loaded,
            fakeGoogle,
            fakeAnki,
            preparations,
            gate,
            "test-token"
          )
          _ <- server.resource.use: _ =>
            val client = HttpClient.newHttpClient()
            val base = s"http://127.0.0.1:${config.port.value}"
            val preparedUri = URI.create(s"$base/prepared-audio/${prepared.audio.audioId}")
            for
              cookie <- IO.blocking:
                val root = send(
                  client,
                  HttpRequest.newBuilder(URI.create(s"$base/")).GET().build()
                )
                assertEquals(root.statusCode(), 200)
                val cookie = root.headers().firstValue("Set-Cookie").orElseThrow().split(';').head

                val forbidden = send(
                  client,
                  HttpRequest.newBuilder(URI.create(s"$base/media/$mediaId")).GET().build()
                )
                assertEquals(forbidden.statusCode(), 403)

                val ranged = send(
                  client,
                  HttpRequest
                    .newBuilder(URI.create(s"$base/media/$mediaId"))
                    .header("Cookie", cookie)
                    .header("Range", "bytes=4-11")
                    .GET()
                    .build()
                )
                assertEquals(ranged.statusCode(), 206)
                assertEquals(ranged.body().toList, (4 until 12).map(_.toByte).toList)
                assertEquals(
                  ranged.headers().firstValue("Content-Range").orElseThrow(),
                  "bytes 4-11/64"
                )

                val preparedForbidden = send(
                  client,
                  HttpRequest.newBuilder(preparedUri).GET().build()
                )
                assertEquals(preparedForbidden.statusCode(), 403)

                val preparedFull = send(
                  client,
                  HttpRequest
                    .newBuilder(preparedUri)
                    .header("Cookie", cookie)
                    .GET()
                    .build()
                )
                assertEquals(preparedFull.statusCode(), 200)
                assertEquals(preparedFull.body().toList, preparedBytes.toList)
                assertPreparedHeaders(preparedFull, prepared.audio.sha256)

                val preparedRange = send(
                  client,
                  HttpRequest
                    .newBuilder(preparedUri)
                    .header("Cookie", cookie)
                    .header("Range", "bytes=3-9")
                    .GET()
                    .build()
                )
                assertEquals(preparedRange.statusCode(), 206)
                assertEquals(preparedRange.body().toList, preparedBytes.slice(3, 10).toList)
                assertPreparedHeaders(preparedRange, prepared.audio.sha256)
                assertEquals(
                  preparedRange.headers().firstValue("Content-Range").orElseThrow(),
                  s"bytes 3-9/${preparedBytes.length}"
                )

                val hostileOrigin = send(
                  client,
                  HttpRequest
                    .newBuilder(URI.create(s"$base/ws"))
                    .header("Cookie", cookie)
                    .header("Origin", "https://attacker.example")
                    .GET()
                    .build()
                )
                assertEquals(hostileOrigin.statusCode(), 403)

                val socket = java.net.Socket("127.0.0.1", config.port.value)
                try
                  val output = OutputStreamWriter(
                    socket.getOutputStream,
                    java.nio.charset.StandardCharsets.US_ASCII
                  )
                  output.write(
                    s"GET /ws HTTP/1.1\r\n" +
                      s"Host: 127.0.0.1:${config.port.value}\r\n" +
                      "Connection: Upgrade\r\n" +
                      "Upgrade: websocket\r\n" +
                      "Sec-WebSocket-Version: 13\r\n" +
                      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
                      "Origin: http://127.0.0.1:" + config.port.value + "\r\n" +
                      s"Cookie: $cookie\r\n\r\n"
                  )
                  output.flush()
                  val input = BufferedReader(
                    InputStreamReader(
                      socket.getInputStream,
                      java.nio.charset.StandardCharsets.US_ASCII
                    )
                  )
                  assert(input.readLine().contains("101"))
                finally socket.close()
                cookie
              _ <- preparations.clear(ownerId)
              _ <- IO.blocking:
                val missing = send(
                  client,
                  HttpRequest
                    .newBuilder(preparedUri)
                    .header("Cookie", cookie)
                    .GET()
                    .build()
                )
                assertEquals(missing.statusCode(), 404)
            yield ()
        yield ()
      program.unsafeRunSync()

  private val fakeGoogle: GoogleGateway = new GoogleGateway:
    override def prepare(
        sentence: String,
        selection: SelectionSpan,
        artifactLanguage: String
    ): IO[GooglePreparation] =
      unexpected("GoogleGateway.prepare")

  private val fakeAnki: AnkiGateway = new AnkiGateway:
    override def deckNames: IO[List[String]] =
      unexpected("AnkiGateway.deckNames")

    override def validateDestination(deckName: String): IO[Unit] =
      unexpected("AnkiGateway.validateDestination")

    override def storeMediaFile(filename: String, bytes: Array[Byte]): IO[String] =
      unexpected("AnkiGateway.storeMediaFile")

    override def addNote(
        deckName: String,
        fields: Map[String, String],
        tags: List[String]
    ): IO[Either[String, Long]] =
      unexpected("AnkiGateway.addNote")

  private def unexpected[A](operation: String): IO[A] =
    IO.raiseError(new AssertionError(s"unexpected service call: $operation"))

  private def assertPreparedHeaders(
      response: HttpResponse[Array[Byte]],
      sha256: String
  ): Unit =
    assertEquals(response.headers().firstValue("Content-Type").orElseThrow(), "audio/mpeg")
    assertEquals(
      response.headers().firstValue("Cache-Control").orElseThrow(),
      "private, no-store"
    )
    assertEquals(
      response.headers().firstValue("X-Content-Type-Options").orElseThrow(),
      "nosniff"
    )
    assertEquals(response.headers().firstValue("ETag").orElseThrow(), s"\"$sha256\"")

  private def send(
      client: HttpClient,
      request: HttpRequest
  ): HttpResponse[Array[Byte]] =
    client.send(request, HttpResponse.BodyHandlers.ofByteArray())

  private def withFixture(test: (Config, LoadedArtifact, String) => Unit): Unit =
    val directory = Files.createTempDirectory("media-reviewer-server-test-")
    try
      val mediaId = "media-server-test"
      val mediaPath = directory.resolve("media.bin")
      Files.write(mediaPath, Array.tabulate[Byte](64)(_.toByte))
      val artifact = MediaArtifact(
        MediaArtifact.ArtifactType,
        MediaArtifact.SchemaVersion,
        "artifact-server-test",
        "Server test",
        "en",
        List(MediaSource(mediaId, "media.bin", "en", None, None, None, Some(1000))),
        List(Utterance("utterance", mediaId, "test", 0, 500, Nil, None, None, List("test")))
      )
      val artifactPath = directory.resolve("artifact.json")
      Files.write(artifactPath, writeToArray(artifact))
      val port = freePort()
      val config = Config.parse(List(artifactPath.toString)) match
        case Right(Some(value)) => value.copy(port = Port.fromInt(port).get)
        case value              => fail(s"could not create config: $value")
      test(config, LoadedArtifact.load(artifactPath).unsafeRunSync(), mediaId)
    finally deleteTree(directory)

  private def freePort(): Int =
    val socket = ServerSocket(0)
    try socket.getLocalPort finally socket.close()

  private def deleteTree(root: Path): Unit =
    if Files.exists(root) then
      val paths = Files.walk(root)
      try paths.iterator().asScala.toList.reverse.foreach(Files.deleteIfExists)
      finally paths.close()
