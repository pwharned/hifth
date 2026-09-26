package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.{Clock, IO, Ref}
import com.github.pwharned.flashcards.shared.domain.{Enrichment, PreparedAudio, SelectionSpan}

import java.security.{MessageDigest, SecureRandom}
import java.util.HexFormat
import scala.concurrent.duration.*

final class StoredPreparation private[backend] (
    val descriptor: PreparedAudio,
    val sourceLanguage: String,
    val binding: StoredPreparation.Binding,
    private val storedBytes: Array[Byte]
):
  def audio: PreparedAudio = descriptor
  def utteranceId: String = binding.utteranceId
  def selection: SelectionSpan = binding.selection
  def enrichment: Enrichment = binding.enrichment
  def bytes: Array[Byte] = storedBytes.clone()

object StoredPreparation:
  final case class Binding(
      utteranceId: String,
      selection: SelectionSpan,
      enrichment: Enrichment
  )

final class EphemeralAudioStore private (
    state: Ref[IO, EphemeralAudioStore.State],
    nextAudioId: IO[String],
    now: IO[FiniteDuration]
):
  import EphemeralAudioStore.*

  def begin(ownerId: String): IO[Long] =
    state.modify: current =>
      val generation = current.lastGeneration + 1L
      val updated = current.copy(
        lastGeneration = generation,
        owners = current.owners.updated(ownerId, OwnerState(generation, None))
      )
      updated -> generation

  def complete(
      ownerId: String,
      generation: Long,
      utteranceId: String,
      selection: SelectionSpan,
      enrichment: Enrichment,
      sourceLanguage: String,
      filename: String,
      bytes: Array[Byte]
  ): IO[Option[StoredPreparation]] =
    for
      safeBytes <- validateAndCopy(bytes)
      digest <- IO.delay(sha256(safeBytes))
      createdAt <- now
      result <- store(
        ownerId,
        generation,
        StoredPreparation.Binding(utteranceId, selection, enrichment),
        sourceLanguage,
        filename,
        safeBytes,
        digest,
        createdAt
      )
    yield result

  def find(audioId: String): IO[Option[StoredPreparation]] =
    now.flatMap: accessedAt =>
      state.modify: current =>
        val pruned = pruneExpiredEntries(current, accessedAt)
        val result = pruned.owners.valuesIterator.collectFirst:
          case OwnerState(_, Some(entry))
              if entry.preparation.descriptor.audioId == audioId =>
            entry.preparation
        pruned -> result

  def forCreate(
      ownerId: String,
      audioId: String,
      utteranceId: String,
      selection: SelectionSpan
  ): IO[Either[String, StoredPreparation]] =
    now.flatMap: accessedAt =>
      state.modify: current =>
        val queriedEntry = current.owners.get(ownerId).flatMap(_.entry)
        val pruned = pruneExpiredEntries(current, accessedAt)
        if queriedEntry.exists(expired(_, accessedAt)) then
          pruned -> Left("prepared audio has expired")
        else
          pruned.owners.get(ownerId).flatMap(_.entry) match
            case Some(entry) if entry.preparation.descriptor.audioId != audioId =>
              pruned -> Left("prepared audio is unavailable for this owner")
            case Some(entry)
                if entry.preparation.binding.utteranceId != utteranceId ||
                  entry.preparation.binding.selection != selection =>
              pruned -> Left("prepared audio does not match the requested selection")
            case Some(entry) => pruned -> Right(entry.preparation)
            case None        => pruned -> Left("prepared audio is unavailable for this owner")

  def pruneExpired: IO[Unit] =
    now.flatMap: accessedAt =>
      state.update(current => pruneExpiredEntries(current, accessedAt))

  def clear(ownerId: String): IO[Unit] =
    state.update(current => current.copy(owners = current.owners - ownerId))

  def clearIfMatches(ownerId: String, audioId: String): IO[Unit] =
    state.update: current =>
      current.owners.get(ownerId).flatMap(_.entry) match
        case Some(entry) if entry.preparation.descriptor.audioId == audioId =>
          current.copy(owners = current.owners - ownerId)
        case _ => current

  private def store(
      ownerId: String,
      generation: Long,
      binding: StoredPreparation.Binding,
      sourceLanguage: String,
      filename: String,
      bytes: Array[Byte],
      digest: String,
      createdAt: FiniteDuration
  ): IO[Option[StoredPreparation]] =
    nextAudioId.flatMap: audioId =>
      descriptor(audioId, filename, bytes.length, digest).flatMap: audio =>
        val preparation = StoredPreparation(audio, sourceLanguage, binding, bytes)
        state.modify: current =>
          current.owners.get(ownerId) match
            case Some(owner) if owner.generation == generation =>
              val pruned = pruneExpiredEntries(current, createdAt)
              val currentOwner = pruned.owners(ownerId)
              val withoutCurrentEntry = pruned.copy(
                owners = pruned.owners.updated(ownerId, currentOwner.copy(entry = None))
              )
              val idInUse = withoutCurrentEntry.owners.valuesIterator.exists:
                _.entry.exists(_.preparation.descriptor.audioId == audioId)
              if idInUse then pruned -> StoreResult.Collision
              else
                val entry = Entry(preparation, createdAt)
                val bounded = makeRoom(withoutCurrentEntry, audio.sizeBytes)
                bounded.copy(
                  owners = bounded.owners.updated(ownerId, currentOwner.copy(entry = Some(entry)))
                ) -> StoreResult.Stored(preparation)
            case _ => current -> StoreResult.Stale
        .flatMap:
          case StoreResult.Stored(value) => IO.pure(Some(value))
          case StoreResult.Stale         => IO.pure(None)
          case StoreResult.Collision =>
            store(
              ownerId,
              generation,
              binding,
              sourceLanguage,
              filename,
              bytes,
              digest,
              createdAt
            )

  private def descriptor(
      audioId: String,
      filename: String,
      sizeBytes: Int,
      digest: String
  ): IO[PreparedAudio] =
    val audio = PreparedAudio(
      audioId = audioId,
      previewUrl = s"/prepared-audio/$audioId",
      filename = Option(filename).fold("")(_.trim),
      contentType = ContentType,
      sizeBytes = sizeBytes.toLong,
      sha256 = digest
    )
    IO.fromEither(audio.validate.left.map(new IllegalArgumentException(_)))

  private def validateAndCopy(bytes: Array[Byte]): IO[Array[Byte]] =
    if bytes == null || bytes.isEmpty then
      IO.raiseError(new IllegalArgumentException("prepared audio must not be empty"))
    else if bytes.length > MaxBytes then
      IO.raiseError(new IllegalArgumentException("prepared audio exceeds the 10 MiB limit"))
    else IO.delay(bytes.clone())

  private def expired(entry: Entry, accessedAt: FiniteDuration): Boolean =
    accessedAt - entry.createdAt >= Lifetime

  private def pruneExpiredEntries(current: State, accessedAt: FiniteDuration): State =
    current.copy(
      owners = current.owners.map: (ownerId, owner) =>
        val retained = owner.entry.filterNot(expired(_, accessedAt))
        ownerId -> owner.copy(entry = retained)
    )

  private def makeRoom(current: State, incomingSize: Long): State =
    val entries = current.owners.iterator.flatMap: (ownerId, owner) =>
      owner.entry.map(ownerId -> _)
    .toList
    val oldest = entries.sortBy: (ownerId, entry) =>
      entry.createdAt.toNanos -> ownerId
    val evictedOwners = selectEvictions(
      oldest,
      entries.size,
      entries.iterator.map(_._2.preparation.descriptor.sizeBytes).sum,
      incomingSize,
      Set.empty
    )
    current.copy(
      owners = evictedOwners.foldLeft(current.owners): (owners, ownerId) =>
        owners.updated(ownerId, owners(ownerId).copy(entry = None))
    )

  private def selectEvictions(
      oldest: List[(String, Entry)],
      count: Int,
      storedBytes: Long,
      incomingSize: Long,
      selected: Set[String]
  ): Set[String] =
    if count + 1 <= MaxEntries && storedBytes + incomingSize <= MaxStoredBytes then selected
    else
      oldest match
        case (ownerId, entry) :: remaining =>
          selectEvictions(
            remaining,
            count - 1,
            storedBytes - entry.preparation.descriptor.sizeBytes,
            incomingSize,
            selected + ownerId
          )
        case Nil => selected

object EphemeralAudioStore:
  private val ContentType = "audio/mpeg"
  private val Lifetime = 30.minutes
  private val MaxBytes = 10 * 1024 * 1024
  private val MaxEntries = 32
  private val MaxStoredBytes = 100L * 1024L * 1024L
  private val secureRandom = new SecureRandom()

  private final case class Entry(
      preparation: StoredPreparation,
      createdAt: FiniteDuration
  )

  private final case class OwnerState(
      generation: Long,
      entry: Option[Entry]
  )

  private final case class State(
      lastGeneration: Long,
      owners: Map[String, OwnerState]
  )

  private enum StoreResult:
    case Stored(value: StoredPreparation)
    case Stale
    case Collision

  def create: IO[EphemeralAudioStore] =
    create(randomAudioId, Clock[IO].monotonic)

  def apply(): IO[EphemeralAudioStore] = create

  private[backend] def create(
      nextAudioId: IO[String],
      now: IO[FiniteDuration]
  ): IO[EphemeralAudioStore] =
    Ref
      .of[IO, State](State(0L, Map.empty))
      .map(new EphemeralAudioStore(_, nextAudioId, now))

  private def randomAudioId: IO[String] = IO.blocking:
    val bytes = new Array[Byte](32)
    secureRandom.nextBytes(bytes)
    HexFormat.of().formatHex(bytes)

  private def sha256(bytes: Array[Byte]): String =
    HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
