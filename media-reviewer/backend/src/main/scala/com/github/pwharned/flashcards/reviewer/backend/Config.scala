package com.github.pwharned.flashcards.reviewer.backend

import com.comcast.ip4s.{Host, Port}

import java.net.URI
import java.nio.file.{InvalidPathException, Path}

final case class Config(
    artifactPath: Path,
    host: Host,
    port: Port,
    ankiUrl: URI,
    deckName: String,
    ankiModelName: String
)

object Config:
  val usage: String =
    """Usage: media-reviewer [options] ARTIFACT
      |
      |Options:
      |  --host HOST                 Bind host (default: 127.0.0.1)
      |  --port PORT                 Bind port (default: 8766)
      |  --anki-url URL              AnkiConnect URL (default: http://127.0.0.1:8765)
      |  --deck NAME                 Anki deck (default: Default)
      |  --anki-model NAME           Anki note type (default: Cloze)
      |  -h, --help                  Show this help
      |""".stripMargin

  private val optionNames = Set(
    "--host",
    "--port",
    "--anki-url",
    "--deck",
    "--anki-model"
  )

  def parse(args: List[String]): Either[String, Option[Config]] =
    if args.contains("--help") || args.contains("-h") then Right(None)
    else
      for
        collected <- collect(args, None, Map.empty)
        (artifact, options) = collected
        hostText <- loopbackHost(options.getOrElse("--host", "127.0.0.1"), "--host")
        host <- Host.fromString(hostText).toRight(s"invalid --host: $hostText")
        port <- parsePort(options.getOrElse("--port", "8766"))
        ankiUrl <- parseLoopbackHttpUri(
          options.getOrElse("--anki-url", "http://127.0.0.1:8765"),
          "--anki-url"
        )
        deckName <- nonEmpty(
          options.getOrElse("--deck", "Default"),
          "--deck"
        )
        ankiModelName <- nonEmpty(
          options.getOrElse("--anki-model", "Cloze"),
          "--anki-model"
        )
        artifactPath <- parsePath(artifact)
      yield Some(
        Config(
          artifactPath,
          host,
          port,
          ankiUrl,
          deckName,
          ankiModelName
        )
      )

  private def collect(
      remaining: List[String],
      artifact: Option[String],
      options: Map[String, String]
  ): Either[String, (String, Map[String, String])] =
    remaining match
      case Nil => artifact.toRight("missing ARTIFACT positional path").map(_ -> options)
      case "--" :: tail =>
        tail match
          case value :: Nil if artifact.isEmpty => Right(value -> options)
          case Nil                              => Left("missing ARTIFACT positional path")
          case _                                => Left("expected exactly one ARTIFACT positional path")
      case raw :: tail if raw.startsWith("--") =>
        val equalsAt = raw.indexOf('=')
        val name = if equalsAt < 0 then raw else raw.substring(0, equalsAt)
        if !optionNames.contains(name) then Left(s"unknown option: $name")
        else if options.contains(name) then Left(s"option supplied more than once: $name")
        else if equalsAt >= 0 then
          val value = raw.substring(equalsAt + 1)
          if value.isEmpty then Left(s"$name requires a value")
          else collect(tail, artifact, options.updated(name, value))
        else
          tail match
            case value :: rest if !value.startsWith("--") =>
              collect(rest, artifact, options.updated(name, value))
            case _ => Left(s"$name requires a value")
      case raw :: _ if raw.startsWith("-") => Left(s"unknown option: $raw")
      case value :: tail =>
        artifact match
          case Some(_) => Left("expected exactly one ARTIFACT positional path")
          case None    => collect(tail, Some(value), options)

  private def nonEmpty(value: String, name: String): Either[String, String] =
    val trimmed = value.trim
    Either.cond(trimmed.nonEmpty, trimmed, s"$name must not be empty")

  private def loopbackHost(value: String, name: String): Either[String, String] =
    nonEmpty(value, name).flatMap: host =>
      val normalized = host.stripPrefix("[").stripSuffix("]").toLowerCase
      Either.cond(
        Set("127.0.0.1", "localhost", "::1").contains(normalized),
        host,
        s"$name must be 127.0.0.1, localhost, or ::1"
      )

  private def parsePort(value: String): Either[String, Port] =
    value.toIntOption
      .flatMap(Port.fromInt)
      .toRight(s"--port must be an integer between 1 and 65535: $value")

  private def parseHttpUri(value: String, name: String): Either[String, URI] =
    try
      val uri = URI.create(value.trim).normalize()
      val validScheme = Option(uri.getScheme).exists(s => s == "http" || s == "https")
      Either.cond(
        uri.isAbsolute && validScheme && Option(uri.getHost).exists(_.nonEmpty),
        uri,
        s"$name must be an absolute HTTP(S) URL"
      )
    catch case _: IllegalArgumentException => Left(s"invalid $name: $value")

  private def parseLoopbackHttpUri(value: String, name: String): Either[String, URI] =
    parseHttpUri(value, name).flatMap: uri =>
      val host = Option(uri.getHost).getOrElse("").stripPrefix("[").stripSuffix("]").toLowerCase
      val loopback = Set("localhost", "127.0.0.1", "::1").contains(host)
      Either.cond(loopback, uri, s"$name must use a loopback host")

  private def parsePath(value: String): Either[String, Path] =
    try Right(Path.of(value))
    catch case _: InvalidPathException => Left(s"invalid ARTIFACT path: $value")
