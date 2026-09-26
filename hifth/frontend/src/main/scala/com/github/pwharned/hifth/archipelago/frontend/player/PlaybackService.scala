package com.github.pwharned.hifth.frontend.player
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import org.scalajs.dom.Audio
trait PlaybackService:
  val playState: Var[PlayState]
  val currentTimeMs: Var[Double]
  val activeWordIdx: Var[Option[WordIndex]]
  val revealedWords: Var[Set[WordIndex]]
  val currentSegIdx: Var[Int]
  def start(
      maskedWords: () => Set[WordIndex],
      onAyahBoundary: (Int, Double) => Unit,
      onComplete: Double => Unit
  ): Unit
  def pause(): Unit
  def toggle(): Unit
  // def seekRatio(ratio: Double, seg: PlayerSegment): Unit
  def dispose(): Unit
object PlaybackService:
  private val RevealOffsetMs = 400.0
  final class Live(segs: Vector[PlayerSegment]) extends PlaybackService:
    val playState: Var[PlayState] = Var(PlayState.Idle)
    val currentTimeMs: Var[Double] = Var(0.0)
    val activeWordIdx: Var[Option[WordIndex]] = Var(None)
    val revealedWords: Var[Set[WordIndex]] = Var(Set.empty)
    val currentSegIdx: Var[Int] = Var(0)
    private var rafHandle: Option[Double] = None
    private var segments: Vector[PlayerSegment] = Vector.empty
    private var getMasked: () => Set[WordIndex] = () => Set.empty
    private var onBoundary: (Int, Double) => Unit = (_, _) => ()
    private var onDone: Double => Unit = _ => ()
    def start(
        maskedWords: () => Set[WordIndex],
        onAyahBoundary: (Int, Double) => Unit,
        onComplete: Double => Unit
    ): Unit = {
      segments = segs
      getMasked = maskedWords
      onBoundary = onAyahBoundary
      onDone = onComplete
      segs.lift(currentSegIdx.now()).foreach { seg =>
        {
          seg.audioService.play()
          playState.set(PlayState.Playing)
          scheduleRaf()
        }
      }
    }
    def pause(): Unit =
      segs.lift(currentSegIdx.now()).foreach {
        seg =>
          seg.audioService.pause()
          playState.set(PlayState.Paused)
        cancelRaf()
      }
    def toggle(): Unit =
      playState.now() match
        case PlayState.Playing => pause()
        case _                 =>
          segs.lift(currentSegIdx.now()).foreach { seg =>
            seg.audioService.play()
            playState.set(PlayState.Playing)
            scheduleRaf()
          }
    // def seekRatio(ratio: Double, seg: PlayerSegment): Unit =
    // val rangeMs = seg.stopAtMs - seg.seekStartMs
    // seg.audioService.applyTime((seg.seekStartMs + ratio * rangeMs) / 1000.0)
    def dispose(): Unit =
      segs.lift(currentSegIdx.now()).foreach { seg =>
        seg.audioService.pause()
      }
      cancelRaf()

    // ── internals ────────────────────────────────────────────────────────────
    private def scheduleRaf(): Unit =
      cancelRaf()
      rafHandle = Some(dom.window.requestAnimationFrame(_ => tick()))
    private def cancelRaf(): Unit =
      rafHandle.foreach(h => dom.window.cancelAnimationFrame(h.toInt))
      rafHandle = None
    private def tick(): Unit =
      segments.lift(currentSegIdx.now()).foreach { seg =>
        val nowMs = seg.audioService.currentTimeMs
        if nowMs >= seg.stopAtMs then
          val next = currentSegIdx.now() + 1
          if next < segments.length then
            println("moving to next segment")
            currentSegIdx.set(next)
            val nextSeg = segments(next)
            nextSeg.audioService.load()
            nextSeg.audioService.play()
            scheduleRaf()
          else
            playState.set(PlayState.Idle)
            onDone(nowMs)
          return
        currentTimeMs.set(nowMs)
        updateActiveWord(seg, nowMs)
        updateRevealedWords(seg, nowMs)
        checkAyahBoundary(seg, nowMs)
      }
      scheduleRaf()
    private def updateActiveWord(seg: PlayerSegment, nowMs: Double): Unit =
      val idx = seg.words.indexWhere(w => nowMs >= w.startMs && nowMs < w.endMs)
      val next = if idx >= 0 then Some(seg.words(idx).globalIndex) else None
      seg.audioService.applyVolume(next.exists(getMasked().contains))
      activeWordIdx.set(next)
    private def updateRevealedWords(seg: PlayerSegment, nowMs: Double): Unit =
      val newReveals = seg.words
        .filter(w => nowMs >= w.startMs + RevealOffsetMs)
        .map(_.globalIndex)
        .toSet
      revealedWords.update(_ ++ newReveals)
    private def checkAyahBoundary(seg: PlayerSegment, nowMs: Double): Unit =
      val words = seg.words
      val active = words.indexWhere(w => nowMs >= w.startMs && nowMs < w.endMs)
      if active > 0 then
        val curr = words(active).ayah
        val prev = words(active - 1).ayah
        if curr != prev then
          words.filter(_.ayah == prev).lastOption.foreach { lw =>
            onBoundary(prev, lw.endMs)
          }
