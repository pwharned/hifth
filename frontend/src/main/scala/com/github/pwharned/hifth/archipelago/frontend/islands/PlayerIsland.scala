package com.github.pwharned.hifth.frontend.islands
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import org.scalajs.dom.{HTMLAudioElement, KeyboardEvent}
import scala.scalajs.js
import scala.scalajs.js.annotation.JSExportTopLevel
import scala.concurrent.{Future, ExecutionContext}
import scala.scalajs.concurrent.JSExecutionContext.Implicits.queue
import com.github.plokhotnyuk.jsoniter_scala.core.*
import com.github.pwharned.hifth.frontend.{AppBus, AppPage}
import com.github.pwharned.hifth.frontend.player.*
import com.github.pwharned.hifth.shared.domain.*
import com.github.pwharned.hifth.shared.domain.StudyPhase.*
import com.github.pwharned.hifth.shared.protocol.ClientMessage

import com.github.pwharned.hifth.shared.domain.SessionResult
import com.github.pwharned.hifth.frontend.Styles.*

object PlayerIsland:
  // ── Constants ───────────────────────────────────────────────────────────────
  private val DelayedRevealOffsetMs = 400.0
  // ── Player Load State ────────────────────────────────────────────────────────
  private enum LoadState:
    case Loading
    case Failed(msg: String)
    case Ready(segments: Vector[PlayerSegment])
  // ── Session State ────────────────────────────────────────────────────────────
  private enum PlayState:
    case Idle
    case Playing
    case Paused
  @JSExportTopLevel("mountPlayer", moduleID = "player")
  def mount(el: dom.Element, quarterHizbId: Int): Unit =
    // ── Derive initial cloze from SRS state ─────────────────────────────────
    val initialCloze: ClozeLevel =
      AppBus.srsEntries.now().get(quarterHizbId).map(_.phase) match
        case Some(Studying(level)) => level
        case Some(_: Reviewing)    => ClozeLevel.L95
        case _                     => ClozeLevel.L0
    // ── Mutable audio element (lives outside Laminar reactive graph) ─────────
    val audioEl: HTMLAudioElement =
      dom.document.createElement("audio").asInstanceOf[HTMLAudioElement]
    // ── RAF handle ───────────────────────────────────────────────────────────
    var rafHandle: Option[Double] = None
    // ── Reactive State ────────────────────────────────────────────────────────
    val loadState: Var[LoadState] = Var(LoadState.Loading)
    val playState: Var[PlayState] = Var(PlayState.Idle)
    val currentTimeMs: Var[Double] = Var(0.0)
    val activeWordIdx: Var[Option[WordIndex]] = Var(None)
    val revealedWords: Var[Set[WordIndex]] = Var(Set.empty)
    val clozeLevel: Var[ClozeLevel] = Var(initialCloze)
    val maskedWords: Var[Set[WordIndex]] = Var(Set.empty)
    val tapRecords: Var[Vector[AyahTapRecord]] = Var(Vector.empty)
    val lastTapMs: Var[Option[Double]] = Var(None)
    val currentSegIdx: Var[Int] = Var(0)
    val sessionDone: Var[Boolean] = Var(false)
    // ── Derived signals ───────────────────────────────────────────────────────
    // All words across all segments, in order
    val allWordsSignal: Signal[Vector[PlayerWord]] =
      loadState.signal.map:
        case LoadState.Ready(segs) => segs.flatMap(_.words)
        case _                     => Vector.empty
    // Current segment
    val currentSegmentSignal: Signal[Option[PlayerSegment]] =
      loadState.signal
        .combineWith(currentSegIdx.signal)
        .map:
          case (LoadState.Ready(segs), idx) => segs.lift(idx)
          case _                            => None
    // Ayah list for tap tracking (one tap opportunity per Ayah)
    val ayahsSignal: Signal[Vector[Int]] =
      allWordsSignal.map(_.map(_.ayah).distinct)
    // ── Load pipeline ─────────────────────────────────────────────────────────
    def fetchAlignmentJson(surahNumber: Int): Future[RawAlignmentFile] =
      val url = AudioUrls.alignmentJsonUrl(surahNumber)
      dom
        .fetch(url)
        .toFuture
        .flatMap(_.text().toFuture)
        .map(text => readFromString[RawAlignmentFile](text))
    def buildSegments(
        segments: Vector[Segment] // (surahNumber, startAyah, endAyah)
    ): Future[Vector[PlayerSegment]] =
      Future.sequence(
        segments.zipWithIndex.map { case (seg, segIdx) =>
          fetchAlignmentJson(seg.surahNumber).map { raw =>
            val filtered = raw.words
              .filter(w => w.ayah >= seg.startAyah && w.ayah <= seg.endAyah)
              .zipWithIndex
              .map { case (w, localIdx) =>
                PlayerWord(
                  globalIndex =
                    WordIndex(segIdx, localIdx), // stable global key
                  seg.segIdx,
                  surah = w.surah,
                  ayah = w.ayah,
                  position = w.position,
                  text = w.text,
                  startMs = w.start_ms,
                  endMs = w.end_ms
                )
              }
              .toVector
            val seekStartMs =
              filtered.headOption.map(_.startMs).getOrElse(0.0)
            val stopAtMs = filtered.lastOption.map(_.endMs).getOrElse(0.0)
            PlayerSegment(
              segIdx = segIdx,
              surahNumber = seg.surahNumber,
              audioUrl = AudioUrls.forSurah(seg.surahNumber),
              seekStartMs = seekStartMs,
              stopAtMs = stopAtMs,
              words = filtered
            )
          }
        }
      )
    // Kick off load
    val qhSegments =
      QuranData.segmentsForQH(quarterHizbId)
    buildSegments(qhSegments).foreach { segs =>
      loadState.set(LoadState.Ready(segs))
      // Compute initial masking

      val segIndices = segs.flatMap(x =>
        MaskEngine
          .maskedIndices(x.words.size, initialCloze, quarterHizbId)
          .map(y => WordIndex(x.segIdx, y))
      )
      maskedWords.set(
        segIndices.toSet
      )
    }
    // ── Recompute masking when cloze level changes ────────────────────────────
    val _ = clozeLevel.signal
      .combineWith(allWordsSignal)
      .foreach { (level, words) =>
        val indices = words.groupBy(x => x.segment).flatMap { (idx, y) =>
          MaskEngine
            .maskedIndices(y.size, level, seed = quarterHizbId)
            .map(WordIndex(idx, _))
        }
        maskedWords.set(
          indices.toSet
        )
        // Reset revealed set when cloze level changes mid-session
        revealedWords.set(Set.empty)
      }(using unsafeWindowOwner)
    // ── Audio segment loading ─────────────────────────────────────────────────
    def loadSegment(seg: PlayerSegment): Unit =
      audioEl.src = seg.audioUrl
      audioEl.currentTime = seg.seekStartMs / 1000.0
      audioEl.load()
    // Watch for segment changes
    val _ = currentSegmentSignal.foreach(x =>
      x match

        case Some(seg) => loadSegment(seg)
        case None      => ()
    )(using unsafeWindowOwner)
    // ── RAF loop ──────────────────────────────────────────────────────────────
    def rafTick(timestamp: Double): Unit =
      val currentSeg = loadState.now() match
        case LoadState.Ready(segs) => segs.lift(currentSegIdx.now())
        case _                     => None
      currentSeg.foreach { seg =>
        val nowMs = audioEl.currentTime * 1000.0
        // Check if this segment has ended
        if nowMs >= seg.stopAtMs then
          val nextIdx = currentSegIdx.now() + 1
          loadState.now() match
            case LoadState.Ready(segs) if nextIdx < segs.length =>
              currentSegIdx.set(nextIdx)
              audioEl.play()
            case _ =>
              // All segments complete
              playState.set(PlayState.Idle)
              evaluateRemainingAyahs(nowMs)
              rafHandle = None
              return
        else
          currentTimeMs.set(nowMs)
          // Update active word
          val words = seg.words
          val active =
            words.indexWhere(w => nowMs >= w.startMs && nowMs < w.endMs)
          activeWordIdx.set(
            if active >= 0 then Some(words(active).globalIndex) else None
          )
          // Reveal words whose delayed window has passed
          val newReveals = words
            .filter(w => nowMs >= w.startMs + DelayedRevealOffsetMs)
            .map(_.globalIndex)
            .toSet
          revealedWords.update(_ ++ newReveals)
          // Evaluate tap at Ayah boundary
          if active > 0 then
            val currAyah = words(active).ayah
            val prevAyah = words(active - 1).ayah
            if currAyah != prevAyah then
              // We just crossed an Ayah boundary - score the tap for prevAyah
              val lastWordOfPrevAyah = words
                .filter(_.ayah == prevAyah)
                .lastOption
              lastWordOfPrevAyah.foreach { lw =>
                val alreadyScored =
                  tapRecords.now().exists(_.ayahNumber == prevAyah)
                if !alreadyScored then
                  val record =
                    TapScorer.evaluate(prevAyah, lw.endMs, lastTapMs.now())
                  tapRecords.update(_ :+ record)
                  lastTapMs.set(None)
              }
      }
      rafHandle = Some(dom.window.requestAnimationFrame(_ => rafTick(0)))
    def evaluateRemainingAyahs(finalMs: Double): Unit =
      val allWords = loadState.now() match
        case LoadState.Ready(segs) => segs.flatMap(_.words)
        case _                     => Vector.empty
      val scoredAyahs = tapRecords.now().map(_.ayahNumber).toSet
      allWords
        .map(_.ayah)
        .distinct
        .filterNot(scoredAyahs.contains)
        .foreach { ayah =>
          val lastWord = allWords.filter(_.ayah == ayah).lastOption
          lastWord.foreach { lw =>
            tapRecords.update(
              _ :+ TapScorer.evaluate(ayah, lw.endMs, lastTapMs.now())
            )
          }
        }
    // ── Playback controls ─────────────────────────────────────────────────────
    def play(): Unit =
      loadState.now() match
        case LoadState.Ready(segs) =>
          val segIdx = currentSegIdx.now()
          segs.lift(segIdx).foreach { seg =>
            if audioEl.src != dom.window.location.origin + seg.audioUrl then
              loadSegment(seg)
            audioEl.play()
            playState.set(PlayState.Playing)
            rafHandle.foreach(h => dom.window.cancelAnimationFrame(h.toInt))
            rafHandle = Some(dom.window.requestAnimationFrame(_ => rafTick(0)))
          }
        case _ => ()
    def pause(): Unit =
      audioEl.pause()
      playState.set(PlayState.Paused)
      rafHandle.foreach(h => dom.window.cancelAnimationFrame(h.toInt))
      rafHandle = None
    def togglePlay(): Unit =
      playState.now() match
        case PlayState.Playing => pause()
        case _                 => play()
    // ── Tap handler ───────────────────────────────────────────────────────────
    def recordTap(): Unit =
      if playState.now() == PlayState.Playing then
        lastTapMs.set(Some(audioEl.currentTime * 1000.0))
    // Keyboard tap - spacebar
    val sessionStartMs: Double = org.scalajs.dom.window.performance.now()
    val keyHandler: js.Function1[KeyboardEvent, Unit] = (e: KeyboardEvent) =>
      if e.code == "Space" then
        e.preventDefault()
        if playState.now() == PlayState.Playing then recordTap()
        else togglePlay()
    dom.window.addEventListener("keydown", keyHandler)
    // ── Submit session ────────────────────────────────────────────────────────
    def submitSession(): Unit =
      val result = SessionResult(
        quarterHizbId = quarterHizbId,
        clozeAchieved = clozeLevel.now(),
        tapPerfect = tapRecords.now().count(_.result == TapResult.Perfect),
        tapGood = tapRecords.now().count(_.result == TapResult.Good),
        tapMiss = tapRecords.now().count(_.result == TapResult.Miss),
        durationMs = (org.scalajs.dom.window.performance.now() -
          sessionStartMs).toLong
      )
      sessionDone.set(true)
      AppBus.currentPage.set(AppPage.SessionComplete(quarterHizbId, result))

    // ── Tap score summary signal ──────────────────────────────────────────────
    val tapSummarySignal: Signal[String] =
      tapRecords.signal.map { records =>
        if records.isEmpty then ""
        else
          val perfect = records.count(_.result == TapResult.Perfect)
          val good = records.count(_.result == TapResult.Good)
          val miss = records.count(_.result == TapResult.Miss)
          s"✦ $perfect  ✔ $good  ✗ $miss"
      }
    // ── Word rendering helper ─────────────────────────────────────────────────
    def renderWord(word: PlayerWord): HtmlElement =
      val isMasked = maskedWords.signal.map(_.contains(word.globalIndex))
      val isRevealed = revealedWords.signal.map(_.contains(word.globalIndex))
      val isActive = activeWordIdx.signal.map(_.contains(word.globalIndex))
      // Visible state: masked AND not yet revealed → show placeholder
      val showPlaceholder: Signal[Boolean] =
        isMasked.combineWith(isRevealed).map { (masked, revealed) =>
          masked && !revealed
        }
      // Placeholder: underscore blocks scaled to word length
      val placeholderText: String =
        "_" * math.max(1, word.text.length / 2)
      span(
        // Dynamic text content
        child.text <-- showPlaceholder.map(hidden =>
          if hidden then placeholderText else word.text
        ),
        // Styling
        display("inline-block"),
        padding("3px 5px"),
        borderRadius("5px"),
        fontFamily("'Traditional Arabic', 'Scheherazade New', 'Amiri', serif"),
        fontSize("26px"),
        transition("all 0.15s"),
        cursor("default"),
        // Active word highlight
        backgroundColor <-- isActive.map(a =>
          if a then "#4f8ef720" else "transparent"
        ),
        color <-- showPlaceholder
          .combineWith(isActive)
          .map:
            case (_, true)  => "#4f8ef7"
            case (true, _)  => "#3b3f50"
            case (false, _) => "#f0f2f8",
        fontWeight <-- isActive.map(a => if a then "bold" else "normal"),
        // Tap on word also records a tap
        onClick --> (_ => recordTap())
      )
    // ── UI Assembly ───────────────────────────────────────────────────────────
    val island = div(
      height("100vh"),
      display("flex"),
      flexDirection("column"),
      backgroundColor("#0f1117"),
      color("#e8eaf0"),
      fontFamily("'Segoe UI', system-ui, sans-serif"),
      onUnmountCallback { _ =>
        // Cleanup
        dom.window.removeEventListener("keydown", keyHandler)
        rafHandle.foreach(h => dom.window.cancelAnimationFrame(h.toInt))
        audioEl.pause()
        audioEl.src = ""
      },
      // ── Header bar ────────────────────────────────────────────────────────
      div(
        display("flex"),
        alignItems("center"),
        justifyContent("space-between"),
        padding("16px 20px"),
        borderBottom("1px solid #2e3140"),
        flexShrink("0"),
        // Back button
        button(
          "← Back",
          onClick --> (_ => AppBus.currentPage.set(AppPage.Home)),
          backgroundColor("transparent"),
          border("1px solid #2e3140"),
          borderRadius("7px"),
          color("#8b90a0"),
          padding("8px 14px"),
          cursor("pointer"),
          fontSize("13px")
        ),
        // Title
        div(
          textAlign("center"),
          div(
            s"Quarter-Hizb $quarterHizbId",
            fontSize("16px"),
            fontWeight("600")
          ),
          div(
            child.text <-- loadState.signal.map:
              case LoadState.Loading     => "Loading..."
              case LoadState.Failed(msg) => s"Error: $msg"
              case LoadState.Ready(segs) =>
                segs.map(s => s"Surah ${s.surahNumber}").mkString(" + "),
            fontSize("12px"),
            color("#8b90a0"),
            marginTop("2px")
          )
        ),
        // Tap score summary
        span(
          child.text <-- tapSummarySignal,
          fontSize("13px"),
          color("#8b90a0"),
          fontVariantNumeric("tabular-nums")
        )
      ),
      // ── Verse display area ─────────────────────────────────────────────────
      div(
        flex("1"),
        overflowY("auto"),
        padding("24px 32px"),
        direction("rtl"),
        lineHeight("3.0"),
        textAlign("justify"),
        // Loading / error states
        child <-- loadState.signal.map:
          case LoadState.Loading =>
            div(
              display("flex"),
              alignItems("center"),
              justifyContent("center"),
              height("100%"),
              color("#8b90a0"),
              fontSize("14px"),
              "Loading alignment data..."
            )
          case LoadState.Failed(msg) =>
            div(
              display("flex"),
              alignItems("center"),
              justifyContent("center"),
              height("100%"),
              color("#f5a623"),
              fontSize("14px"),
              s"Failed to load: $msg"
            )
          case LoadState.Ready(segs) =>
            // Render all segments with Ayah markers
            div(
              segs.flatMap { seg =>
                seg.words
                  .groupBy(_.ayah)
                  .toVector
                  .sortBy(_._1)
                  .flatMap { (ayahNum, ayahWords) =>
                    val wordSpans = ayahWords.map(renderWord)
                    val ayahMarker = span(
                      display("inline-flex"),
                      alignItems("center"),
                      justifyContent("center"),
                      width("28px"),
                      height("28px"),
                      borderRadius("50%"),
                      border("1px solid #6b7280"),
                      color("#6b7280"),
                      fontSize("12px"),
                      fontFamily("system-ui, sans-serif"),
                      margin("0 4px"),
                      verticalAlign("middle"),
                      ayahNum.toString
                    )
                    wordSpans :+ ayahMarker
                  }
              }
            )
      ),
      // ── Bottom controls bar ────────────────────────────────────────────────
      div(
        borderTop("1px solid #2e3140"),
        backgroundColor("#1a1d27"),
        padding("16px 20px"),
        display("flex"),
        flexDirection("column"),
        gap("14px"),
        flexShrink("0"),
        // ── Progress row ───────────────────────────────────────────────────
        div(
          display("flex"),
          alignItems("center"),
          gap("12px"),
          direction("ltr"),
          // Play / Pause button
          button(
            child.text <-- playState.signal.map:
              case PlayState.Playing => "⏸"
              case _                 => "▶",
            onClick --> (_ => togglePlay()),
            disabled <-- loadState.signal.map:
              case LoadState.Ready(_) => false
              case _                  => true,
            width("42px"),
            height("42px"),
            borderRadius("50%"),
            backgroundColor("#4f8ef7"),
            border("none"),
            color("white"),
            fontSize("16px"),
            cursor("pointer"),
            flexShrink("0")
          ),
          // Progress bar
          div(
            flex("1"),
            height("5px"),
            backgroundColor("#21242f"),
            borderRadius("3px"),
            cursor("pointer"),
            position("relative"),
            onClick.mapToEvent --> Observer[dom.MouseEvent](e =>
              val rect = e.currentTarget
                .asInstanceOf[dom.HTMLElement]
                .getBoundingClientRect()
              val ratio = (e.clientX - rect.left) / rect.width
              loadState.now() match
                case LoadState.Ready(segs) =>
                  segs.lift(currentSegIdx.now()).foreach { seg =>
                    val rangeMs = seg.stopAtMs - seg.seekStartMs
                    audioEl.currentTime =
                      (seg.seekStartMs + ratio * rangeMs) / 1000.0
                  }
                case _ => ()
            ),
            div(
              height("100%"),
              backgroundColor("#4f8ef7"),
              borderRadius("3px"),
              pointerEvents("none"),
              width <-- currentTimeMs.signal
                .combineWith(currentSegmentSignal)
                .map { (ms, seg) =>
                  seg.fold("0%") { s =>
                    val range = s.stopAtMs - s.seekStartMs
                    if range <= 0 then "0%"
                    else
                      s"${((ms - s.seekStartMs) / range * 100).min(100).max(0).toInt}%"
                  }
                }
            )
          ),
          // Tap instruction
          div(
            "Tap [Space] at each Ayah end",
            fontSize("12px"),
            color("#8b90a0"),
            flexShrink("0")
          )
        ),
        // ── Cloze controls row ─────────────────────────────────────────────
        div(
          display("flex"),
          alignItems("center"),
          justifyContent("space-between"),
          direction("ltr"),
          div(
            display("flex"),
            alignItems("center"),
            gap("12px"),
            span("Cloze:", fontSize("13px"), color("#8b90a0")),
            button(
              "−",
              onClick --> (_ => clozeLevel.update(ClozeLevel.decrement)),
              disabled <-- clozeLevel.signal.map(_ == ClozeLevel.L0),
              width("32px"),
              height("32px"),
              borderRadius("6px"),
              backgroundColor("#21242f"),
              border("1px solid #2e3140"),
              color("#e8eaf0"),
              fontSize("16px"),
              cursor("pointer")
            ),
            span(
              child.text <-- clozeLevel.signal.map(l => s"${l.percent}%"),
              minWidth("42px"),
              textAlign("center"),
              fontSize("15px"),
              fontWeight("600"),
              color <-- clozeLevel.signal.map(l =>
                if l.percent >= 95 then "#3ecf8e"
                else if l.percent >= 50 then "#4f8ef7"
                else "#8b90a0"
              )
            ),
            button(
              "+",
              onClick --> (_ => clozeLevel.update(ClozeLevel.increment)),
              disabled <-- clozeLevel.signal.map(_ == ClozeLevel.L95),
              width("32px"),
              height("32px"),
              borderRadius("6px"),
              backgroundColor("#21242f"),
              border("1px solid #2e3140"),
              color("#e8eaf0"),
              fontSize("16px"),
              cursor("pointer")
            ),
            // Phase indicator
            span(
              child.text <-- clozeLevel.signal.map(l =>
                if l.percent >= 95 then "→ Will enter Review"
                else s"→ Studying"
              ),
              fontSize("12px"),
              color <-- clozeLevel.signal.map(l =>
                if l.percent >= 95 then "#3ecf8e" else "#8b90a0"
              )
            )
          ),
          // Submit session button
          button(
            "Submit Session",
            onClick --> (_ => submitSession()),
            disabled <-- loadState.signal.map:
              case LoadState.Ready(_) => false
              case _                  => true,
            padding("9px 18px"),
            backgroundColor("#4f8ef7"),
            border("none"),
            borderRadius("7px"),
            color("white"),
            fontSize("13px"),
            fontWeight("600"),
            cursor("pointer")
          )
        )
      )
    )
    render(el, island)
