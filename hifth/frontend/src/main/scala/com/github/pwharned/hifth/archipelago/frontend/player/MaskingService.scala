package com.github.pwharned.hifth.frontend.player
import com.raquo.laminar.api.L.*
import com.github.pwharned.hifth.shared.domain.*

/** Owns the cloze level + derived masked-word set. */
trait MaskingService:
  val clozeLevel: Var[ClozeLevel]
  val maskedWords: Var[Set[WordIndex]]

  /** Recompute masking whenever cloze or words change. */
  def watch(allWordsSignal: Signal[Vector[PlayerWord]], seed: Int): Unit
object MaskingService:
  final class Live(initialCloze: ClozeLevel) extends MaskingService:
    val clozeLevel: Var[ClozeLevel] = Var(initialCloze)
    val maskedWords: Var[Set[WordIndex]] = Var(Set.empty)
    def watch(allWordsSignal: Signal[Vector[PlayerWord]], seed: Int): Unit =
      clozeLevel.signal
        .combineWith(allWordsSignal)
        .foreach { (level, words) =>
          val indices =
            words.groupBy(_.segment).flatMap { (segIdx, segWords) =>
              MaskEngine
                .maskedIndices(segWords.size, level, seed)
                .map(WordIndex(segIdx, _))
            }
          maskedWords.set(indices.toSet)
        }(using unsafeWindowOwner)
