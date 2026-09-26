package com.github.pwharned.flashcards.reviewer.frontend

import com.github.pwharned.flashcards.shared.domain.{
  Enrichment,
  PreparedAudio,
  SelectionSpan,
  Utterance
}
import com.github.pwharned.flashcards.shared.protocol.{ClientMessage, ServerMessage}
import com.raquo.laminar.api.L.*
import scala.scalajs.js

enum ConnectionState:
  case Connecting
  case Connected
  case Reconnecting

enum LoadState[+A]:
  case Loading
  case Ready(value: A)
  case Failed(message: String)

enum DeckState:
  case Loading
  case Ready(decks: List[String])
  case Failed(message: String)

enum PreparationState:
  case Idle
  case Loading
  case Ready(audio: PreparedAudio)
  case Failed(message: String)

enum CreationOutcome:
  case Idle
  case Succeeded(noteId: Long, deck: String)
  case Duplicate(deck: String, reason: String)
  case Failed(deck: Option[String], message: String)
  case OutcomeUnknown(deck: String, message: String)

enum FeedbackKind:
  case Success
  case Duplicate
  case Error

final case class Feedback(kind: FeedbackKind, message: String)

object AppBus:
  val outgoing: EventBus[ClientMessage] = new EventBus
  val incoming: EventBus[ServerMessage] = new EventBus

  val connectionState: Var[ConnectionState] = Var(ConnectionState.Connecting)
  val source: Var[LoadState[ServerMessage.SourceLoaded]] = Var(LoadState.Loading)
  val deckState: Var[DeckState] = Var(DeckState.Loading)
  val selectedDeck: Var[Option[String]] = Var(None)
  val cueIndex: Var[Int] = Var(0)
  val selection: Var[Option[SelectionSpan]] = Var(None)
  val targetGloss: Var[String] = Var("")
  val sentenceTranslation: Var[String] = Var("")
  val preparationState: Var[PreparationState] = Var(PreparationState.Idle)
  val creating: Var[Boolean] = Var(false)
  val creationOutcome: Var[CreationOutcome] = Var(CreationOutcome.Idle)
  val feedback: Var[Option[Feedback]] = Var(None)

  private final case class PendingPreparation(
      requestId: String,
      utteranceId: String,
      selection: SelectionSpan
  )

  private final case class PendingCreate(requestId: String, deckName: String)

  private var initialized = false
  private var requestCounter = 0L
  private var activeSourceRequest = Option.empty[String]
  private var activeDeckRequest = Option.empty[String]
  private var activePreparationRequest = Option.empty[PendingPreparation]
  private var activeCreateRequest = Option.empty[PendingCreate]
  private var feedbackVersion = 0L

  def init(): Unit =
    if !initialized then
      initialized = true
      incoming.events.foreach(handleIncoming)(using unsafeWindowOwner)

  def requestSource(): Unit =
    val requestId = nextRequestId("source")
    activeSourceRequest = Some(requestId)
    source.now() match
      case LoadState.Ready(_) => ()
      case _                  => source.set(LoadState.Loading)
    outgoing.emit(ClientMessage.RequestSource(requestId))

  def requestDecks(): Unit =
    if creating.now() then ()
    else if connectionState.now() != ConnectionState.Connected then
      activeDeckRequest = None
      deckState.set(DeckState.Failed("Reconnect before refreshing decks."))
    else
      val requestId = nextRequestId("decks")
      activeDeckRequest = Some(requestId)
      deckState.set(DeckState.Loading)
      outgoing.emit(ClientMessage.RequestDecks(requestId))

  def selectDeck(deckName: String): Unit =
    if !creating.now() then
      deckState.now() match
        case DeckState.Ready(decks) =>
          updateSelectedDeck(Option(deckName).filter(decks.contains))
        case _ => ()

  def selectCue(index: Int): Unit =
    if creating.now() then ()
    else source.now() match
      case LoadState.Ready(loaded) if loaded.artifact.utterances.indices.contains(index) =>
        if index != cueIndex.now() then
          resetEditor()
          cueIndex.set(index)
      case _ => ()

  def prepareSelection(utteranceId: String, span: SelectionSpan): Boolean =
    if creating.now() || connectionState.now() != ConnectionState.Connected then false
    else currentCue match
      case Some(cue) if cue.id == utteranceId =>
        span.validate(cue.text) match
          case Left(message) =>
            activePreparationRequest = None
            preparationState.set(PreparationState.Failed(message))
            selection.set(None)
            targetGloss.set("")
            sentenceTranslation.set("")
            false
          case Right(validSpan) =>
            validSpan.extract(cue.text) match
              case Right(selected) if selected.trim.nonEmpty =>
                val requestId = nextRequestId("prepare")
                creationOutcome.set(CreationOutcome.Idle)
                activePreparationRequest = Some(
                  PendingPreparation(requestId, utteranceId, validSpan)
                )
                preparationState.set(PreparationState.Loading)
                selection.set(Some(validSpan))
                targetGloss.set("")
                sentenceTranslation.set("")
                outgoing.emit(
                  ClientMessage.PrepareSelection(requestId, utteranceId, validSpan)
                )
                true
              case _ =>
                activePreparationRequest = None
                preparationState.set(
                  PreparationState.Failed("Selection must contain non-whitespace text.")
                )
                selection.set(Some(validSpan))
                targetGloss.set("")
                sentenceTranslation.set("")
                false
      case _ => false

  def retryPreparation(): Unit =
    preparationState.now() match
      case PreparationState.Failed(_) =>
        (currentCue, selection.now()) match
          case (Some(cue), Some(span)) => prepareSelection(cue.id, span)
          case _                       => ()
      case _ => ()

  def createCard(): Unit =
    creationOutcome.now() match
      case CreationOutcome.OutcomeUnknown(_, _) => ()
      case _ =>
        val enrichment = Enrichment(targetGloss.now(), sentenceTranslation.now()).validate
        (
          currentCue,
          selection.now(),
          enrichment,
          activeCreateRequest,
          preparationState.now(),
          connectionState.now(),
          validSelectedDeck
        ) match
          case (
                Some(cue),
                Some(span),
                Right(validEnrichment),
                None,
                PreparationState.Ready(audio),
                ConnectionState.Connected,
                Some(deckName)
              ) if span.validate(cue.text).isRight =>
            val requestId = nextRequestId("create")
            activeCreateRequest = Some(PendingCreate(requestId, deckName))
            creating.set(true)
            outgoing.emit(
              ClientMessage.CreateCard(
                requestId = requestId,
                audioId = audio.audioId,
                deckName = deckName,
                utteranceId = cue.id,
                selection = span,
                targetGloss = validEnrichment.targetGloss,
                sentenceTranslation = validEnrichment.sentenceTranslation
              )
            )
          case _ => ()

  def transportDisconnected(): Unit =
    activeSourceRequest = None
    activeDeckRequest = None
    deckState.set(DeckState.Loading)
    activePreparationRequest = None
    preparationState.now() match
      case PreparationState.Idle => ()
      case _ =>
        preparationState.set(
          PreparationState.Failed(
            "Connection lost. Reconnect, then retry preparation."
          )
        )
    activeCreateRequest.foreach: pending =>
      creationOutcome.set(
        CreationOutcome.OutcomeUnknown(
          pending.deckName,
          "The connection closed before the server confirmed Anki's result."
        )
      )
      showFeedback(
        FeedbackKind.Error,
        s"Creation in '${pending.deckName}' may have completed. Check Anki before retrying."
      )
    activeCreateRequest = None
    creating.set(false)

  def transportSendFailed(message: ClientMessage, failure: String): Unit =
    message match
      case request: ClientMessage.RequestSource
          if activeSourceRequest.contains(request.requestId) =>
        activeSourceRequest = None
        source.now() match
          case LoadState.Ready(_) => ()
          case _                  => source.set(LoadState.Failed(failure))
      case request: ClientMessage.RequestDecks
          if activeDeckRequest.contains(request.requestId) =>
        activeDeckRequest = None
        deckState.set(DeckState.Failed(failure))
      case request: ClientMessage.PrepareSelection =>
        activePreparationRequest match
          case Some(pending) if pending.requestId == request.requestId =>
            activePreparationRequest = None
            preparationState.set(PreparationState.Failed(failure))
          case _ => ()
      case request: ClientMessage.CreateCard =>
        activeCreateRequest match
          case Some(pending) if pending.requestId == request.requestId =>
            activeCreateRequest = None
            preparationState.set(PreparationState.Failed(failure))
            creationOutcome.set(
              CreationOutcome.Failed(Some(pending.deckName), failure)
            )
            creating.set(false)
          case _ => ()
      case _ => ()
    showFeedback(FeedbackKind.Error, failure)

  def reportTransportError(message: String): Unit =
    showFeedback(FeedbackKind.Error, message)

  private def handleIncoming(message: ServerMessage): Unit = message match
    case loaded: ServerMessage.SourceLoaded
        if activeSourceRequest.contains(loaded.requestId) =>
      activeSourceRequest = None
      loaded.artifact.validate match
        case Left(message) => source.set(LoadState.Failed(message))
        case Right(_) if loaded.artifact.utterances.isEmpty =>
          source.set(LoadState.Failed("The source contains no transcript cues."))
        case Right(_) =>
          source.now() match
            case LoadState.Ready(previous) if previous.artifact.id == loaded.artifact.id =>
              val previousIndex = cueIndex.now()
              val lastIndex = loaded.artifact.utterances.size - 1
              val nextIndex = math.min(previousIndex, lastIndex)
              val cueChanged =
                previous.artifact.utterances.lift(previousIndex) !=
                  loaded.artifact.utterances.lift(nextIndex)
              if cueChanged then resetEditor()
              cueIndex.set(nextIndex)
              source.set(LoadState.Ready(loaded))
            case _ =>
              cueIndex.set(0)
              resetEditor()
              source.set(LoadState.Ready(loaded))
          requestDecks()

    case loaded: ServerMessage.DecksLoaded
        if activeDeckRequest.contains(loaded.requestId) =>
      activeDeckRequest = None
      val decks = loaded.decks.filter(_.trim.nonEmpty).distinct.sorted
      val configuredDeck = source.now() match
        case LoadState.Ready(loadedSource) => Some(loadedSource.deckName)
        case _                             => None
      val nextDeck = selectedDeck.now().filter(decks.contains)
        .orElse(configuredDeck.filter(decks.contains))
        .orElse(decks.headOption)
      updateSelectedDeck(nextDeck)
      deckState.set(DeckState.Ready(decks))

    case prepared: ServerMessage.SelectionPrepared =>
      activePreparationRequest match
        case Some(pending)
            if pending.requestId == prepared.requestId &&
              pending.utteranceId == prepared.utteranceId &&
              pending.selection == prepared.selection &&
              selection.now().contains(prepared.selection) &&
              currentCue.exists(_.id == prepared.utteranceId) =>
          activePreparationRequest = None
          val validated = for
            enrichment <- prepared.enrichment.validate
            audio <- prepared.audio.validate
          yield enrichment -> audio
          validated match
            case Right((enrichment, audio)) =>
              targetGloss.set(enrichment.targetGloss)
              sentenceTranslation.set(enrichment.sentenceTranslation)
              preparationState.set(PreparationState.Ready(audio))
            case Left(message) =>
              preparationState.set(PreparationState.Failed(message))
        case _ => ()

    case created: ServerMessage.CardCreated =>
      activeCreateRequest match
        case Some(pending) if pending.requestId == created.requestId =>
          activeCreateRequest = None
          invalidatePreparation()
          creationOutcome.set(
            CreationOutcome.Succeeded(created.noteId, pending.deckName)
          )
          creating.set(false)
          showFeedback(
            FeedbackKind.Success,
            s"Card created in '${pending.deckName}' (note ${created.noteId})."
          )
        case _ => ()

    case unknown: ServerMessage.CardCreationUnknown =>
      activeCreateRequest match
        case Some(pending) if pending.requestId == unknown.requestId =>
          activeCreateRequest = None
          creationOutcome.set(
            CreationOutcome.OutcomeUnknown(pending.deckName, unknown.message)
          )
          creating.set(false)
          showFeedback(
            FeedbackKind.Error,
            s"Creation in '${pending.deckName}' may have completed. Check Anki before continuing."
          )
        case _ => ()

    case rejected: ServerMessage.CardRejected =>
      activeCreateRequest match
        case Some(pending) if pending.requestId == rejected.requestId =>
          activeCreateRequest = None
          val kind = if rejected.duplicate then FeedbackKind.Duplicate else FeedbackKind.Error
          if rejected.duplicate then
            invalidatePreparation()
            creationOutcome.set(
              CreationOutcome.Duplicate(pending.deckName, rejected.reason)
            )
          else
            preparationState.set(PreparationState.Failed(rejected.reason))
            creationOutcome.set(
              CreationOutcome.Failed(Some(pending.deckName), rejected.reason)
            )
          creating.set(false)
          showFeedback(kind, s"${pending.deckName}: ${rejected.reason}")
        case _ => ()

    case failed: ServerMessage.RequestFailed =>
      if activeSourceRequest.contains(failed.requestId) then
        activeSourceRequest = None
        source.now() match
          case LoadState.Ready(_) =>
            deckState.set(
              DeckState.Failed("Decks were not refreshed because source reload failed.")
            )
            showFeedback(FeedbackKind.Error, failed.message)
          case _                  => source.set(LoadState.Failed(failed.message))
      else if activeDeckRequest.contains(failed.requestId) then
        activeDeckRequest = None
        deckState.set(DeckState.Failed(failed.message))
      else
        activePreparationRequest match
          case Some(pending) if pending.requestId == failed.requestId =>
            activePreparationRequest = None
            preparationState.set(PreparationState.Failed(failed.message))
          case _ =>
            activeCreateRequest match
              case Some(pending) if pending.requestId == failed.requestId =>
                activeCreateRequest = None
                creationOutcome.set(
                  CreationOutcome.Failed(Some(pending.deckName), failed.message)
                )
                creating.set(false)
                showFeedback(FeedbackKind.Error, failed.message)
              case _ => ()

    case _ => ()

  private def currentCue: Option[Utterance] = source.now() match
    case LoadState.Ready(loaded) => loaded.artifact.utterances.lift(cueIndex.now())
    case _                       => None

  private def validSelectedDeck: Option[String] =
    (deckState.now(), selectedDeck.now()) match
      case (DeckState.Ready(decks), Some(deck)) if decks.contains(deck) => Some(deck)
      case _                                                           => None

  private def updateSelectedDeck(nextDeck: Option[String]): Unit =
    if selectedDeck.now() != nextDeck then
      selectedDeck.set(nextDeck)
      creationOutcome.now() match
        case CreationOutcome.OutcomeUnknown(_, _) => ()
        case _                                    => creationOutcome.set(CreationOutcome.Idle)

  private def invalidatePreparation(): Unit =
    activePreparationRequest = None
    preparationState.set(PreparationState.Idle)

  private def resetEditor(): Unit =
    activePreparationRequest = None
    preparationState.set(PreparationState.Idle)
    selection.set(None)
    targetGloss.set("")
    sentenceTranslation.set("")
    creationOutcome.set(CreationOutcome.Idle)

  private def nextRequestId(prefix: String): String =
    requestCounter += 1
    s"$prefix-${js.Date.now().toLong}-$requestCounter"

  private def showFeedback(kind: FeedbackKind, message: String): Unit =
    feedbackVersion += 1
    val version = feedbackVersion
    feedback.set(Some(Feedback(kind, message)))
    js.timers.setTimeout(3600) {
      if feedbackVersion == version then feedback.set(None)
    }
