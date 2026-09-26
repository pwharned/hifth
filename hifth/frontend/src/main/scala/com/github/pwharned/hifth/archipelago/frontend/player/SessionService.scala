package com.github.pwharned.hifth.frontend.player
import com.raquo.laminar.api.L.*
import com.github.pwharned.hifth.shared.domain.*

/** Tracks taps + produces a SessionResult. */
trait SessionService:
  val tapRecords: Var[Vector[AyahTapRecord]]
  val lastTapMs: Var[Option[Double]]
  val sessionDone: Var[Boolean]

  /** Called at each ayah boundary by PlaybackService */
  def scoreAyah(ayahNumber: Int, ayahEndMs: Double): Unit

  /** Score any ayahs that weren't hit before session end */
  def scoreRemaining(allWords: Vector[PlayerWord]): Unit
  def recordTap(nowMs: Double): Unit
  def buildResult(
      quarterHizbId: Int,
      clozeLevel: ClozeLevel,
      startMs: Double
  ): SessionResult
  val tapSummarySignal: Signal[String]
object SessionService:
  final class Live extends SessionService:
    val tapRecords: Var[Vector[AyahTapRecord]] = Var(Vector.empty)
    val lastTapMs: Var[Option[Double]] = Var(None)
    val sessionDone: Var[Boolean] = Var(false)
    def recordTap(nowMs: Double): Unit =
      lastTapMs.set(Some(nowMs))
    def scoreAyah(ayahNumber: Int, ayahEndMs: Double): Unit =
      val already = tapRecords.now().exists(_.ayahNumber == ayahNumber)
      if !already then
        val record = TapScorer.evaluate(ayahNumber, ayahEndMs, lastTapMs.now())
        tapRecords.update(_ :+ record)
        lastTapMs.set(None)
    def scoreRemaining(allWords: Vector[PlayerWord]): Unit =
      val scored = tapRecords.now().map(_.ayahNumber).toSet
      allWords.map(_.ayah).distinct.filterNot(scored.contains).foreach { ayah =>
        allWords.filter(_.ayah == ayah).lastOption.foreach { lw =>
          tapRecords.update(
            _ :+ TapScorer.evaluate(ayah, lw.endMs, lastTapMs.now())
          )
        }
      }
    def buildResult(
        quarterHizbId: Int,
        clozeLevel: ClozeLevel,
        startMs: Double
    ): SessionResult =
      val records = tapRecords.now()
      SessionResult(
        quarterHizbId = quarterHizbId,
        clozeAchieved = clozeLevel,
        tapPerfect = records.count(_.result == TapResult.Perfect),
        tapGood = records.count(_.result == TapResult.Good),
        tapMiss = records.count(_.result == TapResult.Miss),
        durationMs = (org.scalajs.dom.window.performance.now() - startMs).toLong
      )
    val tapSummarySignal: Signal[String] =
      tapRecords.signal.map {
        case r if r.isEmpty => ""
        case records        =>
          val p = records.count(_.result == TapResult.Perfect)
          val g = records.count(_.result == TapResult.Good)
          val m = records.count(_.result == TapResult.Miss)
          s"✦ $p  ✔ $g  ✗ $m"
      }
