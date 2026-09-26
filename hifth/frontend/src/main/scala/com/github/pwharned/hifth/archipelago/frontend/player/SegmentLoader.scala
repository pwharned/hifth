package com.github.pwharned.hifth.frontend.player
import org.scalajs.dom
import com.github.plokhotnyuk.jsoniter_scala.core.*
import com.github.pwharned.hifth.shared.domain.*
import scala.concurrent.{Future, ExecutionContext}
object SegmentLoader:
  def load(
      segments: Vector[Segment]
  )(using ExecutionContext): Future[Vector[PlayerSegment]] =
    Future.sequence(
      segments.zipWithIndex.map { case (seg, segIdx) =>
        fetchAlignment(seg.surahNumber).map(raw =>
          buildSegment(raw, seg, segIdx)
        )
      }
    )
  private def fetchAlignment(
      surahNumber: Int
  )(using ExecutionContext): Future[RawAlignmentFile] =
    dom
      .fetch(AudioUrls.alignmentJsonUrl(surahNumber))
      .toFuture
      .flatMap(_.text().toFuture)
      .map(text => readFromString[RawAlignmentFile](text))
  private def buildSegment(
      raw: RawAlignmentFile,
      seg: Segment,
      segIdx: Int
  ): PlayerSegment =
    val words = raw.words
      .filter(w => w.ayah >= seg.startAyah && w.ayah <= seg.endAyah)
      .zipWithIndex
      .map { case (w, localIdx) =>
        PlayerWord(
          globalIndex = WordIndex(segIdx, localIdx),
          segment = seg.segIdx,
          surah = w.surah,
          ayah = w.ayah,
          position = w.position,
          text = w.text,
          startMs = w.start_ms,
          endMs = w.end_ms
        )
      }
      .toVector
    PlayerSegment(
      segIdx = segIdx,
      surahNumber = seg.surahNumber,
      audioUrl = AudioUrls.forSurah(seg.surahNumber),
      seekStartMs = words.headOption.map(_.startMs).getOrElse(0.0),
      stopAtMs = words.lastOption.map(_.endMs).getOrElse(0.0),
      words = words
    )
