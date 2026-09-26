package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.unsafe.implicits.global
import com.github.plokhotnyuk.jsoniter_scala.core.writeToArray
import com.github.pwharned.flashcards.shared.domain.{MediaArtifact, MediaSource, Utterance}

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path}
import scala.jdk.CollectionConverters.*

class LoadedArtifactSuite extends munit.FunSuite:
  test("load resolves media paths relative to the artifact"):
    withTempDirectory: directory =>
      val mediaDirectory = Files.createDirectory(directory.resolve("media"))
      val mediaPath = mediaDirectory.resolve("lesson.bin")
      Files.write(mediaPath, "audio bytes".getBytes(StandardCharsets.UTF_8))

      val artifact = testArtifact("media/lesson.bin", checksum = None)
      val artifactPath = directory.resolve("artifact.json")
      Files.write(artifactPath, writeToArray(artifact))

      val loaded = LoadedArtifact.load(artifactPath).unsafeRunSync()

      assertEquals(loaded.artifact, artifact)
      assertEquals(loaded.mediaById("media-1").path, mediaPath.toRealPath())
      assertEquals(loaded.mediaById("media-1").source.path, "media/lesson.bin")
      assertEquals(loaded.utteranceById("utterance-1"), artifact.utterances.head)

  test("load rejects media whose SHA-256 checksum does not match"):
    withTempDirectory: directory =>
      val mediaPath = directory.resolve("lesson.bin")
      Files.write(mediaPath, "different bytes".getBytes(StandardCharsets.UTF_8))

      val artifact = testArtifact("lesson.bin", checksum = Some("0" * 64))
      val artifactPath = directory.resolve("artifact.json")
      Files.write(artifactPath, writeToArray(artifact))

      val error = intercept[IllegalArgumentException]:
        LoadedArtifact.load(artifactPath).unsafeRunSync()

      assertEquals(error.getMessage, "media media-1 checksum does not match the artifact")

  private def testArtifact(mediaPath: String, checksum: Option[String]): MediaArtifact =
    MediaArtifact(
      artifact_type = MediaArtifact.ArtifactType,
      schema_version = MediaArtifact.SchemaVersion,
      id = "artifact-1",
      title = "Test artifact",
      language = "en",
      media = List(
        MediaSource(
          id = "media-1",
          path = mediaPath,
          language = "en",
          title = None,
          source_url = None,
          checksum_sha256 = checksum,
          duration_ms = Some(2000.0)
        )
      ),
      utterances = List(
        Utterance(
          id = "utterance-1",
          media_id = "media-1",
          text = "Test sentence",
          start_ms = 100.0,
          end_ms = 1000.0,
          tokens = Nil,
          translation = None,
          confidence = None,
          provenance = List("test")
        )
      )
    )

  private def withTempDirectory[A](test: Path => A): A =
    val directory = Files.createTempDirectory("media-reviewer-artifact-test-")
    try test(directory)
    finally deleteTree(directory)

  private def deleteTree(root: Path): Unit =
    if Files.exists(root) then
      val paths = Files.walk(root)
      try
        paths.iterator().asScala.toList.reverse.foreach: path =>
          Files.deleteIfExists(path)
          ()
      finally paths.close()
