package com.github.pwharned.flashcards.reviewer.frontend

import com.github.pwharned.flashcards.shared.domain.{PreparedAudio, Utterance}
import com.github.pwharned.flashcards.shared.protocol.ServerMessage
import com.github.pwharned.flashcards.ui.{CardPreview, SelectionEditor}
import com.raquo.laminar.api.L.*
import org.scalajs.dom

object ReviewerApp:
  def mount(element: dom.Element): Unit =
    element.replaceChildren()
    render(element, app)

  private def app: HtmlElement =
    div(
      cls("mr-app"),
      child <-- AppBus.source.signal.map:
        case LoadState.Loading       => loadingView
        case LoadState.Failed(error) => errorView(error)
        case LoadState.Ready(source) => workspace(source),
      feedbackView
    )

  private def loadingView: HtmlElement =
    div(
      cls("mr-state"),
      div(
        cls("mr-state-card"),
        p(cls("mr-kicker"), "Media reviewer"),
        h1("Loading source"),
        p("Waiting for the reviewer source over WebSocket.")
      )
    )

  private def errorView(message: String): HtmlElement =
    div(
      cls("mr-state"),
      div(
        cls("mr-state-card mr-state-card--error"),
        p(cls("mr-kicker"), "Source error"),
        h1("Unable to load reviewer"),
        p(message),
        button(
          cls("mr-button"),
          "Try again",
          onClick --> (_ => AppBus.requestSource())
        )
      )
    )

  private def workspace(source: ServerMessage.SourceLoaded): HtmlElement =
    val artifact = source.artifact
    val cueSignal = AppBus.cueIndex.signal.map(index => artifact.utterances(index))
    var audioElement = Option.empty[dom.HTMLAudioElement]
    var stopAtSeconds = Option.empty[Double]

    def currentCue: Option[Utterance] = artifact.utterances.lift(AppBus.cueIndex.now())

    def stopAudio(): Unit =
      audioElement.foreach(_.pause())
      stopAtSeconds = None

    def navigate(offset: Int): Unit =
      if !AppBus.creating.now() then
        stopAudio()
        AppBus.selectCue(AppBus.cueIndex.now() + offset)

    def playCue(): Unit =
      for
        cue <- currentCue
        audio <- audioElement
        if source.mediaUrls.contains(cue.media_id)
      do
        stopAtSeconds = Some(cue.end_ms / 1000.0)
        audio.currentTime = cue.start_ms / 1000.0
        val _ = audio.play()

    val audioUrl = cueSignal.map(cue => source.mediaUrls.getOrElse(cue.media_id, "about:blank"))
    val missingAudio = cueSignal.map(cue => !source.mediaUrls.contains(cue.media_id))
    val rtl = isRtl(artifact.language)

    div(
      headerTag(
        cls("mr-header"),
        div(
          p(cls("mr-kicker"), "Media cloze workshop"),
          h1(cls("mr-title"), artifact.title)
        ),
        div(
          cls("mr-header-meta"),
          span(s"Source language: ${artifact.language}"),
          connectionBadge
        )
      ),
      div(
        cls("mr-workspace"),
        div(
          cls("mr-panel mr-media-panel"),
          span(cls("mr-audio-label"), "Source cue"),
          audioTag(
            cls("mr-audio"),
            src <-- audioUrl,
            onMountCallback { context =>
              val player = context.thisNode.ref.asInstanceOf[dom.HTMLAudioElement]
              player.controls = true
              player.preload = "metadata"
              audioElement = Some(player)
            },
            onTimeUpdate --> { event =>
              val player = event.currentTarget.asInstanceOf[dom.HTMLAudioElement]
              stopAtSeconds.foreach { stopAt =>
                if player.currentTime >= stopAt then
                  player.pause()
                  player.currentTime = stopAt
                  stopAtSeconds = None
              }
            },
            onUnmountCallback { _ =>
              stopAudio()
              audioElement = None
            }
          ),
          child <-- missingAudio.map:
            case true  => div(cls("mr-audio-error"), "No media URL was provided for this cue.")
            case false => span(),
          div(
            cls("mr-cue-nav"),
            button(
              cls("mr-button"),
              "Previous",
              disabled <-- AppBus.cueIndex.signal
                .combineWith(AppBus.creating.signal)
                .map((index, busy) => index == 0 || busy),
              onClick --> (_ => navigate(-1))
            ),
            div(
              cls("mr-cue-position"),
              span(
                child.text <-- AppBus.cueIndex.signal.map(index =>
                  s"Cue ${index + 1} / ${artifact.utterances.size}"
                )
              ),
              span(
                cls("mr-cue-time"),
                child.text <-- cueSignal.map(cue =>
                  s"${formatTime(cue.start_ms)} - ${formatTime(cue.end_ms)}"
                )
              )
            ),
            button(
              cls("mr-button"),
              "Next",
              disabled <-- AppBus.cueIndex.signal
                .combineWith(AppBus.creating.signal)
                .map((index, busy) => index >= artifact.utterances.size - 1 || busy),
              onClick --> (_ => navigate(1))
            )
          ),
          button(
            cls("mr-button mr-play"),
            "Play full cue",
            disabled <-- missingAudio,
            onClick --> (_ => playCue())
          )
        ),
        div(
          cls("mr-panel mr-editor-panel"),
          div(
            cls("mr-section-heading"),
            div(
              p(cls("mr-kicker"), "Exact text span"),
              h2(idAttr := "source-transcript-heading", "Select a learning unit")
            ),
            span(cls("mr-instruction"), "Select directly in the transcript; UTF-16 offsets are preserved.")
          ),
          child <-- cueSignal.map(cue => cueEditor(cue, rtl, artifact.language)),
          preparationForm
        )
      )
    )

  private def cueEditor(cue: Utterance, rtl: Boolean, language: String): HtmlElement =
    div(
      SelectionEditor(
        cue.text,
        selection => AppBus.prepareSelection(cue.id, selection)
      ).amend(
        lang := language,
        aria.labelledBy := "source-transcript-heading",
        if rtl then direction("rtl") else direction("ltr"),
        cls <-- AppBus.creating.signal.map:
          case true  => "fc-selection-editor mr-selection-editor--locked"
          case false => "fc-selection-editor"
      ),
      div(
        cls("mr-selected"),
        child <-- AppBus.selection.signal.map:
          case Some(selectionSpan) =>
            selectionSpan.extract(cue.text).fold(
              _ => span(),
              selected =>
                span(
                  span(lang := "en", "Selected: "),
                  strong(
                    lang := language,
                    if rtl then direction("rtl") else direction("ltr"),
                    selected
                  )
                )
            )
          case None => span(lang := "en", "No text selected.")
      ),
      CardPreview(
        cue.text,
        AppBus.selection.signal,
        AppBus.targetGloss.signal,
        AppBus.sentenceTranslation.signal,
        Val(language)
      )
    )

  private def preparationForm: HtmlElement =
    val preparationReady = AppBus.preparationState.signal
      .combineWith(AppBus.connectionState.signal)
      .map:
        case (PreparationState.Ready(_), ConnectionState.Connected) => true
        case _                                                      => false
    val deckReady = AppBus.deckState.signal
      .combineWith(AppBus.selectedDeck.signal)
      .map:
        case (DeckState.Ready(decks), Some(deck)) => decks.contains(deck)
        case _                                    => false
    val createReady = preparationReady.combineWith(deckReady).map(_ && _)
    val creationSafe = AppBus.creationOutcome.signal.map:
      case CreationOutcome.OutcomeUnknown(_, _) => false
      case _                                    => true
    val canCreate = createReady.combineWith(creationSafe).map(_ && _)
    val createDisabled = AppBus.targetGloss.signal
      .combineWith(
        AppBus.sentenceTranslation.signal,
        AppBus.selection.signal,
        AppBus.creating.signal,
        canCreate
      )
      .map { (gloss, translation, selection, creating, ready) =>
        gloss.trim.isEmpty || translation.trim.isEmpty || selection.isEmpty || creating || !ready
      }
    val retryDisabled = AppBus.connectionState.signal
      .combineWith(AppBus.creating.signal, AppBus.selection.signal)
      .map { (connection, creating, selection) =>
        connection != ConnectionState.Connected || creating || selection.isEmpty
      }

    form(
      onSubmit.preventDefault --> (_ => AppBus.createCard()),
      deckSelector,
      div(
        role := "status",
        cls <-- AppBus.preparationState.signal.map:
          case PreparationState.Loading   => "mr-preparation-status mr-preparation-status--loading"
          case PreparationState.Failed(_) => "mr-preparation-status mr-preparation-status--error"
          case _                          => "mr-preparation-status",
        span(
          child.text <-- AppBus.preparationState.signal
            .combineWith(AppBus.selection.signal)
            .map:
              case (PreparationState.Loading, _) =>
                "Translating the sentence and selection, then generating sentence audio..."
              case (PreparationState.Ready(_), _) =>
                "Preparation ready. Review the translations and generated audio."
              case (PreparationState.Failed(message), _) => message
              case (PreparationState.Idle, Some(_)) =>
                "Preparation is no longer valid. Select text to prepare again."
              case _ => "Select nonempty text to prepare a card."
        ),
        child <-- AppBus.preparationState.signal.map:
          case PreparationState.Failed(_) =>
            button(
              cls("mr-button mr-retry"),
              tpe("button"),
              "Retry preparation",
              disabled <-- retryDisabled,
              onClick --> (_ => AppBus.retryPreparation())
            )
          case _ => span()
      ),
      child <-- AppBus.preparationState.signal.map:
        case PreparationState.Ready(audio) => preparedEditor(audio)
        case _                             => span(),
      div(
        cls("mr-create-row"),
        creationOutcomePanel,
        button(
          cls("mr-button mr-primary"),
          tpe("submit"),
          disabled <-- createDisabled,
          child.text <-- AppBus.creating.signal.map(if _ then "Creating..." else "Create card")
        )
      )
    )

  private def deckSelector: HtmlElement =
    val selectorDisabled = AppBus.deckState.signal
      .combineWith(AppBus.creating.signal, AppBus.connectionState.signal)
      .map:
        case (DeckState.Ready(decks), false, ConnectionState.Connected) => decks.isEmpty
        case _                                                         => true
    val refreshDisabled = AppBus.deckState.signal
      .combineWith(AppBus.creating.signal, AppBus.connectionState.signal)
      .map { (state, creating, connection) =>
        state == DeckState.Loading || creating || connection != ConnectionState.Connected
      }

    div(
      cls("mr-deck-control"),
      label(
        cls("mr-field mr-deck-field"),
        span("Destination deck"),
        select(
          cls("mr-deck-select"),
          disabled <-- selectorDisabled,
          onChange.mapToValue --> (deck => AppBus.selectDeck(deck)),
          children <-- AppBus.deckState.signal
            .combineWith(AppBus.selectedDeck.signal)
            .map:
              case (DeckState.Loading, _) =>
                Vector(option(value := "", "Loading decks..."))
              case (DeckState.Failed(_), _) =>
                Vector(option(value := "", "Decks unavailable"))
              case (DeckState.Ready(Nil), _) =>
                Vector(option(value := "", "No decks available"))
              case (DeckState.Ready(decks), currentDeck) =>
                decks.toVector.map: deck =>
                  option(
                    value := deck,
                    selected := currentDeck.contains(deck),
                    deck
                  )
        )
      ),
      button(
        cls("mr-button mr-deck-refresh"),
        tpe("button"),
        disabled <-- refreshDisabled,
        child.text <-- AppBus.deckState.signal.map:
          case DeckState.Loading => "Refreshing..."
          case _                 => "Refresh decks",
        onClick --> (_ => AppBus.requestDecks())
      ),
      div(
        role := "status",
        cls <-- AppBus.deckState.signal.map:
          case DeckState.Failed(_) => "mr-deck-status mr-deck-status--error"
          case _                   => "mr-deck-status",
        child.text <-- AppBus.deckState.signal.map:
          case DeckState.Loading         => "Loading decks from Anki..."
          case DeckState.Failed(message) => message
          case DeckState.Ready(Nil)      => "No Anki decks are available."
          case DeckState.Ready(decks)    => s"${decks.size} decks available."
      )
    )

  private def creationOutcomePanel: HtmlElement =
    div(
      role := "status",
      cls <-- AppBus.creationOutcome.signal.map:
        case CreationOutcome.Idle            => "mr-outcome"
        case CreationOutcome.Succeeded(_, _) => "mr-outcome mr-outcome--success"
        case CreationOutcome.Duplicate(_, _) => "mr-outcome mr-outcome--duplicate"
        case CreationOutcome.Failed(_, _)    => "mr-outcome mr-outcome--error"
        case CreationOutcome.OutcomeUnknown(_, _) => "mr-outcome mr-outcome--unknown",
      child.text <-- AppBus.creationOutcome.signal.map:
        case CreationOutcome.Idle => "No card creation result yet."
        case CreationOutcome.Succeeded(noteId, deck) =>
          s"Created note $noteId in deck '$deck'."
        case CreationOutcome.Duplicate(deck, reason) =>
          s"Anki rejected this note as a duplicate while targeting deck '$deck': $reason"
        case CreationOutcome.Failed(Some(deck), message) =>
          s"Could not create the card in deck '$deck': $message"
        case CreationOutcome.Failed(None, message) =>
          s"Could not create the card: $message"
        case CreationOutcome.OutcomeUnknown(deck, message) =>
          s"The result for deck '$deck' is unknown: $message Check Anki before continuing."
    )

  private def preparedEditor(audio: PreparedAudio): HtmlElement =
    div(
      cls("mr-prepared"),
      div(
        cls("mr-generated-audio-block"),
        span(cls("mr-audio-label"), "Generated sentence audio"),
        audioTag(
          cls("mr-generated-audio"),
          src := audio.previewUrl,
          onMountCallback { context =>
            val player = context.thisNode.ref.asInstanceOf[dom.HTMLAudioElement]
            player.controls = true
            player.preload = "metadata"
            player.autoplay = false
          },
          onUnmountCallback { node =>
            node.ref.asInstanceOf[dom.HTMLAudioElement].pause()
          }
        )
      ),
      div(
        cls("mr-fields"),
        label(
          cls("mr-field"),
          "Selection translation",
          input(
            autoComplete("off"),
            placeholder("Contextual meaning"),
            value <-- AppBus.targetGloss.signal,
            disabled <-- AppBus.creating.signal,
            onInput.mapToValue --> AppBus.targetGloss.writer
          )
        ),
        label(
          cls("mr-field"),
          "Sentence translation",
          textArea(
            rows := 3,
            placeholder("Translation of the full utterance"),
            value <-- AppBus.sentenceTranslation.signal,
            disabled <-- AppBus.creating.signal,
            onInput.mapToValue --> AppBus.sentenceTranslation.writer
          )
        )
      )
    )

  private def connectionBadge: HtmlElement =
    span(
      role := "status",
      cls <-- AppBus.connectionState.signal.map:
        case ConnectionState.Connected    => "mr-connection mr-connection--connected"
        case ConnectionState.Reconnecting => "mr-connection mr-connection--reconnecting"
        case ConnectionState.Connecting   => "mr-connection",
      child.text <-- AppBus.connectionState.signal.map:
        case ConnectionState.Connected    => "Connected"
        case ConnectionState.Reconnecting => "Reconnecting"
        case ConnectionState.Connecting   => "Connecting"
    )

  private def feedbackView: HtmlElement =
    div(
      child <-- AppBus.feedback.signal.map:
        case Some(value) =>
          val kindClass = value.kind match
            case FeedbackKind.Success   => "mr-toast--success"
            case FeedbackKind.Duplicate => "mr-toast--duplicate"
            case FeedbackKind.Error     => "mr-toast--error"
          div(cls(s"mr-toast $kindClass"), value.message)
        case None => div(cls("mr-toast mr-toast--hidden"))
    )

  private def isRtl(language: String): Boolean =
    Set("ar", "fa", "he", "ur").contains(language.toLowerCase.split("-").headOption.getOrElse(""))

  private def formatTime(milliseconds: Double): String =
    val totalMilliseconds = math.max(0L, milliseconds.round)
    val minutes = totalMilliseconds / 60000
    val seconds = (totalMilliseconds / 1000) % 60
    val millis = totalMilliseconds % 1000
    f"$minutes%d:$seconds%02d.$millis%03d"
