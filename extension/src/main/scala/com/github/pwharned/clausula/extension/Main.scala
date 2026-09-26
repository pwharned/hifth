package com.github.pwharned.clausula.extension

import com.github.pwharned.flashcards.shared.domain.{CardDraft, Enrichment, SelectionSpan}
import com.github.pwharned.flashcards.ui.CardPreview
import com.raquo.laminar.api.L.*
import org.scalajs.dom

import scala.concurrent.{ExecutionContext, Future, Promise}
import scala.scalajs.js
import scala.util.{Failure, Success}

object Main:
  private given ExecutionContext = ExecutionContext.global

  private final case class CapturedSelection(
      sentence: String,
      selection: SelectionSpan,
      target: String
  )

  private final case class Translation(text: String, detectedLanguage: Option[String])

  private enum UiState:
    case Idle
    case Translating
    case Ready
    case Creating
    case Created(noteId: String)
    case Failed(message: String)

  private final class DuplicateNote(message: String) extends RuntimeException(message)

  private val ignoredElements = Set("script", "style", "noscript", "rt", "rp")
  private val blockElements = Set(
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "td",
    "th",
    "ul"
  )

  private val current = Var(Option.empty[CapturedSelection])
  private val position = Var((12.0, 12.0))
  private val targetGloss = Var("")
  private val sentenceTranslation = Var("")
  private val sourceLanguage = Var("auto")
  private val uiState = Var[UiState](UiState.Idle)

  private var requestSerial = 0
  private var targetEditedFor = -1
  private var sentenceEditedFor = -1
  private var selectionTimer = Option.empty[js.timers.SetTimeoutHandle]

  def main(args: Array[String]): Unit =
    if dom.document.getElementById("clausula-extension-root") == null then
      val container = dom.document.createElement("div")
      container.id = "clausula-extension-root"
      dom.document.body.appendChild(container)
      render(container, app)
      dom.document.addEventListener("selectionchange", (_: dom.Event) => scheduleSelectionCapture())

  private def app: HtmlElement =
    div(
      cls("clausula-shell"),
      left <-- position.signal.map { case (x, _) =>
        val maxLeft = math.max(12.0, dom.window.innerWidth - 444.0)
        s"${math.max(12.0, math.min(x, maxLeft))}px"
      },
      top <-- position.signal.map { case (_, y) =>
        val maxTop = math.max(12.0, dom.window.innerHeight - 520.0)
        s"${math.max(12.0, math.min(y + 16.0, maxTop))}px"
      },
      display <-- current.signal.map(value => if value.isDefined then "block" else "none"),
      child <-- current.signal.map:
        case Some(captured) => preview(captured)
        case None           => emptyNode
    )

  private def preview(captured: CapturedSelection): HtmlElement =
    val before = captured.sentence.substring(0, captured.selection.startUtf16)
    val target = captured.sentence.substring(
      captured.selection.startUtf16,
      captured.selection.endUtf16
    )
    val after = captured.sentence.substring(captured.selection.endUtf16)

    val locked = uiState.signal.map:
      case UiState.Creating | UiState.Created(_) => true
      case _                                     => false

    val creating = uiState.signal.map(_ == UiState.Creating)

    val createDisabled = targetGloss.signal
      .combineWith(sentenceTranslation.signal, uiState.signal)
      .map { (gloss, translation, state) =>
        val invalid = Enrichment(gloss, translation).validate.isLeft
        val lockedState = state match
          case UiState.Creating | UiState.Created(_) => true
          case _                                     => false
        invalid || lockedState
      }

    div(
      cls("clausula-popup"),
      div(
        cls("clausula-heading"),
        div(
          span(cls("clausula-kicker"), "Selected text"),
          strong(cls("clausula-target"), captured.target)
        ),
        button(
          cls("clausula-close"),
          tpe("button"),
          title("Dismiss"),
          disabled <-- creating,
          "Close",
          onClick --> (_ => dismiss())
        )
      ),
      p(
        cls("clausula-sentence"),
        span(before),
        mark(target),
        span(after)
      ),
      div(
        cls("clausula-language"),
        child.text <-- sourceLanguage.signal.map(language => s"Source language: $language")
      ),
      form(
        onSubmit.preventDefault --> (_ => createCard(captured)),
        label(
          cls("clausula-field"),
          span("Contextual gloss"),
          input(
            autoComplete("off"),
            placeholder("Translation of the exact selection"),
            value <-- targetGloss.signal,
            disabled <-- locked,
            onInput.mapToValue --> { value =>
              targetEditedFor = requestSerial
              targetGloss.set(value)
            }
          )
        ),
        label(
          cls("clausula-field"),
          span("Sentence translation"),
          textArea(
            rows := 3,
            placeholder("Translation of the full sentence"),
            value <-- sentenceTranslation.signal,
            disabled <-- locked,
            onInput.mapToValue --> { value =>
              sentenceEditedFor = requestSerial
              sentenceTranslation.set(value)
            }
          )
        ),
        CardPreview(
          captured.sentence,
          Val(Some(captured.selection)),
          targetGloss.signal,
          sentenceTranslation.signal
        ),
        div(
          cls <-- uiState.signal.map(statusClass),
          child.text <-- uiState.signal.map(statusText)
        ),
        div(
          cls("clausula-actions"),
          button(
            cls("clausula-create"),
            tpe("submit"),
            disabled <-- createDisabled,
            child.text <-- uiState.signal.map:
              case UiState.Creating   => "Creating card..."
              case UiState.Created(_) => "Card created"
              case _                  => "Create card"
          ),
          button(
            cls("clausula-dismiss"),
            tpe("button"),
            disabled <-- creating,
            "Dismiss",
            onClick --> (_ => dismiss())
          )
        )
      )
    )

  private def statusClass(state: UiState): String = state match
    case UiState.Failed(_)  => "clausula-status clausula-status--error"
    case UiState.Created(_) => "clausula-status clausula-status--success"
    case _                  => "clausula-status"

  private def statusText(state: UiState): String = state match
    case UiState.Idle            => ""
    case UiState.Translating     => "Translating the exact selection and full sentence..."
    case UiState.Ready           => "Translations are editable. Review the card before creating it."
    case UiState.Creating        => "Generating whole-sentence audio and contacting Anki..."
    case UiState.Created(noteId) => s"Card created in Anki (note $noteId)."
    case UiState.Failed(message) => message

  private def scheduleSelectionCapture(): Unit =
    selectionTimer.foreach(js.timers.clearTimeout)
    selectionTimer = Some(
      js.timers.setTimeout(250) {
        selectionTimer = None
        handleSelection()
      }
    )

  private def handleSelection(): Unit =
    if uiState.now() != UiState.Creating then
      val selection = dom.window.getSelection()
      val anchor = Option(selection).flatMap(value => Option(value.anchorNode))
      if anchor.exists(isInsidePopup) then ()
      else if selection == null || selection.rangeCount != 1 || selection.isCollapsed then dismiss()
      else
        val range = selection.getRangeAt(0)
        if isInsidePopup(range.commonAncestorContainer) then ()
        else
          capture(range) match
            case Left(message) =>
              dom.console.warn(s"Clausula could not capture the selection: $message")
              dismiss()
            case Right(captured) if current.now().contains(captured) => ()
            case Right(captured) =>
              val bounds = range.getBoundingClientRect()
              position.set((bounds.left, bounds.bottom))
              beginTranslation(captured)

  private def beginTranslation(captured: CapturedSelection): Unit =
    requestSerial += 1
    val serial = requestSerial
    targetEditedFor = -1
    sentenceEditedFor = -1
    targetGloss.set("")
    sentenceTranslation.set("")
    sourceLanguage.set(detectLanguage(captured.sentence))
    current.set(Some(captured))
    uiState.set(UiState.Translating)

    var remaining = 2
    var errors = List.empty[String]

    def complete(error: Option[String]): Unit =
      if isCurrent(serial) then
        error.foreach: message =>
          errors = message :: errors
          uiState.set(UiState.Failed(errors.reverse.mkString(" ")))
        remaining -= 1
        if remaining == 0 && errors.isEmpty then uiState.set(UiState.Ready)

    val sentenceRequest = requestTranslation(captured.sentence, captured, "sentence", "auto")
    val targetRequest = sentenceRequest.transformWith:
      case Success(translation) =>
        val language = translation.detectedLanguage
          .filter(_ != "auto")
          .getOrElse(sourceLanguage.now())
        requestTranslation(captured.target, captured, "contextual-target", language)
      case Failure(_) =>
        requestTranslation(captured.target, captured, "contextual-target", sourceLanguage.now())

    targetRequest.onComplete:
      case Success(translation) if canApplyTranslation(serial) =>
        if targetEditedFor != serial then targetGloss.set(translation.text)
        useDetectedLanguage(translation.detectedLanguage)
        complete(None)
      case Success(_) => ()
      case Failure(error) if canApplyTranslation(serial) =>
        complete(Some(s"Contextual translation failed: ${errorMessage(error)}"))
      case Failure(_) => ()

    sentenceRequest.onComplete:
      case Success(translation) if canApplyTranslation(serial) =>
        if sentenceEditedFor != serial then sentenceTranslation.set(translation.text)
        useDetectedLanguage(translation.detectedLanguage)
        complete(None)
      case Success(_) => ()
      case Failure(error) if canApplyTranslation(serial) =>
        complete(Some(s"Sentence translation failed: ${errorMessage(error)}"))
      case Failure(_) => ()

  private def createCard(captured: CapturedSelection): Unit =
    if current.now().contains(captured) then
      Enrichment(targetGloss.now(), sentenceTranslation.now()).validate match
        case Left(message) => uiState.set(UiState.Failed(message))
        case Right(enrichment) =>
          val language = normaliseLanguage(sourceLanguage.now())
          if language == "auto" then
            uiState.set(UiState.Failed("Could not detect the source language for speech audio."))
          else
            val draft = CardDraft(
              language = language,
              sentence = captured.sentence,
              selection = captured.selection,
              targetGloss = enrichment.targetGloss,
              sentenceTranslation = enrichment.sentenceTranslation
            )
            val serial = requestSerial
            uiState.set(UiState.Creating)
            (for
              _ <- validateAnkiModel()
              filename <- requestAudioFilename(captured.sentence, language)
              fields <- draft.fields(filename) match
                case Left(message) => Future.failed(new IllegalArgumentException(message))
                case Right(value)  => Future.successful(value)
              canAdd <- canAddNote(fields, language)
              _ <-
                if canAdd then Future.successful(())
                else Future.failed(new DuplicateNote("Anki rejected the note as a duplicate"))
              storedFilename <- requestAudio(captured.sentence, language)
              _ <-
                if storedFilename == filename then Future.successful(())
                else Future.failed(new RuntimeException("Audio filename changed during creation"))
              noteId <- addNote(fields, language)
            yield noteId)
              .onComplete:
                case Success(noteId) if isCurrent(serial) =>
                  uiState.set(UiState.Created(noteId))
                case Failure(error: DuplicateNote) if isCurrent(serial) =>
                  uiState.set(UiState.Failed(s"Card already exists: ${errorMessage(error)}"))
                case Failure(error) if isCurrent(serial) =>
                  uiState.set(UiState.Failed(s"Could not create card: ${errorMessage(error)}"))
                case _ => ()

  private def requestTranslation(
      text: String,
      captured: CapturedSelection,
      mode: String,
      language: String
  ): Future[Translation] =
    val message = js.Dynamic.literal(
      `type` = "TRANSLATE_REQUEST",
      text = text,
      context = captured.sentence,
      startUtf16 = captured.selection.startUtf16,
      endUtf16 = captured.selection.endUtf16,
      mode = mode,
      langSrc = language,
      langTgt = "en"
    )
    sendMessage(message).map: response =>
      val translated = optionalString(response.result)
        .filter(_.nonEmpty)
        .getOrElse(throw new RuntimeException("Google returned an empty translation"))
      Translation(translated, optionalString(response.detectedLang).map(normaliseLanguage))

  private def requestAudio(sentence: String, language: String): Future[String] =
    sendMessage(
      js.Dynamic.literal(
        `type` = "AUDIO_REQUEST",
        sentence = sentence,
        lang = language
      )
    ).map: response =>
      optionalString(response.filename)
        .filter(_.nonEmpty)
        .getOrElse(throw new RuntimeException("Audio service returned no filename"))

  private def requestAudioFilename(sentence: String, language: String): Future[String] =
    sendMessage(
      js.Dynamic.literal(
        `type` = "AUDIO_FILENAME_REQUEST",
        sentence = sentence,
        lang = language
      )
    ).map: response =>
      optionalString(response.filename)
        .filter(_.nonEmpty)
        .getOrElse(throw new RuntimeException("Audio service returned no filename"))

  private def validateAnkiModel(): Future[Unit] =
    val payload = js.Dynamic.literal(
      action = "modelFieldNames",
      version = 6,
      params = js.Dynamic.literal(modelName = "Cloze")
    )
    requestAnki(payload).flatMap: data =>
      val fields = data.result.asInstanceOf[js.Array[String]].toSet
      val missing = Set("Text", "Back Extra", "Translation") -- fields
      if missing.nonEmpty then
        throw new RuntimeException(s"Anki model Cloze is missing fields: ${missing.toList.sorted.mkString(", ")}")
      val ordered = data.result.asInstanceOf[js.Array[String]]
      if !ordered.headOption.contains("Text") then
        throw new RuntimeException("Anki model Cloze must use Text as its first field")
      val templatePayload = js.Dynamic.literal(
        action = "modelTemplates",
        version = 6,
        params = js.Dynamic.literal(modelName = "Cloze")
      )
      requestAnki(templatePayload).map: templateData =>
        val templates = templateData.result
          .asInstanceOf[js.Dictionary[js.Dictionary[String]]]
          .values
        val valid = templates.exists: template =>
          template.get("Front").exists(_.contains("{{cloze:Text}}")) &&
            template.get("Back").exists(_.contains("{{Translation}}"))
        if !valid then
          throw new RuntimeException("Anki model Cloze must cloze Text and render Translation")

  private def canAddNote(fields: Map[String, String], language: String): Future[Boolean] =
    val payload = js.Dynamic.literal(
      action = "canAddNotes",
      version = 6,
      params = js.Dynamic.literal(notes = js.Array(ankiNote(fields, language)))
    )
    requestAnki(payload).map: data =>
      val results = data.result.asInstanceOf[js.Array[Boolean]]
      results.headOption.getOrElse(throw new RuntimeException("AnkiConnect returned no duplicate result"))

  private def addNote(fields: Map[String, String], language: String): Future[String] =
    val payload = js.Dynamic.literal(
      action = "addNote",
      version = 6,
      params = js.Dynamic.literal(note = ankiNote(fields, language))
    )
    requestAnki(payload).map: data =>
      if data.result == null || data.result == js.undefined then
        throw new RuntimeException("AnkiConnect returned no note id")
      data.result.asInstanceOf[Double].toLong.toString

  private def ankiNote(fields: Map[String, String], language: String): js.Dynamic =
    val ankiFields = js.Dictionary[String]()
    fields.foreach { case (name, value) => ankiFields(name) = value }
    js.Dynamic.literal(
      deckName = "Default",
      modelName = "Cloze",
      fields = ankiFields,
      options = js.Dynamic.literal(allowDuplicate = false),
      tags = js.Array("clausula", language)
    )

  private def requestAnki(payload: js.Dynamic): Future[js.Dynamic] =
    sendMessage(js.Dynamic.literal(`type` = "ANKI_REQUEST", payload = payload)).map: response =>
      val data = response.data
      optionalString(data.error) match
        case Some(message) if message.toLowerCase.contains("duplicate") =>
          throw new DuplicateNote(message)
        case Some(message) => throw new RuntimeException(s"AnkiConnect: $message")
        case None => data

  private def sendMessage(message: js.Dynamic): Future[js.Dynamic] =
    val promise = Promise[js.Dynamic]()
    try
      val chrome = js.Dynamic.global.chrome
      chrome.runtime.sendMessage(
        message,
        (response: js.Dynamic) =>
          val lastError = chrome.runtime.lastError
          if lastError != null && lastError != js.undefined then
            promise.tryFailure(new RuntimeException(lastError.message.asInstanceOf[String]))
          else if response == null || response == js.undefined then
            promise.tryFailure(new RuntimeException("No response from the extension background worker"))
          else if response.success.asInstanceOf[Boolean] then promise.trySuccess(response)
          else
            promise.tryFailure(
              new RuntimeException(optionalString(response.error).getOrElse("Background request failed"))
            )
      )
    catch case error: Throwable => promise.tryFailure(error)
    promise.future

  private def capture(range: dom.Range): Either[String, CapturedSelection] =
    val startNode = range.startContainer
    val endNode = range.endContainer
    try
      val root = contextRoot(startNode, endNode)
      for
        rendered <- renderedContext(root, range)
        (context, absolute) = rendered
        sentenceAndSpan <- sentenceAround(context, absolute)
        (sentence, sentenceSpan) = sentenceAndSpan
        target <- sentenceSpan.extract(sentence)
        _ <- Either.cond(target.trim.nonEmpty, (), "selection contains only whitespace")
      yield CapturedSelection(sentence, sentenceSpan, target)
    catch case error: Throwable => Left(errorMessage(error))

  private def renderedContext(
      root: dom.Node,
      range: dom.Range
  ): Either[String, (String, SelectionSpan)] =
    val output = new StringBuilder
    var selectionStart = Option.empty[Int]
    var selectionEnd = Option.empty[Int]

    def recordBoundary(node: dom.Node, offset: Int): Unit =
      if node == range.startContainer && offset == range.startOffset then
        selectionStart = Some(output.length)
      if node == range.endContainer && offset == range.endOffset then
        selectionEnd = Some(output.length)

    def appendSeparator(): Unit =
      if output.nonEmpty && !output.last.isWhitespace then output.append('\n')

    def ignored(node: dom.Node): Boolean =
      if node.nodeType != dom.Node.ELEMENT_NODE then false
      else
        val element = node.asInstanceOf[dom.Element]
        val style = dom.window.getComputedStyle(element)
        ignoredElements.contains(node.nodeName.toLowerCase) ||
        element.id == "clausula-extension-root" ||
        style.display == "none" || style.visibility == "hidden" ||
        style.visibility == "collapse"

    def hasBlockDisplay(node: dom.Node): Boolean =
      if node.nodeType != dom.Node.ELEMENT_NODE then false
      else
        val display = dom.window.getComputedStyle(node.asInstanceOf[dom.Element]).display
        display == "block" || display == "list-item" || display == "flow-root" ||
        display == "flex" || display == "grid" || display.startsWith("table")

    def visit(node: dom.Node, isRoot: Boolean): Unit =
      if ignored(node) then ()
      else if node.nodeType == dom.Node.TEXT_NODE then
        val value = Option(node.nodeValue).getOrElse("")
        if node == range.startContainer && range.startOffset <= value.length then
          selectionStart = Some(output.length + range.startOffset)
        if node == range.endContainer && range.endOffset <= value.length then
          selectionEnd = Some(output.length + range.endOffset)
        output.append(value)
      else
        val name = node.nodeName.toLowerCase
        val block = !isRoot && hasBlockDisplay(node)
        if block then appendSeparator()
        recordBoundary(node, 0)
        if name == "br" then appendSeparator()
        else
          var index = 0
          while index < node.childNodes.length do
            visit(node.childNodes(index), isRoot = false)
            index += 1
            recordBoundary(node, index)
        if block then appendSeparator()

    visit(root, isRoot = true)
    for
      start <- selectionStart.toRight("could not map the start of the DOM range")
      end <- selectionEnd.toRight("could not map the end of the DOM range")
      context = output.result()
      span <- SelectionSpan(start, end).validate(context)
    yield context -> span

  private def sentenceAround(
      text: String,
      absolute: SelectionSpan
  ): Either[String, (String, SelectionSpan)] =
    var start = absolute.startUtf16
    while start > 0 && !isSentenceBoundaryAt(text, start - 1) do start -= 1

    var end = absolute.endUtf16
    if end == 0 || !isSentenceBoundaryAt(text, end - 1) then
      while end < text.length && !isSentenceBoundaryAt(text, end) do end += 1
      if end < text.length then end += 1
    while end < text.length && isClosingPunctuation(text.charAt(end)) do end += 1

    while start < absolute.startUtf16 && java.lang.Character.isWhitespace(text.charAt(start)) do
      start += 1
    while end > absolute.endUtf16 && java.lang.Character.isWhitespace(text.charAt(end - 1)) do
      end -= 1

    val sentence = text.substring(start, end)
    val local = SelectionSpan(absolute.startUtf16 - start, absolute.endUtf16 - start)
    local.validate(sentence).map(_ => sentence -> local)

  private def contextRoot(startNode: dom.Node, endNode: dom.Node): dom.Node =
    var node =
      if startNode.nodeType == dom.Node.ELEMENT_NODE then startNode
      else Option(startNode.parentNode).getOrElse(dom.document.body)
    while node != null do
      if blockElements.contains(node.nodeName.toLowerCase) && contains(node, endNode) then return node
      node = node.parentNode
    dom.document.body

  private def contains(parent: dom.Node, child: dom.Node): Boolean =
    parent.asInstanceOf[js.Dynamic].contains(child).asInstanceOf[Boolean]

  private def isInsidePopup(node: dom.Node): Boolean =
    var currentNode = node
    while currentNode != null do
      if currentNode.nodeType == dom.Node.ELEMENT_NODE &&
        currentNode.asInstanceOf[dom.Element].id == "clausula-extension-root"
      then return true
      currentNode = currentNode.parentNode
    false

  private def isSentenceBoundaryAt(text: String, index: Int): Boolean =
    val character = text.charAt(index)
    if character == '!' || character == '?' || character == '\u3002' ||
        character == '\uff01' || character == '\uff1f' || character == '\u061f'
    then true
    else if character != '.' then false
    else
      val previous = Option.when(index > 0)(text.charAt(index - 1))
      val next = Option.when(index + 1 < text.length)(text.charAt(index + 1))
      val decimal = previous.exists(_.isDigit) && next.exists(_.isDigit)
      val insideWord = next.exists(value => !value.isWhitespace && value != '"' && value != '\'')
      val earlierPeriod = next.contains('.')
      val wordStart = text.lastIndexWhere(_.isWhitespace, math.max(0, index - 1)) + 1
      val token = text.substring(wordStart, index).toLowerCase
      val abbreviation = Set(
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "vs",
        "etc",
        "e.g",
        "i.e"
      ).contains(token) || (token.length == 1 && token.headOption.exists(_.isLetter))
      !decimal && !insideWord && !earlierPeriod && !abbreviation

  private def isClosingPunctuation(character: Char): Boolean =
    character == '"' || character == '\'' || character == ')' || character == ']' ||
      character == '}' || character == '\u2019' || character == '\u201d' ||
      character == '\u3009' || character == '\u300b' || character == '\u300d'

  private def splitsSurrogatePair(text: String, index: Int): Boolean =
    index > 0 && index < text.length &&
      java.lang.Character.isHighSurrogate(text.charAt(index - 1)) &&
      java.lang.Character.isLowSurrogate(text.charAt(index))

  private def detectLanguage(text: String): String =
    val sample = text.take(400)
    if sample.exists(character => character >= '\u0400' && character <= '\u04ff') then "ru"
    else if sample.exists(character => character >= '\u3040' && character <= '\u30ff') then "ja"
    else if sample.exists(character => character >= '\u4e00' && character <= '\u9fff') then "zh"
    else if sample.exists(character => Set(0x06af, 0x0686, 0x067e, 0x0698, 0x06cc, 0x06a9).contains(character.toInt))
    then "fa"
    else if sample.exists(character => character >= '\u0600' && character <= '\u06ff') then "ar"
    else if sample.exists(character => character >= '\u0590' && character <= '\u05ff') then "he"
    else
      Option(dom.document.documentElement.getAttribute("lang"))
        .map(normaliseLanguage)
        .filter(_.nonEmpty)
        .getOrElse("auto")

  private def useDetectedLanguage(language: Option[String]): Unit =
    language.map(normaliseLanguage).filter(value => value.nonEmpty && value != "auto").foreach:
      detected => sourceLanguage.set(detected)

  private def normaliseLanguage(language: String): String =
    language.trim.toLowerCase.replace('_', '-') match
      case "" => "auto"
      case value => value

  private def optionalString(value: js.Dynamic): Option[String] =
    if value == null || value == js.undefined then None
    else Option(value.asInstanceOf[String]).map(_.trim)

  private def isCurrent(serial: Int): Boolean =
    serial == requestSerial && current.now().isDefined

  private def canApplyTranslation(serial: Int): Boolean =
    isCurrent(serial) && (uiState.now() match
      case UiState.Creating | UiState.Created(_) => false
      case _                                     => true)

  private def dismiss(): Unit =
    requestSerial += 1
    current.set(None)
    targetGloss.set("")
    sentenceTranslation.set("")
    uiState.set(UiState.Idle)

  private def errorMessage(error: Throwable): String =
    Option(error.getMessage).map(_.trim).filter(_.nonEmpty).getOrElse(error.getClass.getSimpleName)
