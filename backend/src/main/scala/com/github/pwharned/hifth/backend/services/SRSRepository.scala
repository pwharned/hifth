package com.github.pwharned.hifth.backend.services
import cats.effect.{IO, Ref, Resource}
import com.github.pwharned.hifth.shared.domain.*
// ── SRS Entry Repository ──────────────────────────────────────────────────────
trait SRSRepository[F[_]]:
  def getAll(userId: UserId): F[List[SRSEntry]]
  def upsert(entry: SRSEntry): F[Unit]
class InMemorySRSRepository(
    store: Ref[IO, Map[(UserId, Int), SRSEntry]]
) extends SRSRepository[IO]:
  def getAll(userId: UserId): IO[List[SRSEntry]] =
    store.get.map(
      _.values
        .filter(_.userId == userId)
        .toList
        .sortBy(_.quarterHizbId)
    )
  def upsert(entry: SRSEntry): IO[Unit] =
    store.update(_.updated((entry.userId, entry.quarterHizbId), entry))
object InMemorySRSRepository:
  def make: Resource[IO, InMemorySRSRepository] =
    Resource.eval(
      Ref
        .of[IO, Map[(UserId, Int), SRSEntry]](Map.empty)
        .map(new InMemorySRSRepository(_))
    )
// ── Session Log Repository ────────────────────────────────────────────────────
// Retains a rolling 90-day window of session logs per user.
// Used for streak calculation and recent history display.
trait SessionLogRepository[F[_]]:
  def append(log: SessionLog): F[Unit]
  def recent(userId: UserId, sinceMs: Long): F[List[SessionLog]]
class InMemorySessionLogRepository(
    store: Ref[IO, List[SessionLog]]
) extends SessionLogRepository[IO]:
  private val NinetyDaysMs = 90L * 24 * 60 * 60 * 1000
  def append(log: SessionLog): IO[Unit] =
    val cutoff = log.timestamp - NinetyDaysMs
    store.update { logs =>
      // Append new log and prune entries older than 90 days from now
      (log :: logs).filter(_.timestamp >= cutoff)
    }
  def recent(userId: UserId, sinceMs: Long): IO[List[SessionLog]] =
    store.get.map(
      _.filter(l => l.userId == userId && l.timestamp >= sinceMs)
        .sortBy(_.timestamp)
    )
object InMemorySessionLogRepository:
  def make: Resource[IO, InMemorySessionLogRepository] =
    Resource.eval(
      Ref
        .of[IO, List[SessionLog]](List.empty)
        .map(new InMemorySessionLogRepository(_))
    )
