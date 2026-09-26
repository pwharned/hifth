package com.github.pwharned.hifth.backend.services
import cats.effect.{IO, Resource}
import com.github.pwharned.hifth.backend.MessageHandler
import com.github.pwharned.hifth.shared.domain.*
import com.github.pwharned.hifth.shared.domain.StudyPhase.*
import com.github.pwharned.hifth.shared.protocol.*
class SRSService(
    srsRepo: SRSRepository[IO],
    logRepo: SessionLogRepository[IO]
) extends MessageHandler[IO, ClientMessage, ServerMessage]:
  private val NinetyDaysMs = 90L * 24 * 60 * 60 * 1000
  def handle(msg: ClientMessage): IO[Delivery[ServerMessage]] =
    msg match
      case ClientMessage.RequestHomeState   => handleRequestHomeState()
      case msg: ClientMessage.SubmitSession => handleSubmitSession(msg)
  // ── Request Home State ──────────────────────────────────────────────────────
  private def handleRequestHomeState(): IO[Delivery[ServerMessage]] =
    val userId = UserId.GlobalUser
    val nowMs = System.currentTimeMillis()
    val cutoff = nowMs - NinetyDaysMs
    for
      entries <- srsRepo.getAll(userId)
      logs <- logRepo.recent(userId, cutoff)
      streak = StreakCalculator.compute(logs, nowMs)
    yield Delivery.Unicast(
      ServerMessage.HomeStateLoaded(
        entries = entries,
        recentLogs = logs,
        streakState = streak
      )
    )
  // ── Submit Session ──────────────────────────────────────────────────────────
  private def handleSubmitSession(
      msg: ClientMessage.SubmitSession
  ): IO[Delivery[ServerMessage]] =
    ClozeLevel.fromPercent(msg.achievedClozePercent) match
      case None =>
        IO.pure(
          Delivery.Unicast(
            ServerMessage.Error(
              s"${msg.achievedClozePercent} is not a valid cloze step. " +
                s"Valid values: ${ClozeLevel.steps.map(_.percent).mkString(", ")}"
            )
          )
        )
      case Some(achievedLevel) =>
        val userId = UserId.GlobalUser
        val nowMs = System.currentTimeMillis()
        val cutoff = nowMs - NinetyDaysMs
        for
          // Resolve existing entry or create fresh
          existing <- srsRepo
            .getAll(userId)
            .map(
              _.find(_.quarterHizbId == msg.quarterHizbId)
                .getOrElse(SRSEntry.fresh(msg.quarterHizbId, userId))
            )
          // Compute new SRS phase
          updated = transition(existing, achievedLevel, nowMs)
          // Write updated entry
          _ <- srsRepo.upsert(updated)
          // Build and write session log
          log = SessionLog(
            quarterHizbId = msg.quarterHizbId,
            userId = userId,
            timestamp = nowMs,
            clozeAchieved = achievedLevel,
            tapPerfect = msg.tapPerfect,
            tapGood = msg.tapGood,
            tapMiss = msg.tapMiss,
            durationMs = msg.durationMs
          )
          _ <- logRepo.append(log)
          // Recompute streak from updated log set
          recentLogs <- logRepo.recent(userId, cutoff)
          streak = StreakCalculator.compute(recentLogs, nowMs)
        yield Delivery.Unicast(
          ServerMessage.SessionAccepted(
            updatedEntry = updated,
            newLog = log,
            streakState = streak
          )
        )
  // ── Phase Transition Logic ──────────────────────────────────────────────────
  // NotStarted + any cloze  → Studying(cloze)
  // Studying   + cloze < 95 → Studying(cloze)
  // Studying   + cloze = 95 → Reviewing(fresh SM2)
  // Reviewing  + cloze = 95 → Reviewing(SM2 advance)
  // Reviewing  + cloze < 95 → Studying(cloze)  (demotion - resets interval)
  private def transition(
      entry: SRSEntry,
      achievedLevel: ClozeLevel,
      nowMs: Long
  ): SRSEntry =
    val newPhase = (entry.phase, achievedLevel) match
      case (_, level) if level.percent < ClozeLevel.reviewThreshold.percent =>
        Studying(level)
      case (NotStarted, ClozeLevel.L95) =>
        SM2.initialReviewing(nowMs)
      case (Studying(_), ClozeLevel.L95) =>
        SM2.initialReviewing(nowMs)
      case (reviewing: Reviewing, ClozeLevel.L95) =>
        SM2.advance(reviewing, nowMs)
      case (phase, _) =>
        phase
    entry.copy(phase = newPhase)
object SRSService:
  def make(
      srsRepo: SRSRepository[IO],
      logRepo: SessionLogRepository[IO]
  ): Resource[IO, SRSService] =
    Resource.pure(SRSService(srsRepo, logRepo))
