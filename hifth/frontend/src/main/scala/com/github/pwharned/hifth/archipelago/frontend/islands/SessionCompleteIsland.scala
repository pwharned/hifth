package com.github.pwharned.hifth.frontend.islands
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import scala.scalajs.js.annotation.JSExportTopLevel
import com.github.pwharned.hifth.frontend.{AppBus, AppPage}
import com.github.pwharned.hifth.frontend.player.{QueueBuilder}
import com.github.pwharned.hifth.shared.domain.*
import com.github.pwharned.hifth.shared.domain.StudyPhase.*
import com.github.pwharned.hifth.shared.protocol.ClientMessage
object SessionCompleteIsland:
  def mountInternal(
      el: dom.Element,
      quarterHizbId: Int,
      result: SessionResult
  ): Unit =
    mountImpl(el, quarterHizbId, result)
  private def mountImpl(
      el: dom.Element,
      quarterHizbId: Int,
      result: SessionResult
  ): Unit =
    val surahNum = QuranData.primarySurahForQH(quarterHizbId)
    val surahName = QuranData.surahName(surahNum)
    // Duration display
    val minutes = (result.durationMs / 60000).toInt
    val seconds = ((result.durationMs % 60000) / 1000).toInt
    val durationText =
      if minutes > 0 then s"${minutes} min ${seconds}s"
      else s"${seconds}s"
    // Cloze prompt message
    val nextCloze = ClozeLevel.increment(result.clozeAchieved)
    val clozePrompt =
      if result.clozeAchieved == ClozeLevel.L95 then None
      else if result.clozeAchieved == nextCloze then None
      else
        Some(
          (
            "Ready to push further?",
            s"You reached ${result.clozeAchieved.percent}% cloze. " +
              s"Next session try ${nextCloze.percent}% — one step closer to full recall."
          )
        )
    // Phase label for the complete screen
    val phaseLabel: Signal[String] =
      AppBus.srsEntries.signal.map(_.get(quarterHizbId).map(_.phase) match
        case Some(_: Reviewing) =>
          s"In review · next due after session interval"
        case Some(Studying(c)) => s"Studying · ${c.percent}% cloze"
        case _                 => "Not started")
    // Mini river: percentage breakdown for this Surah
    val qhForSurah = (1 to 240).filter { qhId =>
      QuranData.primarySurahForQH(qhId) == surahNum
    }
    val riverSegments: Signal[Vector[(String, Double)]] =
      AppBus.srsEntries.signal.map { entries =>
        import scala.scalajs.js
        val nowMs = js.Date.now().toLong
        val total = qhForSurah.size.toDouble
        var reviewed = 0.0
        var due = 0.0
        var studying = 0.0
        qhForSurah.foreach { qhId =>
          entries.get(qhId).map(_.phase) match
            case Some(r: Reviewing) =>
              if r.nextReviewMs <= nowMs then due += 100.0 / total
              else reviewed += 100.0 / total
            case Some(_: Studying) => studying += 100.0 / total
            case _                 => ()
        }
        Vector(
          ("seg seg-reviewed", reviewed),
          ("seg seg-due", due),
          ("seg seg-studying", studying)
        ).filter(_._2 > 0)
      }
    // Submit the session to the backend
    AppBus.outgoing.emit(
      ClientMessage.SubmitSession(
        quarterHizbId = quarterHizbId,
        achievedClozePercent = result.clozeAchieved.percent,
        tapPerfect = result.tapPerfect,
        tapGood = result.tapGood,
        tapMiss = result.tapMiss,
        durationMs = result.durationMs
      )
    )
    // Next section in queue (skip current)
    val nextSection: Signal[Option[Int]] =
      AppBus.srsEntries.signal.map { entries =>
        import scala.scalajs.js
        val nowMs = js.Date.now().toLong
        val queue = QueueBuilder.build(entries, nowMs)
        queue
          .find(_.quarterHizbId != quarterHizbId)
          .map(_.quarterHizbId)
      }
    val island = div(
      cls("app screen active"),
      // Top bar
      div(
        cls("top-bar"),
        div(cls("wordmark"), "h", span("i"), "fth"),
        div()
      ),
      // Complete body
      div(
        cls("complete-body"),
        div(cls("complete-icon"), "✦"),
        div(cls("complete-title"), "Session Complete"),
        div(
          cls("complete-subtitle"),
          s"$surahName · ${result.tapPerfect + result.tapGood + result.tapMiss} Ayahs · $durationText"
        ),
        // Stats row
        div(
          cls("complete-stats"),
          div(
            cls("complete-stat"),
            div(
              cls("complete-stat-value"),
              s"${result.clozeAchieved.percent}%"
            ),
            div(cls("complete-stat-label"), "Cloze level")
          ),
          div(
            cls("complete-stat"),
            div(cls("complete-stat-value"), result.tapPerfect.toString),
            div(cls("complete-stat-label"), "Taps perfect")
          ),
          div(
            cls("complete-stat"),
            div(cls("complete-stat-value"), durationText),
            div(cls("complete-stat-label"), "Duration")
          )
        ),
        // Cloze prompt (conditional)
        clozePrompt.map { (title, body) =>
          div(
            cls("cloze-prompt"),
            div(cls("cloze-prompt-title"), title),
            div(cls("cloze-prompt-body"), body)
          )
        },
        // Mini river for this Surah
        div(
          cls("complete-river"),
          div(cls("complete-river-label"), surahName),
          div(
            cls("complete-river-bar"),
            children <-- riverSegments.map(segs =>
              segs.map { (segCls, pct) =>
                div(
                  cls(segCls),
                  styleAttr(s"width: ${pct.toInt}%")
                )
              }
            )
          ),
          div(
            cls("complete-next-review"),
            child.text <-- phaseLabel
          )
        ),
        // Streak update
        div(
          cls("streak-update"),
          "🔥 Streak: ",
          child.text <-- AppBus.streakState.signal.map(
            _.fold("0")(_.count.toString)
          ),
          " days"
        ),
        // Actions
        div(
          cls("complete-actions"),
          button(
            cls("modal-btn modal-btn-cancel"),
            "Home",
            onClick --> (_ => AppBus.currentPage.set(AppPage.Home))
          ),
          button(
            cls("modal-btn modal-btn-confirm"),
            child.text <-- nextSection.map(n =>
              if n.isDefined then "Next Section →" else "Back to Home"
            ),
            onClick.compose(_.withCurrentValueOf(nextSection)) --> {
              (_, next) =>
                next match
                  case Some(qhId) =>
                    AppBus.currentPage.set(AppPage.Player(qhId))
                  case None => AppBus.currentPage.set(AppPage.Home)
            }
          )
        )
      )
    )
    render(el, island)
