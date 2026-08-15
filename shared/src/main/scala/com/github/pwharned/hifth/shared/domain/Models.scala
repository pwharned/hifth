package com.github.pwharned.hifth.shared.domain
import com.github.plokhotnyuk.jsoniter_scala.macros.*
import com.github.plokhotnyuk.jsoniter_scala.core.*
// ── User Identity ─────────────────────────────────────────────────────────────
sealed trait UserId
object UserId:
  case object GlobalUser extends UserId
  given JsonValueCodec[UserId] = JsonCodecMaker.make
// ── Cloze Level ───────────────────────────────────────────────────────────────
enum ClozeLevel(val percent: Int):
  case L0 extends ClozeLevel(0)
  case L10 extends ClozeLevel(10)
  case L25 extends ClozeLevel(25)
  case L50 extends ClozeLevel(50)
  case L75 extends ClozeLevel(75)
  case L90 extends ClozeLevel(90)
  case L95 extends ClozeLevel(95)
object ClozeLevel:
  val steps: Vector[ClozeLevel] =
    Vector(L0, L10, L25, L50, L75, L90, L95)
  val reviewThreshold: ClozeLevel = L95
  def increment(current: ClozeLevel): ClozeLevel =
    val idx = steps.indexOf(current)
    if idx < steps.length - 1 then steps(idx + 1) else current
  def decrement(current: ClozeLevel): ClozeLevel =
    val idx = steps.indexOf(current)
    if idx > 0 then steps(idx - 1) else current
  def fromPercent(p: Int): Option[ClozeLevel] =
    steps.find(_.percent == p)
  given JsonValueCodec[ClozeLevel] = JsonCodecMaker.make
// ── Study Phase ───────────────────────────────────────────────────────────────
sealed trait StudyPhase
object StudyPhase:
  case object NotStarted extends StudyPhase
  case class Studying(
      clozeLevel: ClozeLevel
  ) extends StudyPhase
  case class Reviewing(
      clozeLevel: ClozeLevel,
      easeFactor: Double,
      intervalDays: Int,
      nextReviewMs: Long
  ) extends StudyPhase
  given JsonValueCodec[StudyPhase] = JsonCodecMaker.make
// ── SRS Entry ─────────────────────────────────────────────────────────────────
case class SRSEntry(
    quarterHizbId: Int,
    userId: UserId,
    phase: StudyPhase
)
object SRSEntry:
  given JsonValueCodec[SRSEntry] = JsonCodecMaker.make
  def fresh(quarterHizbId: Int, userId: UserId): SRSEntry =
    SRSEntry(
      quarterHizbId = quarterHizbId,
      userId = userId,
      phase = StudyPhase.NotStarted
    )
// ── Session Result ────────────────────────────────────────────────────────────
// Produced by the player at the end of a session.
// Carried in AppPage.SessionComplete and submitted to the backend.
case class SessionResult(
    quarterHizbId: Int,
    clozeAchieved: ClozeLevel,
    tapPerfect: Int,
    tapGood: Int,
    tapMiss: Int,
    durationMs: Long
)
object SessionResult:
  given JsonValueCodec[SessionResult] = JsonCodecMaker.make
// ── Session Log ───────────────────────────────────────────────────────────────
// One entry written per completed session.
// Retained for a rolling 90-day window.
// Used to compute streak state.
case class SessionLog(
    quarterHizbId: Int,
    userId: UserId,
    timestamp: Long, // epoch ms - day boundary used for streak calc
    clozeAchieved: ClozeLevel,
    tapPerfect: Int,
    tapGood: Int,
    tapMiss: Int,
    durationMs: Long
)
object SessionLog:
  given JsonValueCodec[SessionLog] = JsonCodecMaker.make
// ── Streak State ──────────────────────────────────────────────────────────────
// Computed from recent session logs.
// shieldActive:    the shield is currently protecting an unbroken streak
//                  (user missed one day but has not burned the shield yet)
// shieldAvailable: the user has earned a shield through 7 consecutive days
//                  and it has not been used yet
case class StreakState(
    count: Int,
    shieldActive: Boolean,
    shieldAvailable: Boolean
)
object StreakState:
  given JsonValueCodec[StreakState] = JsonCodecMaker.make
  val empty: StreakState = StreakState(
    count = 0,
    shieldActive = false,
    shieldAvailable = false
  )
