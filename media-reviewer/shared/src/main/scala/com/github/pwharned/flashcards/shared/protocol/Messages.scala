package com.github.pwharned.flashcards.shared.protocol

import com.github.plokhotnyuk.jsoniter_scala.core.JsonValueCodec
import com.github.plokhotnyuk.jsoniter_scala.macros.{CodecMakerConfig, JsonCodecMaker}
import com.github.pwharned.flashcards.shared.domain.*

sealed trait ClientMessage
object ClientMessage:
  final case class RequestSource(requestId: String) extends ClientMessage
  final case class RequestDecks(requestId: String) extends ClientMessage
  final case class PrepareSelection(
      requestId: String,
      utteranceId: String,
      selection: SelectionSpan
  ) extends ClientMessage
  final case class CreateCard(
      requestId: String,
      audioId: String,
      deckName: String,
      utteranceId: String,
      selection: SelectionSpan,
      targetGloss: String,
      sentenceTranslation: String
  ) extends ClientMessage

  given JsonValueCodec[ClientMessage] =
    JsonCodecMaker.make(CodecMakerConfig.withSkipUnexpectedFields(false))

sealed trait ServerMessage
object ServerMessage:
  final case class SourceLoaded(
      requestId: String,
      artifact: MediaArtifact,
      mediaUrls: Map[String, String],
      deckName: String,
      modelName: String
  ) extends ServerMessage
  final case class SelectionPrepared(
      requestId: String,
      utteranceId: String,
      selection: SelectionSpan,
      enrichment: Enrichment,
      audio: PreparedAudio
  ) extends ServerMessage
  final case class DecksLoaded(requestId: String, decks: List[String]) extends ServerMessage
  final case class CardCreated(requestId: String, noteId: Long) extends ServerMessage
  final case class CardCreationUnknown(requestId: String, message: String) extends ServerMessage
  final case class CardRejected(requestId: String, reason: String, duplicate: Boolean)
      extends ServerMessage
  final case class RequestFailed(requestId: String, message: String) extends ServerMessage

  given JsonValueCodec[ServerMessage] =
    JsonCodecMaker.make(CodecMakerConfig.withSkipUnexpectedFields(false))
