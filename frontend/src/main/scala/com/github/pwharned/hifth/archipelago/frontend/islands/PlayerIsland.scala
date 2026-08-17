package com.github.pwharned.hifth.frontend.islands
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import scala.scalajs.js.annotation.JSExportTopLevel
import scala.scalajs.concurrent.JSExecutionContext.Implicits.queue
import com.github.pwharned.hifth.frontend.{AppBus, AppPage}
import com.github.pwharned.hifth.frontend.player.*
import com.github.pwharned.hifth.shared.domain.*
import com.github.pwharned.hifth.shared.domain.StudyPhase.*
object PlayerIsland:
  @JSExportTopLevel("mountPlayer", moduleID = "player")
  def mount(el: dom.Element, quarterHizbId: Int): Unit =
    // ── Bootstrap services ───────────────────────────────────────────────────
    val initialCloze: ClozeLevel =
      AppBus.srsEntries.now().get(quarterHizbId).map(_.phase) match
        case Some(Studying(level)) => level
        case Some(_: Reviewing)    => ClozeLevel.L95
        case _                     => ClozeLevel.L0

    val loadState: Var[LoadState] = Var(LoadState.Loading)
    val playback = Var(PlaybackService.Live(Vector.empty))
    val session = SessionService.Live()
    val masking = MaskingService.Live(initialCloze)
    val sessionStartMs: Double = dom.window.performance.now()
    // ── Derived signals (for UI binding only) ────────────────────────────────
    val allWordsSignal: Signal[Vector[PlayerWord]] =
      loadState.signal.map:
        case LoadState.Ready(segs) => segs.flatMap(_.words)
        case _                     => Vector.empty
    val currentSegmentSignal: Signal[Option[PlayerSegment]] =
      loadState.signal
        .combineWith(playback)
        .flatMapSwitch:
          case (LoadState.Ready(segs), player) =>
            player.currentSegIdx.signal.map(x => segs.lift(x))
          case _ => Val(None)
    // ── Helpers that read current values without calling .now() on Signals ───
    def allWordsNow(): Vector[PlayerWord] =
      loadState.now() match
        case LoadState.Ready(segs) => segs.flatMap(_.words)
        case _                     => Vector.empty
    // ── Start masking reactor ────────────────────────────────────────────────
    masking.watch(allWordsSignal, seed = quarterHizbId)
    println("loading")
    // ── Load segments ────────────────────────────────────────────────────────
    import scala.util.{Success, Failure}
    SegmentLoader.load(QuranData.segmentsForQH(quarterHizbId)).onComplete {
      case Success(segs) =>
        segs.head.audioService.load()
        playback.set(PlaybackService.Live(segs))
        loadState.set(LoadState.Ready(segs))
      case Failure(err) =>
        println(s"SegmentLoader failed: ${err.getMessage}")
        println(err.getStackTrace.mkString("\n"))
        loadState.set(LoadState.Failed(err.getMessage))
    }

    // ── Reload audio when segment index changes ──────────────────────────────
    def onAyahBoundary(ayahNumber: Int, endMs: Double): Unit =
      session.scoreAyah(ayahNumber, endMs)
    def onComplete(finalMs: Double): Unit =
      session.scoreRemaining(allWordsNow())
    // ── Playback controls ────────────────────────────────────────────────────
    def startOrToggle(): Unit =
      loadState.now() match
        case LoadState.Ready(segs) =>
          playback.now().playState.now() match
            case PlayState.Idle =>
              playback
                .now()
                .start(
                  () => masking.maskedWords.now(),
                  onAyahBoundary,
                  onComplete
                )

            case _ => playback.now().toggle()
        case _ => ()
    def submitSession(): Unit =
      session.scoreRemaining(allWordsNow())
      val result = session.buildResult(
        quarterHizbId,
        masking.clozeLevel.now(),
        sessionStartMs
      )
      session.sessionDone.set(true)
      playback.now().dispose()
      AppBus.currentPage.set(AppPage.SessionComplete(quarterHizbId, result))
    // ── Render ───────────────────────────────────────────────────────────────
    val island = div(
      height("100vh"),
      display("flex"),
      flexDirection("column"),
      backgroundColor("#0f1117"),
      color("#e8eaf0"),
      fontFamily("'Segoe UI', system-ui, sans-serif"),
      onUnmountCallback { _ =>
        playback.now().dispose()
      },
      child <-- loadState.signal.map { x =>
        PlayerView.header(
          loadState = loadState.signal,
          tapSummary = session.tapSummarySignal,
          quarterHizbId = quarterHizbId,
          onBack = () => AppBus.currentPage.set(AppPage.Home)
        )
      },
      child <-- loadState.signal.map { x =>
        PlayerView.verseArea(
          loadState = loadState.signal,
          maskedWords = masking.maskedWords.signal,
          revealedWords = playback.now().revealedWords.signal,
          activeWordIdx = playback.now().activeWordIdx.signal
        )
      },
      child <-- loadState.signal.map { x =>
        PlayerView.bottomControls(
          playState = playback.now().playState.signal,
          loadState = loadState.signal,
          clozeLevel = masking.clozeLevel.signal,
          currentTimeMs = playback.now().currentTimeMs.signal,
          currentSeg = currentSegmentSignal,
          onTogglePlay = () => startOrToggle(),
          onSeek = ratio => {
            ()
          }, // currentSegNow().foreach(playback.seekRatio(ratio, _)),
          onClozeDown = () => masking.clozeLevel.update(ClozeLevel.decrement),
          onClozeUp = () => masking.clozeLevel.update(ClozeLevel.increment),
          onSubmit = () => submitSession()
        )
      }
    )
    render(el, island)
