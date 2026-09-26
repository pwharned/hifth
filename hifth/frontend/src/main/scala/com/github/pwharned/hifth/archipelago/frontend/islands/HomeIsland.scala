package com.github.pwharned.hifth.frontend.islands
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import scala.scalajs.js
import scala.scalajs.js.annotation.JSExportTopLevel
import com.github.pwharned.hifth.frontend.{AppBus, AppPage, Styles}
import com.github.pwharned.hifth.frontend.player.{
  QueueBuilder,
  QueueEntry,
  QueueEntryType
}
import com.github.pwharned.hifth.shared.domain.*
import com.github.pwharned.hifth.shared.domain.StudyPhase.*
object HomeIsland:
  // ── Constants ───────────────────────────────────────────────────────────────
  private val TotalSections = 240
  // Called programmatically from AppIsland
  def mountInternal(el: dom.Element): Unit = mountImpl(el)
  @JSExportTopLevel("mountHome", moduleID = "home")
  def mount(el: dom.Element): Unit = mountImpl(el)
  private def mountImpl(el: dom.Element): Unit =
    // ── Derived signals ───────────────────────────────────────────────────────
    val nowMs = js.Date.now().toLong
    val queueSignal: Signal[Vector[QueueEntry]] =
      AppBus.srsEntries.signal.map(entries =>
        QueueBuilder.build(entries, nowMs)
      )
    val solidCount: Signal[Int] =
      AppBus.srsEntries.signal.map(
        _.values.count(e =>
          e.phase match
            case _: Reviewing => true
            case _            => false
        )
      )
    val studyingCount: Signal[Int] =
      AppBus.srsEntries.signal.map(
        _.values.count(e =>
          e.phase match
            case _: Studying => true
            case _           => false
        )
      )
    // ── Hijri date ────────────────────────────────────────────────────────────
    val hijriDate: String =
      val now = new js.Date()
      val hijri = js.Dynamic
        .newInstance(
          js.Dynamic.global.Intl.DateTimeFormat
        )(
          "en-u-ca-islamic-umalqura",
          js.Dynamic.literal(day = "numeric", month = "long", year = "numeric")
        )
        .format(now)
        .asInstanceOf[String]
      val weekday = js.Dynamic
        .newInstance(
          js.Dynamic.global.Intl.DateTimeFormat
        )(
          "en",
          js.Dynamic.literal(weekday = "long")
        )
        .format(now)
        .asInstanceOf[String]
      s"$weekday, $hijri"
    // ── Greeting ──────────────────────────────────────────────────────────────
    val greeting: String =
      val h = new js.Date().getHours()
      if h < 5 then "Good night"
      else if h < 12 then "Good morning"
      else if h < 17 then "Good afternoon"
      else "Good evening"
    // ── Helper: render one queue item row ─────────────────────────────────────
    def renderQueueItem(entry: QueueEntry): HtmlElement =
      val (dotClass, tagText, tagClass) = entry.entryType match
        case QueueEntryType.ReviewOverdue =>
          ("dot-review", "Overdue", "tag-overdue")
        case QueueEntryType.ReviewDue => ("dot-review", "Review", "tag-review")
        case QueueEntryType.Studying  => ("dot-study", "Study", "tag-study")
        case QueueEntryType.New       => ("dot-new", "New", "tag-new")
      div(
        cls("queue-item"),
        div(
          cls("queue-item-left"),
          div(cls(s"queue-type-dot $dotClass")),
          div(
            div(cls("queue-item-name"), entry.surahName),
            div(cls("queue-item-sub"), entry.detail)
          )
        ),
        div(cls(s"queue-item-tag $tagClass"), tagText),
        onClick --> (_ =>
          AppBus.currentPage.set(AppPage.Player(entry.quarterHizbId))
        )
      )
    // ── Helper: render river row ──────────────────────────────────────────────
    def renderRiverRow(
        surahIdx: Int,
        entries: Map[Int, SRSEntry],
        nowMs: Long
    ): HtmlElement =
      val surahNum = surahIdx + 1
      val name = QuranData.surahName(surahNum)
      val ayahCount = QuranData.surahAyahCounts(surahIdx)
      // Find all QH entries that belong to this Surah
      val qhForSurah = (1 to TotalSections).filter { qhId =>
        QuranData.primarySurahForQH(qhId) == surahNum
      }
      val totalQH = qhForSurah.size.toDouble
      var reviewedPct = 0.0
      var duePct = 0.0
      var studyingPct = 0.0
      qhForSurah.foreach { qhId =>
        entries.get(qhId).map(_.phase) match
          case Some(_: Reviewing) =>
            entries(qhId).phase match
              case r: Reviewing if r.nextReviewMs <= nowMs =>
                duePct += 100.0 / totalQH
              case _ =>
                reviewedPct += 100.0 / totalQH
          case Some(_: Studying) =>
            studyingPct += 100.0 / totalQH
          case _ => ()
      }
      div(
        cls("river-row"),
        // Surah name
        div(
          cls("river-name"),
          name,
          title(name)
        ),
        // Bar
        div(
          cls("river-bar-wrap"),
          styleAttr(
            s"flex-grow: $ayahCount; flex-shrink: 1; flex-basis: 24px; min-width: 24px;"
          ),
          onClick --> (_ => {
            // Find first QH for this Surah and open confirmation
            qhForSurah.headOption.foreach { qhId =>
              AppBus.currentPage.set(AppPage.Player(qhId))
            }
          }),

          Vector(
            Option.when(reviewedPct > 0)(
              div(
                cls("seg seg-reviewed"),
                styleAttr(s"width: ${reviewedPct.toInt}%")
              )
            ),
            Option.when(duePct > 0)(
              div(
                cls("seg seg-due"),
                styleAttr(s"width: ${duePct.toInt}%")
              )
            ),
            Option.when(studyingPct > 0)(
              div(
                cls("seg seg-studying"),
                styleAttr(s"width: ${studyingPct.toInt}%")
              )
            )
          ).flatten
          // Segments
        )
      )
    // ── Island ────────────────────────────────────────────────────────────────
    val island = div(
      cls("app"),
      // Top bar
      div(
        cls("top-bar"),
        div(cls("wordmark"), "h", span("i"), "fth"),
        div(cls("connection-dot"))
      ),
      // Scroll body
      div(
        cls("scroll-body"),
        // Greeting
        div(
          cls("greeting-block"),
          div(cls("hijri-date"), hijriDate),
          div(cls("greeting"), greeting)
        ),
        // Streak card
        div(
          cls("streak-card"),
          div(
            cls("streak-left"),
            div(cls("streak-flame"), "🔥"),
            div(
              div(
                cls("streak-count"),
                child.text <-- AppBus.streakState.signal.map(
                  _.fold("0")(_.count.toString)
                )
              ),
              div(cls("streak-label"), "day streak")
            )
          ),
          // Shield indicator
          child <-- AppBus.streakState.signal.map { state =>
            val (icon, label) = state match
              case Some(s) if s.shieldActive    => ("🛡", "Shield active")
              case Some(s) if s.shieldAvailable => ("🛡", "Shield ready")
              case _                            => ("🛡", "No shield")
            div(
              cls("streak-shield"),
              span(cls("shield-icon"), icon),
              label
            )
          }
        ),
        // Summary stats
        div(
          cls("summary-stat"),
          div(
            cls("stat-pill"),
            child.text <-- solidCount.map(n =>
              s"$n of $TotalSections sections solid"
            )
          ),
          div(
            cls("stat-pill"),
            child.text <-- studyingCount.map(n => s"$n in progress")
          )
        ),
        // Today's queue
        div(cls("section-label"), "Today's Session"),
        div(
          cls("queue-card"),
          // Header
          div(
            cls("queue-header"),
            div(
              cls("queue-title"),
              child.text <-- queueSignal.map(q =>
                if q.isEmpty then "Nothing due today"
                else
                  s"${q.size} section${if q.size == 1 then "" else "s"} queued"
              )
            ),
            div(
              cls("est-time"),
              child.text <-- queueSignal.map(q =>
                if q.isEmpty then ""
                else s"~${QueueBuilder.estimatedMinutes(q)} min"
              )
            )
          ),
          // Queue items
          div(
            cls("queue-items"),
            children <-- queueSignal.map(queue =>
              if queue.isEmpty then
                Vector(
                  div(
                    padding("16px"),
                    fontSize("13px"),
                    color("#8b90a0"),
                    direction("ltr"),
                    "All caught up — come back tomorrow or start something new below."
                  )
                )
              else queue.map(renderQueueItem)
            )
          )
        ),
        // Begin button
        button(
          cls("begin-btn"),
          child.text <-- queueSignal.map(q =>
            if q.isEmpty then "Start Something New →"
            else "Begin Today's Session →"
          ),
          disabled <-- AppBus.srsEntries.signal.map(_.isEmpty),
          onClick.compose(_.withCurrentValueOf(queueSignal)) --> { (_, queue) =>
            queue.headOption.foreach { first =>
              AppBus.currentPage.set(AppPage.Player(first.quarterHizbId))
            }
          }
        ),
        // River
        div(cls("section-label"), "Your Quran"),
        div(
          cls("river-container"),
          children <-- AppBus.srsEntries.signal.map { entries =>
            (0 until 114).toVector.map { idx =>
              renderRiverRow(idx, entries, js.Date.now().toLong)
            }
          }
        )
      )
    )
    render(el, island)
