package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.effect.std.Semaphore
import com.github.pwharned.flashcards.shared.domain.{CardDraft, SelectionSpan, Utterance}
import com.github.pwharned.flashcards.shared.protocol.{ClientMessage, ServerMessage}

final class Reviewer(
    config: Config,
    source: LoadedArtifact,
    google: GoogleGateway,
    anki: AnkiGateway,
    preparations: EphemeralAudioStore,
    ownerId: String,
    workGate: Semaphore[IO]
):
  def handle(message: ClientMessage): IO[ServerMessage] =
    start(message).flatten

  def start(message: ClientMessage): IO[IO[ServerMessage]] =
    if requestId(message).trim.isEmpty then
      IO.pure(IO.pure(ServerMessage.RequestFailed("", "requestId must not be empty")))
    else
      message match
        case request: ClientMessage.PrepareSelection =>
          preparations.begin(ownerId).map: generation =>
            guarded(message)(prepare(request, generation))
        case _ => IO.pure(guarded(message)(dispatch(message)))

  def close: IO[Unit] = preparations.clear(ownerId)

  private def dispatch(message: ClientMessage): IO[ServerMessage] =
    message match
      case request: ClientMessage.RequestSource =>
        IO.pure(
          ServerMessage.SourceLoaded(
            request.requestId,
            source.artifact,
            source.mediaById.keysIterator.map(id => id -> s"/media/${encodeSegment(id)}").toMap,
            config.deckName,
            config.ankiModelName
          )
        )
      case request: ClientMessage.RequestDecks =>
        anki.deckNames.map(decks => ServerMessage.DecksLoaded(request.requestId, decks))
      case _: ClientMessage.PrepareSelection =>
        IO.raiseError(new IllegalStateException("preparation was not initialized"))
      case request: ClientMessage.CreateCard => createCard(request)

  private def prepare(
      request: ClientMessage.PrepareSelection,
      generation: Long
  ): IO[ServerMessage] =
    validateSelection(request.utteranceId, request.selection) match
      case Left(error) => IO.pure(ServerMessage.RequestFailed(request.requestId, error))
      case Right((utterance, _)) =>
        val artifactLanguage = source.mediaById(utterance.media_id).source.language
        google
          .prepare(utterance.text, request.selection, artifactLanguage)
          .flatMap: prepared =>
            preparations
              .complete(
                ownerId,
                generation,
                utterance.id,
                request.selection,
                prepared.enrichment,
                prepared.sourceLanguage,
                prepared.filename,
                prepared.audioBytes
              )
              .map:
                case Some(stored) =>
                  ServerMessage.SelectionPrepared(
                    request.requestId,
                    utterance.id,
                    request.selection,
                    stored.enrichment,
                    stored.audio
                  )
                case None =>
                  ServerMessage.RequestFailed(
                    request.requestId,
                    "selection preparation was superseded"
                  )

  private def createCard(request: ClientMessage.CreateCard): IO[ServerMessage] =
    validateSelection(request.utteranceId, request.selection) match
      case Left(error) =>
        IO.pure(ServerMessage.CardRejected(request.requestId, error, duplicate = false))
      case Right((utterance, _)) =>
        if request.deckName.trim.isEmpty then
          IO.pure(
            ServerMessage.CardRejected(
              request.requestId,
              "deck name must not be empty",
              duplicate = false
            )
          )
        else
          preparations
            .forCreate(ownerId, request.audioId, utterance.id, request.selection)
            .flatMap:
              case Left(error) =>
                IO.pure(ServerMessage.CardRejected(request.requestId, error, duplicate = false))
              case Right(prepared) =>
                val draft = CardDraft(
                  language = prepared.sourceLanguage,
                  sentence = utterance.text,
                  selection = request.selection,
                  targetGloss = request.targetGloss,
                  sentenceTranslation = request.sentenceTranslation
                )
                draft.validate match
                  case Left(error) =>
                    IO.pure(ServerMessage.CardRejected(request.requestId, error, duplicate = false))
                  case Right(validDraft) =>
                    createNote(
                      request.requestId,
                      request.deckName.trim,
                      validDraft,
                      prepared
                    )

  private def createNote(
      requestId: String,
      deckName: String,
      draft: CardDraft,
      prepared: StoredPreparation
  ): IO[ServerMessage] =
    val audio = prepared.audio
    IO.fromEither(
      draft.fields(audio.filename).left.map(error => new IllegalArgumentException(error))
    ).flatMap: fields =>
      val tags = List("clausula", ankiTag(draft.language)).distinct
      anki.validateDestination(deckName) *>
        anki.storeMediaFile(audio.filename, prepared.bytes).flatMap: storedFilename =>
          if storedFilename != audio.filename then
            IO.raiseError(
              new IllegalStateException(
                s"AnkiConnect stored audio as '$storedFilename' instead of '${audio.filename}'"
              )
            )
          else
            anki.addNote(deckName, fields, tags).flatMap:
              case Right(noteId) =>
                preparations.clearIfMatches(ownerId, audio.audioId).as(
                  ServerMessage.CardCreated(requestId, noteId)
                )
              case Left(reason) =>
                preparations.clearIfMatches(ownerId, audio.audioId).as(
                  ServerMessage.CardRejected(requestId, reason, duplicate = true)
                )

  private def validateSelection(
      utteranceId: String,
      selection: SelectionSpan
  ): Either[String, (Utterance, String)] =
    for
      utterance <- source.utteranceById
        .get(utteranceId)
        .toRight(s"unknown utterance: $utteranceId")
      selected <- selection.extract(utterance.text)
      _ <- Either.cond(selected.trim.nonEmpty, (), "selection must not contain only whitespace")
    yield utterance -> selected

  private def requestId(message: ClientMessage): String = message match
    case value: ClientMessage.RequestSource     => value.requestId
    case value: ClientMessage.RequestDecks      => value.requestId
    case value: ClientMessage.PrepareSelection => value.requestId
    case value: ClientMessage.CreateCard        => value.requestId

  private def guarded(message: ClientMessage)(operation: IO[ServerMessage]): IO[ServerMessage] =
    workGate.permit.use(_ => operation).handleError:
      case error: AnkiOutcomeUnknown =>
        ServerMessage.CardCreationUnknown(requestId(message), errorMessage(error))
      case error => ServerMessage.RequestFailed(requestId(message), errorMessage(error))

  private def ankiTag(value: String): String = value.trim.replaceAll("\\s+", "_")

  private def encodeSegment(value: String): String =
    value.getBytes(java.nio.charset.StandardCharsets.UTF_8).map: byte =>
      val unsigned = byte & 0xff
      val character = unsigned.toChar
      val unreserved =
        (unsigned >= 'a' && unsigned <= 'z') ||
          (unsigned >= 'A' && unsigned <= 'Z') ||
          (unsigned >= '0' && unsigned <= '9') ||
          "-._~".contains(character)
      if unreserved then character.toString
      else "%" + f"$unsigned%02X"
    .mkString

  private def errorMessage(error: Throwable): String =
    val messages = Iterator
      .iterate(Option(error))(_.flatMap(value => Option(value.getCause)))
      .takeWhile(_.nonEmpty)
      .flatten
      .flatMap(value => Option(value.getMessage))
      .map(_.trim)
      .filter(_.nonEmpty)
      .toList
    messages.lastOption.getOrElse(error.getClass.getSimpleName).take(1000)
