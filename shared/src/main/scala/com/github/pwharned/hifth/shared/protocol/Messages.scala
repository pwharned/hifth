package com.github.pwharned.hifth.shared.protocol
import com.github.plokhotnyuk.jsoniter_scala.macros.*
import com.github.plokhotnyuk.jsoniter_scala.core.*
import com.github.pwharned.hifth.shared.domain.*
// ── Client → Server ───────────────────────────────────────────────────────────
sealed trait ClientMessage
object ClientMessage:
  // Sent on connect - server responds with HomeStateLoaded
  case object RequestHomeState extends ClientMessage
  // Sent when a session completes
  case class SubmitSession(
      quarterHizbId: Int,
      achievedClozePercent: Int,
      tapPerfect: Int,
      tapGood: Int,
      tapMiss: Int,
      durationMs: Long
  ) extends ClientMessage
  given JsonValueCodec[ClientMessage] = JsonCodecMaker.make
// ── Server → Client ───────────────────────────────────────────────────────────
sealed trait ServerMessage
object ServerMessage:
  // Full state snapshot sent on connect and after any mutation.
  // Carries everything the home screen needs in a single message.
  case class HomeStateLoaded(
      entries: List[SRSEntry],
      recentLogs: List[SessionLog],
      streakState: StreakState
  ) extends ServerMessage
  // Sent after a session is recorded.
  // Frontend patches local state rather than waiting for full reload.
  case class SessionAccepted(
      updatedEntry: SRSEntry,
      newLog: SessionLog,
      streakState: StreakState
  ) extends ServerMessage
  case class Error(msg: String) extends ServerMessage
  given JsonValueCodec[ServerMessage] = JsonCodecMaker.make
