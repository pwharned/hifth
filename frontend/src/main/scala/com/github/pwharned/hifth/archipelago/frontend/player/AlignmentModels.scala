package com.github.pwharned.hifth.frontend.player
import com.github.plokhotnyuk.jsoniter_scala.macros.*
import com.github.plokhotnyuk.jsoniter_scala.core.*
// ── Raw alignment JSON structures ─────────────────────────────────────────────
// Mirrors the schema produced by the Python alignment pipeline.
// snake_case field names match the JSON keys directly.
case class RawWordToken(
    index: Int,
    surah: Int,
    ayah: Int,
    position: Int,
    text: String,
    start_ms: Double,
    end_ms: Double,
    score: Double
)
object RawWordToken:
  given JsonValueCodec[RawWordToken] = JsonCodecMaker.make
case class RawAlignmentFile(
    surah_number: Int,
    total_words: Int,
    audio_duration_ms: Double,
    words: List[RawWordToken]
)
object RawAlignmentFile:
  given JsonValueCodec[RawAlignmentFile] = JsonCodecMaker.make
// ── Processed player token ────────────────────────────────────────────────────
// Filtered and enriched view used exclusively by the player.
// startMs/endMs are relative to the start of the surah audio file.

opaque type WordIndex = Int
object WordIndex:
  def apply(i: Int): WordIndex = i
  def apply(segIdx: Int, localIdx: Int): WordIndex =
    segIdx * 10000 + localIdx

case class PlayerWord(
    globalIndex: WordIndex, // unique index across all segments in this QH session
    segment: Int,
    surah: Int,
    ayah: Int,
    position: Int,
    text: String,
    startMs: Double,
    endMs: Double
)
// One audio segment per Surah spanned by this Quarter-Hizb
case class PlayerSegment(
    segIdx: Int,
    surahNumber: Int,
    audioUrl: String,
    seekStartMs: Double, // seek position at start of this segment
    stopAtMs: Double, // stop playback at this position
    words: Vector[PlayerWord], // filtered to this segment's ayah range
    audioService: AudioService
)

object PlayerSegment:
  def apply(
      segIdx: Int,
      surahNumber: Int,
      audioUrl: String,
      seekStartMs: Double, // seek position at start of this segment
      stopAtMs: Double, // stop playback at this position
      words: Vector[PlayerWord] // filtered to this segment's ayah range
  ): PlayerSegment = new PlayerSegment(
    segIdx,
    surahNumber,
    audioUrl,
    seekStartMs,
    stopAtMs,
    words,
    AudioService.Live(audioUrl, seekStartMs)
  )

// Tap scoring windows
enum TapResult:
  case Perfect // delta < 500ms
  case Good // delta 500-1500ms
  case Miss // delta > 1500ms or no tap before next Ayah
case class AyahTapRecord(
    ayahNumber: Int,
    expectedMs: Double, // end_ms of last word in Ayah
    actualMs: Double, // -1.0 if no tap recorded
    result: TapResult
)
