package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.effect.unsafe.implicits.global
import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}

import java.security.MessageDigest
import java.util.HexFormat
import java.util.concurrent.atomic.AtomicLong
import scala.concurrent.duration.*

class EphemeralAudioStoreSuite extends munit.FunSuite:
  private val selection = SelectionSpan(2, 6)
  private val enrichment = Enrichment("meaning", "the sentence")
  private val audioBytes = Array[Byte](73, 68, 51, 4, 0, 1, 2, 3)

  test("owners cannot use each other's preparation"):
    val harness = Harness()
    val ownerAGeneration = harness.store.begin("owner-a").unsafeRunSync()
    harness.store.begin("owner-b").unsafeRunSync()
    val prepared = complete(harness, "owner-a", ownerAGeneration)

    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)
    assert(
      harness.store
        .forCreate("owner-b", prepared.audio.audioId, "utterance-1", selection)
        .unsafeRunSync()
        .isLeft
    )
    assert(
      harness.store
        .forCreate("owner-a", prepared.audio.audioId, "utterance-1", selection)
        .unsafeRunSync()
        .isRight
    )

  test("a new begin evicts the owner's previous preparation"):
    val harness = Harness()
    val firstGeneration = harness.store.begin("owner").unsafeRunSync()
    val first = complete(harness, "owner", firstGeneration)

    val secondGeneration = harness.store.begin("owner").unsafeRunSync()
    assert(secondGeneration > firstGeneration)
    assert(harness.store.find(first.audio.audioId).unsafeRunSync().isEmpty)

    val second = complete(harness, "owner", secondGeneration, filename = "second.mp3")
    assert(harness.store.find(first.audio.audioId).unsafeRunSync().isEmpty)
    assertEquals(
      harness.store.find(second.audio.audioId).unsafeRunSync().map(_.audio.filename),
      Some("second.mp3")
    )

  test("superseded and disconnected completions cannot recreate state"):
    val harness = Harness()
    val staleGeneration = harness.store.begin("owner").unsafeRunSync()
    val currentGeneration = harness.store.begin("owner").unsafeRunSync()

    assert(
      harness.store
        .complete(
          "owner",
          staleGeneration,
          "utterance-1",
          selection,
          enrichment,
          "en",
          "stale.mp3",
          audioBytes
        )
        .unsafeRunSync()
        .isEmpty
    )

    val current = complete(harness, "owner", currentGeneration)
    harness.store.clear("owner").unsafeRunSync()
    assert(harness.store.find(current.audio.audioId).unsafeRunSync().isEmpty)
    assert(
      harness.store
        .complete(
          "owner",
          currentGeneration,
          "utterance-1",
          selection,
          enrichment,
          "en",
          "after-clear.mp3",
          audioBytes
        )
        .unsafeRunSync()
        .isEmpty
    )

  test("card creation requires the exact utterance and selection binding"):
    val harness = Harness()
    val generation = harness.store.begin("owner").unsafeRunSync()
    val prepared = complete(harness, "owner", generation)

    assert(
      harness.store
        .forCreate("owner", prepared.audio.audioId, "other-utterance", selection)
        .unsafeRunSync()
        .isLeft
    )
    assert(
      harness.store
        .forCreate("owner", prepared.audio.audioId, "utterance-1", SelectionSpan(2, 5))
        .unsafeRunSync()
        .isLeft
    )
    val accepted = harness.store
      .forCreate("owner", prepared.audio.audioId, "utterance-1", selection)
      .unsafeRunSync()
    assertEquals(accepted.toOption.map(_.binding), Some(prepared.binding))

  test("completion creates the expected SHA-256 and immutable metadata"):
    val harness = Harness()
    val generation = harness.store.begin("owner").unsafeRunSync()
    val prepared = complete(harness, "owner", generation)

    assertEquals(prepared.audio.audioId, "test-audio-1")
    assertEquals(prepared.audio.previewUrl, "/prepared-audio/test-audio-1")
    assertEquals(prepared.audio.filename, "clip.mp3")
    assertEquals(prepared.audio.contentType, "audio/mpeg")
    assertEquals(prepared.audio.sizeBytes, audioBytes.length.toLong)
    assertEquals(prepared.audio.sha256, sha256(audioBytes))
    assertEquals(prepared.descriptor, prepared.audio)
    assertEquals(prepared.sourceLanguage, "en")
    assertEquals(prepared.utteranceId, "utterance-1")
    assertEquals(prepared.selection, selection)
    assertEquals(prepared.enrichment, enrichment)
    assertEquals(prepared.audio.validate, Right(prepared.audio))

  test("stored bytes are defensive against input and result mutation"):
    val harness = Harness()
    val input = audioBytes.clone()
    val expected = input.toList
    val generation = harness.store.begin("owner").unsafeRunSync()
    val prepared = complete(harness, "owner", generation, bytes = input)

    input(0) = 0
    assertEquals(prepared.bytes.toList, expected)

    val exposed = prepared.bytes
    exposed(1) = 0
    assertEquals(prepared.bytes.toList, expected)
    assertEquals(
      harness.store.find(prepared.audio.audioId).unsafeRunSync().map(_.bytes.toList),
      Some(expected)
    )

  test("preparations expire after thirty minutes without access extending them"):
    val harness = Harness()
    val generation = harness.store.begin("owner").unsafeRunSync()
    val prepared = complete(harness, "owner", generation)

    harness.advance(29.minutes)
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)
    harness.advance(1.minute)
    assert(
      harness.store
        .forCreate("owner", prepared.audio.audioId, "utterance-1", selection)
        .unsafeRunSync()
        .isLeft
    )
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().isEmpty)

  test("pruneExpired sweeps expired preparations for every owner"):
    val harness = Harness()
    val firstGeneration = harness.store.begin("owner-a").unsafeRunSync()
    val first = complete(harness, "owner-a", firstGeneration)
    val secondGeneration = harness.store.begin("owner-b").unsafeRunSync()
    val second = complete(harness, "owner-b", secondGeneration)
    harness.advance(20.minutes)
    val freshGeneration = harness.store.begin("owner-c").unsafeRunSync()
    val fresh = complete(harness, "owner-c", freshGeneration)

    harness.advance(11.minutes)
    harness.store.pruneExpired.unsafeRunSync()

    assert(harness.store.find(first.audio.audioId).unsafeRunSync().isEmpty)
    assert(harness.store.find(second.audio.audioId).unsafeRunSync().isEmpty)
    assert(harness.store.find(fresh.audio.audioId).unsafeRunSync().nonEmpty)

  test("completion evicts the oldest preparation at the 32-entry limit"):
    val harness = Harness()
    val audioIds = (1 to 33).map: index =>
      val ownerId = s"owner-$index"
      val generation = harness.store.begin(ownerId).unsafeRunSync()
      val audioId = complete(harness, ownerId, generation).audio.audioId
      harness.advance(1.nanosecond)
      audioId

    assert(harness.store.find(audioIds.head).unsafeRunSync().isEmpty)
    assertEquals(
      audioIds.tail.count(id => harness.store.find(id).unsafeRunSync().nonEmpty),
      32
    )

  test("completion evicts oldest audio before exceeding the 100 MiB limit"):
    val harness = Harness()
    val tenMiB = new Array[Byte](10 * 1024 * 1024)
    val largeAudioIds = (1 to 10).map: index =>
      val ownerId = s"large-owner-$index"
      val generation = harness.store.begin(ownerId).unsafeRunSync()
      val audioId = complete(harness, ownerId, generation, bytes = tenMiB).audio.audioId
      harness.advance(1.nanosecond)
      audioId

    val newestGeneration = harness.store.begin("small-owner").unsafeRunSync()
    val newest = complete(harness, "small-owner", newestGeneration)

    assert(harness.store.find(largeAudioIds.head).unsafeRunSync().isEmpty)
    assert(largeAudioIds.tail.forall(id => harness.store.find(id).unsafeRunSync().nonEmpty))
    assert(harness.store.find(newest.audio.audioId).unsafeRunSync().nonEmpty)
    val retainedBytes = (largeAudioIds.tail :+ newest.audio.audioId).flatMap: audioId =>
      harness.store.find(audioId).unsafeRunSync().map(_.audio.sizeBytes)
    assertEquals(retainedBytes.sum, 9L * 10L * 1024L * 1024L + audioBytes.length)

  test("cleanup operations are conditional and idempotent"):
    val harness = Harness()
    harness.store.clear("missing-owner").unsafeRunSync()
    harness.store.clear("missing-owner").unsafeRunSync()

    val generation = harness.store.begin("owner").unsafeRunSync()
    val prepared = complete(harness, "owner", generation)
    harness.store.clearIfMatches("owner", "different-audio").unsafeRunSync()
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)

    harness.store.clearIfMatches("owner", prepared.audio.audioId).unsafeRunSync()
    harness.store.clearIfMatches("owner", prepared.audio.audioId).unsafeRunSync()
    harness.store.clear("owner").unsafeRunSync()
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().isEmpty)
    assert(
      harness.store
        .complete(
          "owner",
          generation,
          "utterance-1",
          selection,
          enrichment,
          "en",
          "late.mp3",
          audioBytes
        )
        .unsafeRunSync()
        .isEmpty
    )

  test("empty and oversized audio are rejected"):
    val harness = Harness()
    val generation = harness.store.begin("owner").unsafeRunSync()

    intercept[IllegalArgumentException]:
      harness.store
        .complete(
          "owner",
          generation,
          "utterance-1",
          selection,
          enrichment,
          "en",
          "empty.mp3",
          Array.emptyByteArray
        )
        .unsafeRunSync()

    intercept[IllegalArgumentException]:
      harness.store
        .complete(
          "owner",
          generation,
          "utterance-1",
          selection,
          enrichment,
          "en",
          "large.mp3",
          new Array[Byte](10 * 1024 * 1024 + 1)
        )
        .unsafeRunSync()

  private def complete(
      harness: Harness,
      ownerId: String,
      generation: Long,
      filename: String = "clip.mp3",
      bytes: Array[Byte] = audioBytes
  ): StoredPreparation =
    harness.store
      .complete(
        ownerId,
        generation,
        "utterance-1",
        selection,
        enrichment,
        "en",
        filename,
        bytes
      )
      .unsafeRunSync()
      .getOrElse(fail("current completion was not stored"))

  private def sha256(bytes: Array[Byte]): String =
    HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))

  private final class Harness private (
      val store: EphemeralAudioStore,
      elapsedNanos: AtomicLong
  ):
    def advance(duration: FiniteDuration): Unit =
      elapsedNanos.addAndGet(duration.toNanos)

  private object Harness:
    def apply(): Harness =
      val ids = new AtomicLong(0L)
      val elapsedNanos = new AtomicLong(0L)
      val store = EphemeralAudioStore
        .create(
          IO.delay(s"test-audio-${ids.incrementAndGet()}"),
          IO.delay(elapsedNanos.get().nanos)
        )
        .unsafeRunSync()
      new Harness(store, elapsedNanos)
