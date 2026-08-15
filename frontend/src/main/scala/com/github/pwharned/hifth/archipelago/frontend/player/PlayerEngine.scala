package com.github.pwharned.hifth.frontend.player
import org.scalajs.dom
import com.github.pwharned.hifth.shared.domain.ClozeLevel
// ── Masking Engine ────────────────────────────────────────────────────────────
// Computes which word indices are masked for a given cloze level.
// Uses a seeded shuffle so:
//   - The same QH always masks the same words
//   - Mask sets are additive: words masked at 25% are also masked at 50%
object MaskEngine:
  def maskedIndices(
      totalWords: Int,
      clozeLevel: ClozeLevel,
      seed: Int
  ): Set[Int] =
    val rng = new scala.util.Random(seed)
    val shuffled = rng.shuffle((0 until totalWords).toVector)
    val count = math.round(totalWords * clozeLevel.percent / 100.0).toInt
    val res = shuffled.take(count).toSet
    res
// ── Tap Scorer ────────────────────────────────────────────────────────────────
object TapScorer:
  private val PerfectThresholdMs = 500.0
  private val GoodThresholdMs = 1500.0
  def score(deltaMs: Double): TapResult =
    val abs = math.abs(deltaMs)
    if abs < PerfectThresholdMs then TapResult.Perfect
    else if abs < GoodThresholdMs then TapResult.Good
    else TapResult.Miss
  // Called at the END of an Ayah. If no tap was recorded, it is a Miss.
  def evaluate(
      ayahNumber: Int,
      expectedMs: Double,
      lastTapMs: Option[Double]
  ): AyahTapRecord =
    lastTapMs match
      case None =>
        AyahTapRecord(ayahNumber, expectedMs, -1.0, TapResult.Miss)
      case Some(tapMs) =>
        AyahTapRecord(ayahNumber, expectedMs, tapMs, score(tapMs - expectedMs))
// ── Audio URL Builder ─────────────────────────────────────────────────────────

object AudioUrls:
  def forSurah(surahNumber: Int): String =
    val padded = f"$surahNumber%03d"
    s"/data/audio/$padded.mp3"
  def alignmentJsonUrl(surahNumber: Int): String =
    val padded = f"$surahNumber%03d"
    s"/data/surah/${padded}_aligned.json"
