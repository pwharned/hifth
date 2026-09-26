package com.github.pwharned.hifth.backend.services
import com.github.pwharned.hifth.shared.domain.*
import java.time.{Instant, LocalDate, ZoneOffset}
// ── Streak Calculator ─────────────────────────────────────────────────────────
// Computes StreakState from a list of recent session logs.
//
// Rules:
//   - One study day = at least one session log with a timestamp on that
//     calendar day (UTC)
//   - Consecutive days are counted backwards from today
//   - Shield absorbs one missed day without breaking the streak
//   - Shield becomes available after SHIELD_EARN_DAYS consecutive days
//   - Shield regenerates after SHIELD_EARN_DAYS consecutive days following
//     a shield use
//
// This is a pure function - no IO, fully testable.
object StreakCalculator:
  private val ShieldEarnDays = 7
  def compute(logs: List[SessionLog], nowMs: Long): StreakState =
    if logs.isEmpty then return StreakState.empty
    // Convert all log timestamps to UTC calendar dates
    val studiedDays: Set[LocalDate] = logs
      .map(l =>
        Instant
          .ofEpochMilli(l.timestamp)
          .atZone(ZoneOffset.UTC)
          .toLocalDate
      )
      .toSet
    val today = Instant
      .ofEpochMilli(nowMs)
      .atZone(ZoneOffset.UTC)
      .toLocalDate
    // Walk backwards from today counting consecutive study days.
    // One gap is allowed if a shield has not yet been used in this run.
    var streak = 0
    var shieldUsed = false
    var shieldEarned = false
    var consecutiveRun = 0 // tracks runs of 7+ for shield regeneration
    var date = today
    var running = true
    while running do
      if studiedDays.contains(date) then
        streak += 1
        consecutiveRun += 1
        // Shield becomes available once 7 consecutive days are accumulated
        if consecutiveRun >= ShieldEarnDays then shieldEarned = true
        date = date.minusDays(1)
      else
        // Gap found
        if !shieldUsed && shieldEarned then
          // Burn the shield to bridge this gap
          shieldUsed = true
          consecutiveRun = 0
          date = date.minusDays(1)
        else
          // No shield available or already used - streak ends here
          running = false
    // Shield is active if it was earned and has not been used
    // Shield is available if earned and not used
    val shieldAvailable = shieldEarned && !shieldUsed
    val shieldActive = shieldUsed // shield was consumed bridging a gap
    StreakState(
      count = streak,
      shieldActive = shieldActive,
      shieldAvailable = shieldAvailable
    )
