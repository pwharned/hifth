package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import com.github.plokhotnyuk.jsoniter_scala.core.readFromArray
import com.github.pwharned.flashcards.shared.domain.{MediaArtifact, MediaSource, Utterance}

import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.{Files, LinkOption, Path}
import java.security.MessageDigest
import java.util.HexFormat

final case class ResolvedMedia(
    source: MediaSource,
    path: Path,
    size: Long,
    modifiedMillis: Long
):
  def verifyMetadata(): Unit =
    val attributes = Files.readAttributes(path, classOf[BasicFileAttributes], LinkOption.NOFOLLOW_LINKS)
    if !attributes.isRegularFile ||
        attributes.size != size ||
        attributes.lastModifiedTime.toMillis != modifiedMillis
    then throw new IllegalStateException(s"media ${source.id} changed after the artifact was loaded")

  def verifyChecksum(): Unit =
    verifyMetadata()
    source.checksum_sha256.foreach: expected =>
      val actual = LoadedArtifact.sha256(path)
      verifyMetadata()
      if !actual.equalsIgnoreCase(expected) then
        throw new IllegalStateException(s"media ${source.id} checksum no longer matches the artifact")

final case class LoadedArtifact(
    artifact: MediaArtifact,
    mediaById: Map[String, ResolvedMedia],
    utteranceById: Map[String, Utterance]
)

object LoadedArtifact:
  def load(input: Path): IO[LoadedArtifact] = IO.blocking:
    val artifactPath = input.toAbsolutePath.normalize()
    if !Files.isRegularFile(artifactPath) then
      throw new IllegalArgumentException(s"artifact is not a readable file: $artifactPath")

    val artifact =
      try readFromArray[MediaArtifact](Files.readAllBytes(artifactPath))
      catch
        case error: Throwable =>
          throw new IllegalArgumentException(
            s"could not decode media artifact $artifactPath: ${message(error)}",
            error
          )

    val validated = artifact.validate.fold(
      error => throw new IllegalArgumentException(s"invalid media artifact: $error"),
      identity
    )
    validateAdditionalFields(validated)

    val baseDirectory = artifactPath.toRealPath().getParent
    val resolvedMedia = validated.media.map: source =>
      val configuredPath = Path.of(source.path)
      val candidate =
        if configuredPath.isAbsolute then configuredPath.normalize()
        else baseDirectory.resolve(configuredPath).normalize()
      if !Files.isRegularFile(candidate) then
        throw new IllegalArgumentException(
          s"media ${source.id} is not a readable file: $candidate"
        )
      val realPath = candidate.toRealPath()
      val before = Files.readAttributes(realPath, classOf[BasicFileAttributes], LinkOption.NOFOLLOW_LINKS)
      source.checksum_sha256.foreach: expected =>
        val actual = sha256(realPath)
        if !actual.equalsIgnoreCase(expected) then
          throw new IllegalArgumentException(
            s"media ${source.id} checksum does not match the artifact"
          )
      val after = Files.readAttributes(realPath, classOf[BasicFileAttributes], LinkOption.NOFOLLOW_LINKS)
      if before.size != after.size || before.lastModifiedTime != after.lastModifiedTime then
        throw new IllegalArgumentException(s"media ${source.id} changed while it was being verified")
      source.id -> ResolvedMedia(source, realPath, after.size, after.lastModifiedTime.toMillis)

    LoadedArtifact(
      validated,
      resolvedMedia.toMap,
      validated.utterances.map(utterance => utterance.id -> utterance).toMap
    )

  private def validateAdditionalFields(artifact: MediaArtifact): Unit =
    artifact.media.find(source => source.id.trim.isEmpty || source.path.trim.isEmpty) match
      case Some(_) => throw new IllegalArgumentException("media ids and paths must not be empty")
      case None    => ()

    artifact.media.find(_.language.trim.isEmpty) match
      case Some(source) =>
        throw new IllegalArgumentException(s"media ${source.id} language must not be empty")
      case None => ()

    artifact.media.foreach: source =>
      source.duration_ms.foreach: duration =>
        if !java.lang.Double.isFinite(duration) || duration <= 0 then
          throw new IllegalArgumentException(s"media ${source.id} has invalid duration")

    artifact.utterances.foreach: utterance =>
      if utterance.id.trim.isEmpty then
        throw new IllegalArgumentException("utterance ids must not be empty")
      if !java.lang.Double.isFinite(utterance.start_ms) ||
          !java.lang.Double.isFinite(utterance.end_ms)
      then throw new IllegalArgumentException(s"utterance ${utterance.id} has invalid timing")

      val media = artifact.media.find(_.id == utterance.media_id).get
      media.duration_ms.foreach: duration =>
        if utterance.end_ms > duration then
          throw new IllegalArgumentException(
            s"utterance ${utterance.id} ends after media ${media.id}"
          )

  private def message(error: Throwable): String =
    Option(error.getMessage).filter(_.trim.nonEmpty).getOrElse(error.getClass.getSimpleName)

  private[backend] def sha256(path: Path): String =
    val digest = MessageDigest.getInstance("SHA-256")
    val input = Files.newInputStream(path)
    try
      val buffer = Array.ofDim[Byte](1024 * 1024)
      var count = input.read(buffer)
      while count >= 0 do
        if count > 0 then digest.update(buffer, 0, count)
        count = input.read(buffer)
    finally input.close()
    HexFormat.of().formatHex(digest.digest())
