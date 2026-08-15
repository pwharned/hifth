package com.github.pwharned.hifth.backend.services
import com.github.pwharned.hifth.shared.domain.StudyPhase.Reviewing
// ── SM2 Scheduling (Binary Pass/Fail) ────────────────────────────────────────
// Used only in the Reviewing phase.
// Pass = session completed at cloze L95
// Fail = session submitted below L95 (caller handles phase downgrade)
object SM2:
  private val MinEaseFactor = 1.3
  private val InitialEase = 2.5
  private val MsPerDay = 86400000L
  // Called on a PASS. Advances the interval and adjusts ease.
  def advance(current: Reviewing, nowMs: Long): Reviewing =
    val newInterval = current.intervalDays match
      case 0 => 1
      case 1 => 6
      case n => math.round(n * current.easeFactor).toInt
    // Binary pass uses quality 4 in SM2 ease formula
    val quality = 4
    val newEase = math.max(
      MinEaseFactor,
      current.easeFactor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    )
    current.copy(
      easeFactor = newEase,
      intervalDays = newInterval,
      nextReviewMs = nowMs + newInterval * MsPerDay
    )
  // Returns a fresh Reviewing state for when a Studying entry first reaches L95
  def initialReviewing(nowMs: Long): Reviewing =
    Reviewing(
      clozeLevel = com.github.pwharned.hifth.shared.domain.ClozeLevel.L95,
      easeFactor = InitialEase,
      intervalDays = 0,
      nextReviewMs = nowMs
    )
