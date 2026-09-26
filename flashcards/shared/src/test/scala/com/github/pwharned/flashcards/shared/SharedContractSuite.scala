package com.github.pwharned.flashcards.shared

import com.github.plokhotnyuk.jsoniter_scala.core.{readFromString, writeToString}
import com.github.pwharned.flashcards.shared.domain.*
import com.github.pwharned.flashcards.shared.protocol.*

class SharedContractSuite extends munit.FunSuite:
  test("exact repeated occurrence is clozed once"):
    val draft = CardDraft(
      language = "en",
      sentence = "fish and fish",
      selection = SelectionSpan(9, 13),
      targetGloss = "poisson",
      sentenceTranslation = "poisson et poisson"
    )
    val fields = draft.fields("clip.mp3").toOption.get
    assertEquals(fields("Text"), "fish and {{c1::fish}}[sound:clip.mp3]")
    assertEquals(fields("Back Extra"), "")
    assertEquals(fields("Translation"), "poisson : poisson et poisson")

  test("UTF-16 selection accepts an emoji but not half of it"):
    val text = "a😀b"
    assertEquals(SelectionSpan(1, 3).extract(text), Right("😀"))
    assert(SelectionSpan(1, 2).validate(text).isLeft)
    assert(SelectionSpan(2, 3).validate(text).isLeft)

  test("shared WebSocket messages round trip"):
    val message: ClientMessage = ClientMessage.PrepareSelection(
      "request-1",
      "utterance-1",
      SelectionSpan(2, 6)
    )
    val messageJson = writeToString(message)
    assertEquals(
      messageJson,
      """{"type":"PrepareSelection","requestId":"request-1","utteranceId":"utterance-1","selection":{"startUtf16":2,"endUtf16":6}}"""
    )
    assertEquals(readFromString[ClientMessage](messageJson), message)

    val prepared: ServerMessage = ServerMessage.SelectionPrepared(
      "request-1",
      "utterance-1",
      SelectionSpan(2, 6),
      Enrichment("meaning", "sentence"),
      PreparedAudio(
        "audio-1",
        "/prepared-audio/audio-1",
        "clausula_en_hash.mp3",
        "audio/mpeg",
        42,
        "a" * 64
      )
    )
    assertEquals(readFromString[ServerMessage](writeToString(prepared)), prepared)

    val decks: ServerMessage = ServerMessage.DecksLoaded(
      "decks-1",
      List("Default", "Language::Vietnamese")
    )
    assertEquals(readFromString[ServerMessage](writeToString(decks)), decks)

    val create: ClientMessage = ClientMessage.CreateCard(
      "create-1",
      "audio-1",
      "Language::Vietnamese",
      "utterance-1",
      SelectionSpan(2, 6),
      "meaning",
      "sentence"
    )
    assertEquals(readFromString[ClientMessage](writeToString(create)), create)

    val unknown: ServerMessage = ServerMessage.CardCreationUnknown(
      "create-1",
      "Anki may have created the note before the response was lost"
    )
    assertEquals(readFromString[ServerMessage](writeToString(unknown)), unknown)

  test("artifact decoding rejects state fields and validation checks code-point spans"):
    val invalidJson =
      """{"artifact_type":"media","schema_version":1,"id":"a","title":"t","language":"en","media":[],"utterances":[],"cards":[]}"""
    intercept[Throwable](readFromString[MediaArtifact](invalidJson))

    val artifact = MediaArtifact(
      "media",
      1,
      "artifact",
      "Title",
      "en",
      List(MediaSource("media", "audio.mp3", "en", None, None, None, Some(1000))),
      List(
        Utterance(
          "utterance",
          "media",
          "a😀b",
          0,
          900,
          List(BaseToken("wrong", ArtifactTextSpan(1, 2), None, None, None)),
          None,
          None,
          List("test")
        )
      )
    )
    assert(artifact.validate.isLeft)

  test("Scala artifact JSON emits nullable and empty fields deterministically"):
    val artifact = MediaArtifact(
      "media",
      1,
      "artifact",
      "Title",
      "en",
      List(MediaSource("media", "audio.mp3", "en", None, None, None, None)),
      Nil
    )
    val json = writeToString(artifact)
    assert(json.contains("\"source_url\":null"))
    assert(json.contains("\"utterances\":[]"))
    assertEquals(readFromString[MediaArtifact](json), artifact)

  test("nullable artifact fields may be omitted"):
    val json =
      """{"artifact_type":"media","schema_version":1,"id":"artifact","title":"Title","language":"en","media":[{"id":"media","path":"audio.mp3","language":"en"}],"utterances":[{"id":"utterance","media_id":"media","text":"word","start_ms":0,"end_ms":1000,"tokens":[{"text":"word","span":{"start_char":0,"end_char":4}}],"provenance":[]}]}"""
    val artifact = readFromString[MediaArtifact](json)
    assertEquals(artifact.validate, Right(artifact))
    assertEquals(artifact.media.head.title, None)
    assertEquals(artifact.utterances.head.tokens.head.start_ms, None)

  test("translation fields escape active HTML"):
    val draft = CardDraft(
      "en",
      "one two",
      SelectionSpan(4, 7),
      "<script>alert('x')</script>",
      "one & two"
    )
    assertEquals(
      draft.fields("clip.mp3").toOption.get("Translation"),
      "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; : one &amp; two"
    )
