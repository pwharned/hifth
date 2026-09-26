package com.github.pwharned.flashcards.ui

import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}
import com.raquo.laminar.api.L.*
import org.scalajs.dom

def SelectionEditor(
    text: String,
    onSelected: SelectionSpan => Boolean
): HtmlElement =
  var lastEmitted = Option.empty[SelectionSpan]

  def readSelection(element: dom.HTMLTextAreaElement): Unit =
    val maybeSpan = Option.when(element.selectionStart != element.selectionEnd):
      SelectionSpan(element.selectionStart, element.selectionEnd)
    maybeSpan.filter(_.validate(text).isRight) match
      case Some(span) if !lastEmitted.contains(span) =>
        if onSelected(span) then lastEmitted = Some(span)
        else lastEmitted = None
      case Some(_) => ()
      case None    => lastEmitted = None

  textArea(
    cls("fc-selection-editor"),
    aria.label := "Source transcript. Select text to prepare a card.",
    readOnly := true,
    value := text,
    onSelect --> (event => readSelection(event.currentTarget.asInstanceOf[dom.HTMLTextAreaElement])),
    onPointerUp --> (event =>
      readSelection(event.currentTarget.asInstanceOf[dom.HTMLTextAreaElement])
    ),
    onKeyUp --> (event => readSelection(event.currentTarget.asInstanceOf[dom.HTMLTextAreaElement]))
  )

def CardPreview(
    text: String,
    selection: Signal[Option[SelectionSpan]],
    targetGloss: Signal[String],
    sentenceTranslation: Signal[String]
): HtmlElement =
  CardPreview(text, selection, targetGloss, sentenceTranslation, Val("und"))

def CardPreview(
    text: String,
    selection: Signal[Option[SelectionSpan]],
    targetGloss: Signal[String],
    sentenceTranslation: Signal[String],
    sourceLanguage: Signal[String]
): HtmlElement =
  val cloze = selection.map:
    case Some(span) if span.validate(text).isRight =>
      val before = text.substring(0, span.startUtf16)
      val target = text.substring(span.startUtf16, span.endUtf16)
      val after = text.substring(span.endUtf16)
      s"$before{{c1::$target}}$after"
    case _ => "Select text to preview the cloze."

  val translation = targetGloss.combineWith(sentenceTranslation).map { (gloss, sentence) =>
    Enrichment(gloss, sentence).validate.fold(
      _ => "Complete both translation fields to preview the back.",
      value => s"${value.targetGloss} : ${value.sentenceTranslation}"
    )
  }

  div(
    cls("fc-card-preview"),
    div(
      cls("fc-card-preview__side"),
      span(cls("fc-card-preview__label"), "Cloze"),
      div(
        cls("fc-card-preview__value"),
        lang <-- sourceLanguage,
        onMountCallback(_.thisNode.ref.setAttribute("dir", "auto")),
        child.text <-- cloze
      )
    ),
    div(
      cls("fc-card-preview__side"),
      lang := "en",
      span(cls("fc-card-preview__label"), "Translation"),
      div(cls("fc-card-preview__value"), child.text <-- translation)
    )
  )
