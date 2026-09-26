package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.{ExitCode, IO, IOApp}
import cats.effect.std.Semaphore

import java.security.SecureRandom
import java.util.HexFormat

object Main extends IOApp:
  def run(args: List[String]): IO[ExitCode] =
    Config.parse(args) match
      case Left(error) =>
        stderr(s"$error\n\n${Config.usage}").as(ExitCode.Error)
      case Right(None) => IO.println(Config.usage).as(ExitCode.Success)
      case Right(Some(config)) =>
        LoadedArtifact
          .load(config.artifactPath)
          .flatMap: source =>
            val google = GoogleClient.live
            val anki = ExternalClients(config)
            for
              workGate <- Semaphore[IO](4)
              preparations <- EphemeralAudioStore.create
              server = Server(
                config,
                source,
                google,
                anki,
                preparations,
                workGate,
                sessionToken()
              )
              _ <- IO.println(
                s"Reviewing '${source.artifact.title}' at http://${config.host}:${config.port.value}"
              )
              _ <- server.resource.use(_ => IO.never)
            yield ()
          .as(ExitCode.Success)
          .handleErrorWith: error =>
            stderr(s"media reviewer failed: ${errorMessage(error)}").as(ExitCode.Error)

  private def stderr(value: String): IO[Unit] = IO.delay(System.err.println(value))

  private def errorMessage(error: Throwable): String =
    Iterator
      .iterate(Option(error))(_.flatMap(value => Option(value.getCause)))
      .takeWhile(_.nonEmpty)
      .flatten
      .flatMap(value => Option(value.getMessage))
      .map(_.trim)
      .find(_.nonEmpty)
      .getOrElse(error.getClass.getSimpleName)

  private def sessionToken(): String =
    val bytes = Array.ofDim[Byte](32)
    SecureRandom().nextBytes(bytes)
    HexFormat.of().formatHex(bytes)
