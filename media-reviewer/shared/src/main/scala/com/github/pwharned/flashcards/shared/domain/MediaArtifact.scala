package com.github.pwharned.flashcards.shared.domain

import com.github.plokhotnyuk.jsoniter_scala.core.JsonValueCodec
import com.github.plokhotnyuk.jsoniter_scala.macros.{CodecMakerConfig, JsonCodecMaker}

final case class ArtifactTextSpan(start_char: Int, end_char: Int)

final case class MediaSource(
    id: String,
    path: String,
    language: String,
    title: Option[String],
    source_url: Option[String],
    checksum_sha256: Option[String],
    duration_ms: Option[Double]
)

final case class BaseToken(
    text: String,
    span: ArtifactTextSpan,
    start_ms: Option[Double],
    end_ms: Option[Double],
    confidence: Option[Double]
)

final case class Utterance(
    id: String,
    media_id: String,
    text: String,
    start_ms: Double,
    end_ms: Double,
    tokens: List[BaseToken],
    translation: Option[String],
    confidence: Option[Double],
    provenance: List[String]
)

final case class MediaArtifact(
    artifact_type: String,
    schema_version: Int,
    id: String,
    title: String,
    language: String,
    media: List[MediaSource],
    utterances: List[Utterance]
): 
  def validate: Either[String, MediaArtifact] =
    val mediaIds = media.map(_.id)
    val utteranceIds = utterances.map(_.id)
    Either.cond(artifact_type == MediaArtifact.ArtifactType, (), "unsupported artifact type")
      .flatMap(_ =>
        Either.cond(
          schema_version == MediaArtifact.SchemaVersion,
          (),
          s"unsupported media artifact schema version $schema_version"
        )
      )
      .flatMap(_ => MediaArtifact.nonEmpty(id, "artifact id"))
      .flatMap(_ => MediaArtifact.nonEmpty(title, "artifact title"))
      .flatMap(_ => MediaArtifact.nonEmpty(language, "artifact language"))
      .flatMap(_ => Either.cond(media.nonEmpty, (), "artifact must contain media"))
      .flatMap(_ => MediaArtifact.validateAll(media)(MediaArtifact.validateMedia))
      .flatMap(_ => Either.cond(mediaIds.distinct.size == mediaIds.size, (), "duplicate media id"))
      .flatMap(_ =>
        Either.cond(
          utteranceIds.distinct.size == utteranceIds.size,
          (),
          "duplicate utterance id"
        )
      )
      .flatMap(_ => MediaArtifact.validateAll(utterances)(MediaArtifact.validateUtterance))
      .flatMap(_ =>
        utterances.find(u => !mediaIds.contains(u.media_id)) match
          case Some(value) => Left(s"utterance ${value.id} references unknown media ${value.media_id}")
          case None        => Right(())
      )
      .flatMap(_ =>
        utterances.find: utterance =>
          media.find(_.id == utterance.media_id).flatMap(_.duration_ms).exists(utterance.end_ms > _)
        match
          case Some(value) => Left(s"utterance ${value.id} exceeds its media duration")
          case None        => Right(())
      )
      .map(_ => this)

object MediaArtifact:
  val ArtifactType = "media"
  val SchemaVersion = 1
  given JsonValueCodec[MediaArtifact] =
    JsonCodecMaker.make(
      CodecMakerConfig
        .withSkipUnexpectedFields(false)
        .withTransientDefault(false)
        .withTransientEmpty(false)
        .withTransientNone(false)
        .withRequireCollectionFields(true)
        .withRequireDefaultFields(true)
        .withSkipNestedOptionValues(false)
    )

  private def validateMedia(source: MediaSource): Either[String, Unit] =
    for
      _ <- nonEmpty(source.id, "media id")
      _ <- nonEmpty(source.path, s"media ${source.id} path")
      _ <- nonEmpty(source.language, s"media ${source.id} language")
      _ <- validateOptionalString(source.title, s"media ${source.id} title")
      _ <- validateOptionalString(source.source_url, s"media ${source.id} source URL")
      _ <- source.checksum_sha256 match
        case Some(value) =>
          Either.cond(
            value.matches("(?i)[0-9a-f]{64}"),
            (),
            s"media ${source.id} checksum must contain 64 hexadecimal characters"
          )
        case None => Right(())
      _ <- source.duration_ms match
        case Some(value) =>
          Either.cond(
            finite(value) && value > 0,
            (),
            s"media ${source.id} duration must be positive and finite"
          )
        case None => Right(())
    yield ()

  private def validateUtterance(utterance: Utterance): Either[String, Unit] =
    for
      _ <- nonEmpty(utterance.id, "utterance id")
      _ <- nonEmpty(utterance.media_id, s"utterance ${utterance.id} media id")
      _ <- nonEmpty(utterance.text, s"utterance ${utterance.id} text")
      _ <- Either.cond(
        finite(utterance.start_ms) && finite(utterance.end_ms) &&
          utterance.start_ms >= 0 && utterance.end_ms > utterance.start_ms,
        (),
        s"utterance ${utterance.id} has invalid timing"
      )
      _ <- validateOptionalString(utterance.translation, s"utterance ${utterance.id} translation")
      _ <- validateConfidence(utterance.confidence, s"utterance ${utterance.id} confidence")
      _ <- Either.cond(
        utterance.provenance.forall(_.trim.nonEmpty),
        (),
        s"utterance ${utterance.id} provenance must not contain empty values"
      )
      _ <- validateTokens(utterance)
    yield ()

  private def validateTokens(utterance: Utterance): Either[String, Unit] =
    var previousEnd = 0
    var previousTimedStart = Double.NegativeInfinity
    var previousTimedEnd = Double.NegativeInfinity
    utterance.tokens.zipWithIndex.foldLeft[Either[String, Unit]](Right(())):
      case (failure @ Left(_), _) => failure
      case (Right(_), (token, index)) =>
        val prefix = s"utterance ${utterance.id} token $index"
        for
          _ <- nonEmpty(token.text, s"$prefix text")
          bounds <- spanUtf16Bounds(utterance.text, token.span, prefix)
          (startUtf16, endUtf16) = bounds
          _ <- Either.cond(
            token.span.start_char >= previousEnd,
            (),
            s"$prefix span is not ordered"
          )
          _ <- Either.cond(
            utterance.text.substring(startUtf16, endUtf16) == token.text,
            (),
            s"$prefix text does not match its source span"
          )
          _ <- validateConfidence(token.confidence, s"$prefix confidence")
          _ <- (token.start_ms, token.end_ms) match
            case (None, None) => Right(())
            case (Some(start), Some(end)) =>
              val valid = finite(start) && finite(end) && end > start &&
                start >= utterance.start_ms && end <= utterance.end_ms &&
                start >= previousTimedStart && end >= previousTimedEnd
              if valid then
                previousTimedStart = start
                previousTimedEnd = end
                Right(())
              else Left(s"$prefix has invalid or unordered timing")
            case _ => Left(s"$prefix must provide both start_ms and end_ms")
        yield
          previousEnd = token.span.end_char
          ()

  private def spanUtf16Bounds(
      text: String,
      span: ArtifactTextSpan,
      prefix: String
  ): Either[String, (Int, Int)] =
    for
      _ <- Either.cond(
        span.start_char >= 0 && span.end_char > span.start_char,
        (),
        s"$prefix has an invalid code-point span"
      )
      start <- utf16IndexAtCodePoint(text, span.start_char)
        .toRight(s"$prefix span starts outside the source text")
      end <- utf16IndexAtCodePoint(text, span.end_char)
        .toRight(s"$prefix span ends outside the source text")
    yield start -> end

  private def utf16IndexAtCodePoint(text: String, requested: Int): Option[Int] =
    var codePoints = 0
    var utf16 = 0
    while codePoints < requested && utf16 < text.length do
      val width =
        if Character.isHighSurrogate(text.charAt(utf16)) &&
            utf16 + 1 < text.length && Character.isLowSurrogate(text.charAt(utf16 + 1))
        then 2
        else 1
      utf16 += width
      codePoints += 1
    Option.when(codePoints == requested)(utf16)

  private def validateConfidence(value: Option[Double], name: String): Either[String, Unit] =
    value match
      case Some(number) =>
        Either.cond(finite(number) && number >= 0 && number <= 1, (), s"$name must be between 0 and 1")
      case None => Right(())

  private def validateOptionalString(value: Option[String], name: String): Either[String, Unit] =
    value.fold[Either[String, Unit]](Right(()))(nonEmpty(_, name))

  private def nonEmpty(value: String, name: String): Either[String, Unit] =
    Either.cond(value.trim.nonEmpty, (), s"$name must not be empty")

  private def finite(value: Double): Boolean = java.lang.Double.isFinite(value)

  private def validateAll[A](values: List[A])(validate: A => Either[String, Unit]): Either[String, Unit] =
    values.foldLeft[Either[String, Unit]](Right(())):
      case (failure @ Left(_), _) => failure
      case (Right(_), value)      => validate(value)
