package com.github.pwharned.hifth.frontend.player
import com.github.pwharned.hifth.shared.domain.*
import com.github.pwharned.hifth.shared.domain.StudyPhase.*
// ── Queue Entry ───────────────────────────────────────────────────────────────
enum QueueEntryType:
  case ReviewOverdue
  case ReviewDue
  case Studying
  case New
case class QueueEntry(
    quarterHizbId: Int,
    entryType: QueueEntryType,
    surahName: String,
    detail: String // human-readable subtitle shown in queue card
)
// ── Queue Builder ─────────────────────────────────────────────────────────────
// Pure frontend logic. Builds today's ordered session queue from SRS state.
//
// Priority order:
//   1. Overdue reviews  (nextReviewMs < nowMs, sorted by most overdue first)
//   2. Due today reviews (nextReviewMs <= end of today)
//   3. In-progress studying (sorted by cloze level descending)
//   4. Next unstarted section (exactly one - the lowest-numbered not started)
//
// Estimated time uses a fixed constant per section.
object QueueBuilder:
  private val MinutesPerSection = 8
  private val MsPerDay = 86400000L
  def build(
      entries: Map[Int, SRSEntry],
      nowMs: Long
  ): Vector[QueueEntry] =
    val endOfTodayMs = nowMs - (nowMs % MsPerDay) + MsPerDay
    val overdue = scala.collection.mutable.ArrayBuffer.empty[(Long, QueueEntry)]
    val dueToday = scala.collection.mutable.ArrayBuffer.empty[QueueEntry]
    val studying = scala.collection.mutable.ArrayBuffer.empty[(Int, QueueEntry)]
    var nextNew = Option.empty[QueueEntry]
    // Walk all 240 slots in order
    (1 to 240).foreach { qhId =>
      val entry =
        entries.getOrElse(qhId, SRSEntry.fresh(qhId, UserId.GlobalUser))
      val name = qhLabel(qhId)
      entry.phase match
        case NotStarted =>
          // Capture only the first unstarted section
          if nextNew.isEmpty then
            nextNew = Some(
              QueueEntry(
                quarterHizbId = qhId,
                entryType = QueueEntryType.New,
                surahName = name,
                detail = "Not yet started"
              )
            )
        case Studying(cloze) =>
          studying += ((
            cloze.percent,
            QueueEntry(
              quarterHizbId = qhId,
              entryType = QueueEntryType.Studying,
              surahName = name,
              detail = s"${cloze.percent}% cloze"
            )
          ))
        case r: Reviewing =>
          if r.nextReviewMs < nowMs then
            val overdueDays = ((nowMs - r.nextReviewMs) / MsPerDay).toInt
            val detail = overdueDays match
              case 0 => "Due today"
              case 1 => "1 day overdue"
              case n => s"$n days overdue"
            overdue += ((
              r.nextReviewMs,
              QueueEntry(
                quarterHizbId = qhId,
                entryType = QueueEntryType.ReviewOverdue,
                surahName = name,
                detail = detail
              )
            ))
          else if r.nextReviewMs <= endOfTodayMs then
            dueToday += QueueEntry(
              quarterHizbId = qhId,
              entryType = QueueEntryType.ReviewDue,
              surahName = name,
              detail = s"Interval: ${r.intervalDays}d"
            )
    }
    // Sort overdue by most overdue first (lowest nextReviewMs first)
    val sortedOverdue = overdue.sortBy(_._1).map(_._2).toVector
    // Sort studying by highest cloze first (closest to graduation)
    val sortedStudying = studying.sortBy(-_._1).map(_._2).toVector
    val queue = sortedOverdue ++
      dueToday.toVector ++
      sortedStudying ++
      nextNew.toVector
    queue
  def estimatedMinutes(queue: Vector[QueueEntry]): Int =
    queue.size * MinutesPerSection
  // Label for a Quarter-Hizb: primary Surah name
  private def qhLabel(quarterHizbId: Int): String =
    val surahNum = QuranData.primarySurahForQH(quarterHizbId)
    QuranData.surahName(surahNum)
