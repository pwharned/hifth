package com.github.pwharned.hifth.frontend.player
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import com.github.pwharned.hifth.shared.domain.*
object PlayerView:
  def word(
      word: PlayerWord,
      maskedWords: Signal[Set[WordIndex]],
      revealedWords: Signal[Set[WordIndex]],
      activeWordIdx: Signal[Option[WordIndex]]
  ): HtmlElement =
    val isMasked = maskedWords.map(_.contains(word.globalIndex))
    val isRevealed = revealedWords.map(_.contains(word.globalIndex))
    val isActive = activeWordIdx.map(_.contains(word.globalIndex))
    val hidden = isMasked.combineWith(isRevealed).map((m, r) => m && !r)
    val placeholder = "_" * math.max(1, word.text.length / 2)
    span(
      child.text <-- hidden.map(if _ then placeholder else word.text),
      display("inline-block"),
      padding("3px 5px"),
      borderRadius("5px"),
      fontFamily("'Traditional Arabic', 'Scheherazade New', 'Amiri', serif"),
      fontSize("26px"),
      transition("all 0.15s"),
      cursor("default"),
      backgroundColor <-- isActive.map(
        if _ then "#4f8ef720" else "transparent"
      ),
      color <-- hidden
        .combineWith(isActive)
        .map:
          case (_, true) => "#4f8ef7"
          case (true, _) => "#3b3f50"
          case _         => "#f0f2f8",
      fontWeight <-- isActive.map(if _ then "bold" else "normal")
    )
  def ayahMarker(ayahNum: Int): HtmlElement =
    span(
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
  def verseArea(
      loadState: Signal[LoadState],
      maskedWords: Signal[Set[WordIndex]],
      revealedWords: Signal[Set[WordIndex]],
      activeWordIdx: Signal[Option[WordIndex]]
  ): HtmlElement =
    div(
      flex("1"),
      overflowY("auto"),
      padding("24px 32px"),
      direction("rtl"),
      lineHeight("3.0"),
      textAlign("justify"),
      child <-- loadState.map:
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
          div(
            segs.flatMap { seg =>
              seg.words
                .groupBy(_.ayah)
                .toVector
                .sortBy(_._1)
                .flatMap { (ayahNum, ayahWords) =>
                  ayahWords.map(w =>
                    word(w, maskedWords, revealedWords, activeWordIdx)
                  )
                    :+ ayahMarker(ayahNum)
                }
            }
          )
    )
  def header(
      loadState: Signal[LoadState],
      tapSummary: Signal[String],
      quarterHizbId: Int,
      onBack: () => Unit
  ): HtmlElement =
    div(
      display("flex"),
      alignItems("center"),
      justifyContent("space-between"),
      padding("16px 20px"),
      borderBottom("1px solid #2e3140"),
      flexShrink("0"),
      button(
        "← Back",
        onClick --> (_ => onBack()),
        backgroundColor("transparent"),
        border("1px solid #2e3140"),
        borderRadius("7px"),
        color("#8b90a0"),
        padding("8px 14px"),
        cursor("pointer"),
        fontSize("13px")
      ),
      div(
        textAlign("center"),
        div(
          s"Quarter-Hizb $quarterHizbId",
          fontSize("16px"),
          fontWeight("600")
        ),
        div(
          child.text <-- loadState.map:
            case LoadState.Loading     => "Loading..."
            case LoadState.Failed(msg) => s"Error: $msg"
            case LoadState.Ready(segs) =>
              segs.map(s => s"Surah ${s.surahNumber}").mkString(" + "),
          fontSize("12px"),
          color("#8b90a0"),
          marginTop("2px")
        )
      ),
      span(
        child.text <-- tapSummary,
        fontSize("13px"),
        color("#8b90a0"),
        styleAttr := "font-variant-numeric: tabular-nums"
      )
    )
  def bottomControls(
      playState: Signal[PlayState],
      loadState: Signal[LoadState],
      clozeLevel: Signal[ClozeLevel],
      currentTimeMs: Signal[Double],
      currentSeg: Signal[Option[PlayerSegment]],
      onTogglePlay: () => Unit,
      onSeek: Double => Unit, // ratio [0,1]; caller resolves segment
      onClozeDown: () => Unit,
      onClozeUp: () => Unit,
      onSubmit: () => Unit
  ): HtmlElement =
    val notReady = loadState.map {
      case LoadState.Ready(_) => false; case _ => true
    }
    div(
      borderTop("1px solid #2e3140"),
      backgroundColor("#1a1d27"),
      padding("16px 20px"),
      display("flex"),
      flexDirection("column"),
      gap("14px"),
      flexShrink("0"),
      // ── Playback row ────────────────────────────────────────────────────────
      div(
        display("flex"),
        alignItems("center"),
        gap("12px"),
        direction("ltr"),
        button(
          child.text <-- playState.map:
            case PlayState.Playing => "⏸"
            case _                 => "▶",
          onClick --> (_ => {
            onTogglePlay()
          }),
          disabled <-- notReady,
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
        // Seek bar
        div(
          flex("1"),
          height("5px"),
          backgroundColor("#21242f"),
          borderRadius("3px"),
          cursor("pointer"),
          position("relative"),
          onClick.mapToEvent --> Observer[dom.MouseEvent] { e =>
            val rect = e.currentTarget
              .asInstanceOf[dom.HTMLElement]
              .getBoundingClientRect()
            val ratio = (e.clientX - rect.left) / rect.width
            onSeek(ratio)
          },
          div(
            height("100%"),
            backgroundColor("#4f8ef7"),
            borderRadius("3px"),
            pointerEvents("none"),
            width <-- currentTimeMs.combineWith(currentSeg).map { (ms, seg) =>
              seg.fold("0%") { s =>
                val range = s.stopAtMs - s.seekStartMs
                if range <= 0 then "0%"
                else
                  s"${((ms - s.seekStartMs) / range * 100).min(100).max(0).toInt}%"
              }
            }
          )
        ),
        div(
          "Tap [Space] at each Ayah end",
          fontSize("12px"),
          color("#8b90a0"),
          flexShrink("0")
        )
      ),
      // ── Cloze row ───────────────────────────────────────────────────────────
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
            onClick --> (_ => onClozeDown()),
            disabled <-- clozeLevel.map(_ == ClozeLevel.L0),
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
            child.text <-- clozeLevel.map(l => s"${l.percent}%"),
            minWidth("42px"),
            textAlign("center"),
            fontSize("15px"),
            fontWeight("600"),
            color <-- clozeLevel.map(l =>
              if l.percent >= 95 then "#3ecf8e"
              else if l.percent >= 50 then "#4f8ef7"
              else "#8b90a0"
            )
          ),
          button(
            "+",
            onClick --> (_ => onClozeUp()),
            disabled <-- clozeLevel.map(_ == ClozeLevel.L95),
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
            child.text <-- clozeLevel.map(l =>
              if l.percent >= 95 then "→ Will enter Review" else "→ Studying"
            ),
            fontSize("12px"),
            color <-- clozeLevel.map(l =>
              if l.percent >= 95 then "#3ecf8e" else "#8b90a0"
            )
          )
        ),
        button(
          "Submit Session",
          onClick --> (_ => onSubmit()),
          disabled <-- notReady,
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
