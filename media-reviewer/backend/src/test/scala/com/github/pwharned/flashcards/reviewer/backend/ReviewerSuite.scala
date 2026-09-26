package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.{Deferred, IO}
import cats.effect.std.Semaphore
import cats.effect.syntax.all.*
import cats.effect.unsafe.implicits.global
import cats.syntax.all.*
import com.github.pwharned.flashcards.shared.domain.{
  Enrichment,
  MediaArtifact,
  MediaSource,
  PreparedAudio,
  SelectionSpan,
  Utterance
}
import com.github.pwharned.flashcards.shared.protocol.{ClientMessage, ServerMessage}

import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import scala.concurrent.duration.*
import scala.jdk.CollectionConverters.*

class ReviewerSuite extends munit.FunSuite:
  private val ownerA = "owner-a"
  private val ownerB = "owner-b"
  private val sentence = "alpha beta gamma"
  private val selection = SelectionSpan(6, 10)
  private val otherSelection = SelectionSpan(0, 5)
  private val ttsFilename = "clausula_ar_fixture.mp3"
  private val audioBytes = Array[Byte](0, 1, -1, 127, -128, 42)
  private val googleEnrichment = Enrichment("machine gloss", "machine sentence translation")
  private val editedGloss = "edited gloss"
  private val editedTranslation = "edited sentence translation"
  private val selectedDeck = "Selected Deck"
  private val duplicateReason = "cannot create note because it is a duplicate"
  private val noteId = 987654321L

  private val media = MediaSource(
    id = "media-1",
    path = "fixture.mp3",
    language = "fa",
    title = Some("Fixture media"),
    source_url = None,
    checksum_sha256 = None,
    duration_ms = Some(2000.0)
  )
  private val utterance = Utterance(
    id = "utterance-1",
    media_id = media.id,
    text = sentence,
    start_ms = 125.0,
    end_ms = 975.0,
    tokens = Nil,
    translation = None,
    confidence = None,
    provenance = List("test")
  )
  private val artifact = MediaArtifact(
    artifact_type = MediaArtifact.ArtifactType,
    schema_version = MediaArtifact.SchemaVersion,
    id = "artifact-1",
    title = "Reviewer fixture",
    language = "fa",
    media = List(media),
    utterances = List(utterance)
  )
  private val source = LoadedArtifact(
    artifact,
    Map(media.id -> ResolvedMedia(media, Path.of(media.path), 1L, 0L)),
    Map(utterance.id -> utterance)
  )
  private val config = Config.parse(
    List(
      "--deck",
      "Reviewer Deck",
      "--anki-model",
      "Cloze Test",
      "artifact.json"
    )
  ) match
    case Right(Some(value)) => value
    case result             => throw new IllegalStateException(s"could not build test config: $result")

  test("request decks returns the Anki deck names"):
    val decks = List("Default", selectedDeck, "Archive")
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki(availableDecks = decks)
    val harness = newHarness(google, anki)

    assertEquals(
      harness
        .reviewer(ownerA)
        .handle(ClientMessage.RequestDecks("request-decks"))
        .unsafeRunSync(),
      ServerMessage.DecksLoaded("request-decks", decks)
    )
    assertEquals(anki.calls, List(AnkiCall.DeckNames))
    assertEquals(google.calls, Nil)

  test("prepare and create use the shared protocol end to end"):
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki()
    val harness = newHarness(google, anki)
    val reviewer = harness.reviewer(ownerA)

    val prepared = expectPrepared(
      reviewer
        .handle(ClientMessage.PrepareSelection("prepare-1", utterance.id, selection))
        .unsafeRunSync()
    )
    val expectedAudio = PreparedAudio(
      audioId = "audio-1",
      previewUrl = "/prepared-audio/audio-1",
      filename = ttsFilename,
      contentType = "audio/mpeg",
      sizeBytes = audioBytes.length.toLong,
      sha256 = sha256(audioBytes)
    )

    assertEquals(
      prepared,
      ServerMessage.SelectionPrepared(
        "prepare-1",
        utterance.id,
        selection,
        googleEnrichment,
        expectedAudio
      )
    )
    assertEquals(
      google.calls,
      List(GoogleCall(sentence, selection, artifactLanguage = "fa"))
    )
    assertEquals(anki.calls, Nil)

    val stored = harness.store
      .find(prepared.audio.audioId)
      .unsafeRunSync()
      .getOrElse(fail("prepared audio was not stored"))
    assertEquals(stored.enrichment, googleEnrichment)
    assertEquals(stored.sourceLanguage, "ar")
    assertEquals(stored.utteranceId, utterance.id)
    assertEquals(stored.selection, selection)
    assert(stored.bytes.sameElements(audioBytes))

    val fields = Map(
      "Text" -> s"alpha {{c1::beta}} gamma[sound:$ttsFilename]",
      "Back Extra" -> "",
      "Translation" -> s"$editedGloss : $editedTranslation"
    )
    val tags = List("clausula", "ar")
    assert(selectedDeck != config.deckName)
    val created = reviewer
      .handle(
        createRequest(
          "create-1",
          prepared.audio.audioId,
          deckName = selectedDeck
        )
      )
      .unsafeRunSync()

    assertEquals(created, ServerMessage.CardCreated("create-1", noteId))
    assertEquals(
      anki.calls,
      List(
        AnkiCall.ValidateDestination(selectedDeck),
        AnkiCall.StoreMediaFile(ttsFilename, audioBytes.toList),
        AnkiCall.AddNote(selectedDeck, fields, tags)
      )
    )
    assertEquals(google.calls.size, 1)
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().isEmpty)

  test("create rejects the wrong owner, audio id, and selection without calling Anki"):
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki()
    val harness = newHarness(google, anki)
    val reviewerA = harness.reviewer(ownerA)
    val reviewerB = harness.reviewer(ownerB)
    val prepared = expectPrepared(
      reviewerA
        .handle(ClientMessage.PrepareSelection("prepare-owner", utterance.id, selection))
        .unsafeRunSync()
    )

    assertEquals(
      reviewerB
        .handle(createRequest("wrong-owner", prepared.audio.audioId))
        .unsafeRunSync(),
      ServerMessage.CardRejected(
        "wrong-owner",
        "prepared audio is unavailable for this owner",
        duplicate = false
      )
    )
    assertEquals(
      reviewerA
        .handle(createRequest("wrong-audio", "audio-that-was-never-issued"))
        .unsafeRunSync(),
      ServerMessage.CardRejected(
        "wrong-audio",
        "prepared audio is unavailable for this owner",
        duplicate = false
      )
    )
    assertEquals(
      reviewerA
        .handle(
          createRequest(
            "wrong-selection",
            prepared.audio.audioId,
            span = otherSelection
          )
        )
        .unsafeRunSync(),
      ServerMessage.CardRejected(
        "wrong-selection",
        "prepared audio does not match the requested selection",
        duplicate = false
      )
    )

    assertEquals(anki.calls, Nil)
    assertEquals(google.calls.size, 1)
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)

  test("empty and unknown decks fail before media storage"):
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki()
    val harness = newHarness(google, anki)
    val reviewer = harness.reviewer(ownerA)
    val prepared = expectPrepared(
      reviewer
        .handle(ClientMessage.PrepareSelection("prepare-deck-errors", utterance.id, selection))
        .unsafeRunSync()
    )

    assertEquals(
      reviewer
        .handle(
          createRequest(
            "create-empty-deck",
            prepared.audio.audioId,
            deckName = "   "
          )
        )
        .unsafeRunSync(),
      ServerMessage.CardRejected(
        "create-empty-deck",
        "deck name must not be empty",
        duplicate = false
      )
    )
    assertEquals(anki.calls, Nil)
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)

    assertEquals(
      reviewer
        .handle(
          createRequest(
            "create-unknown-deck",
            prepared.audio.audioId,
            deckName = "Missing Deck"
          )
        )
        .unsafeRunSync(),
      ServerMessage.RequestFailed(
        "create-unknown-deck",
        "Anki deck Missing Deck does not exist"
      )
    )
    assertEquals(
      anki.calls,
      List(AnkiCall.ValidateDestination("Missing Deck"))
    )
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)
    assertEquals(google.calls.size, 1)

  test("duplicate addNote result clears preparation after media storage"):
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki(addResult = Left(duplicateReason))
    val harness = newHarness(google, anki)
    val reviewer = harness.reviewer(ownerA)
    val prepared = expectPrepared(
      reviewer
        .handle(ClientMessage.PrepareSelection("prepare-duplicate", utterance.id, selection))
        .unsafeRunSync()
    )
    val fields = expectedFields(ttsFilename)
    val tags = List("clausula", "ar")
    val create = createRequest("create-duplicate", prepared.audio.audioId)

    assertEquals(
      reviewer.handle(create).unsafeRunSync(),
      ServerMessage.CardRejected(
        "create-duplicate",
        duplicateReason,
        duplicate = true
      )
    )
    assertEquals(
      anki.calls,
      List(
        AnkiCall.ValidateDestination(selectedDeck),
        AnkiCall.StoreMediaFile(ttsFilename, audioBytes.toList),
        AnkiCall.AddNote(selectedDeck, fields, tags)
      )
    )
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().isEmpty)

    assertEquals(
      reviewer
        .handle(create.copy(requestId = "retry-duplicate"))
        .unsafeRunSync(),
      ServerMessage.CardRejected(
        "retry-duplicate",
        "prepared audio is unavailable for this owner",
        duplicate = false
      )
    )
    assertEquals(anki.calls.size, 3)
    assertEquals(google.calls.size, 1)

  test("an unknown addNote outcome retains preparation after media storage"):
    val unknownMessage = "Anki addNote may have succeeded before the connection closed"
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki(
      failFirstAdd = Some(new AnkiOutcomeUnknown(unknownMessage))
    )
    val harness = newHarness(google, anki)
    val reviewer = harness.reviewer(ownerA)
    val prepared = expectPrepared(
      reviewer
        .handle(ClientMessage.PrepareSelection("prepare-unknown", utterance.id, selection))
        .unsafeRunSync()
    )
    val fields = expectedFields(ttsFilename)
    val tags = List("clausula", "ar")

    val response = reviewer
      .handle(createRequest("create-unknown", prepared.audio.audioId))
      .unsafeRunSync()

    assertEquals(
      response,
      ServerMessage.CardCreationUnknown("create-unknown", unknownMessage)
    )
    assertEquals(
      anki.calls,
      List(
        AnkiCall.ValidateDestination(selectedDeck),
        AnkiCall.StoreMediaFile(ttsFilename, audioBytes.toList),
        AnkiCall.AddNote(selectedDeck, fields, tags)
      )
    )
    val retained = harness.store
      .find(prepared.audio.audioId)
      .unsafeRunSync()
      .getOrElse(fail("unknown addNote outcome cleared the preparation"))
    assert(retained.bytes.sameElements(audioBytes))
    assertEquals(google.calls.size, 1)

  test("a nonduplicate addNote exception retains preparation for a successful retry"):
    val google = FakeGoogle.fixed(preparation())
    val anki = FakeAnki(
      failFirstAdd = Some(new IllegalStateException("Anki temporarily unavailable"))
    )
    val harness = newHarness(google, anki)
    val reviewer = harness.reviewer(ownerA)
    val prepared = expectPrepared(
      reviewer
        .handle(ClientMessage.PrepareSelection("prepare-retry", utterance.id, selection))
        .unsafeRunSync()
    )
    val fields = expectedFields(ttsFilename)
    val tags = List("clausula", "ar")

    assertEquals(
      reviewer
        .handle(createRequest("create-fails", prepared.audio.audioId))
        .unsafeRunSync(),
      ServerMessage.RequestFailed("create-fails", "Anki temporarily unavailable")
    )
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().nonEmpty)

    assertEquals(
      reviewer
        .handle(createRequest("create-retry", prepared.audio.audioId))
        .unsafeRunSync(),
      ServerMessage.CardCreated("create-retry", noteId)
    )
    assertEquals(
      anki.calls,
      List(
        AnkiCall.ValidateDestination(selectedDeck),
        AnkiCall.StoreMediaFile(ttsFilename, audioBytes.toList),
        AnkiCall.AddNote(selectedDeck, fields, tags),
        AnkiCall.ValidateDestination(selectedDeck),
        AnkiCall.StoreMediaFile(ttsFilename, audioBytes.toList),
        AnkiCall.AddNote(selectedDeck, fields, tags)
      )
    )
    assertEquals(google.calls.size, 1)
    assert(harness.store.find(prepared.audio.audioId).unsafeRunSync().isEmpty)

  test("start registers preparation arrival order before returned operations run"):
    val firstBytes = Array[Byte](30, 31, 32)
    val secondBytes = Array[Byte](40, 41, 42)
    val firstPreparation = preparation(
      enrichment = Enrichment("first gloss", "first translation"),
      filename = "first.mp3",
      bytes = firstBytes
    )
    val secondPreparation = preparation(
      enrichment = Enrichment("second gloss", "second translation"),
      filename = "second.mp3",
      bytes = secondBytes
    )
    val google = FakeGoogle: (call, _) =>
      if call.selection == otherSelection then IO.pure(firstPreparation)
      else if call.selection == selection then IO.pure(secondPreparation)
      else IO.raiseError(new IllegalStateException("unexpected selection"))
    val anki = FakeAnki()
    val harness = newHarness(google, anki)
    val reviewer = harness.reviewer(ownerA)
    val firstRequest = ClientMessage.PrepareSelection(
      "start-first",
      utterance.id,
      otherSelection
    )
    val secondRequest = ClientMessage.PrepareSelection(
      "start-second",
      utterance.id,
      selection
    )

    val (firstResponse, secondResponse) = (for
      firstOperation <- reviewer.start(firstRequest)
      secondOperation <- reviewer.start(secondRequest)
      // Arrival order is registered above; execute the guarded work in reverse order.
      secondResponse <- secondOperation
      firstResponse <- firstOperation
    yield firstResponse -> secondResponse).unsafeRunSync()

    assertEquals(
      firstResponse,
      ServerMessage.RequestFailed(
        "start-first",
        "selection preparation was superseded"
      )
    )
    val second = expectPrepared(secondResponse)
    assertEquals(second.requestId, "start-second")
    assertEquals(second.selection, selection)
    assertEquals(second.enrichment, secondPreparation.enrichment)
    assertEquals(second.audio.audioId, "audio-1")
    assertEquals(second.audio.filename, "second.mp3")

    val current = harness.store
      .forCreate(ownerA, second.audio.audioId, utterance.id, selection)
      .unsafeRunSync()
      .toOption
      .getOrElse(fail("second preparation was not current"))
    assert(current.bytes.sameElements(secondBytes))
    assert(harness.store.find("audio-2").unsafeRunSync().isEmpty)
    assertEquals(
      google.calls,
      List(
        GoogleCall(sentence, selection, "fa"),
        GoogleCall(sentence, otherSelection, "fa")
      )
    )
    assertEquals(anki.calls, Nil)

  test("a superseded preparation cannot become usable after its Google call completes"):
    val firstStarted = Deferred[IO, Unit].unsafeRunSync()
    val releaseFirst = Deferred[IO, Unit].unsafeRunSync()
    val staleBytes = Array[Byte](10, 11, 12)
    val currentBytes = Array[Byte](20, 21, 22)
    val stalePreparation = preparation(
      enrichment = Enrichment("stale gloss", "stale translation"),
      filename = "stale.mp3",
      bytes = staleBytes
    )
    val currentPreparation = preparation(
      enrichment = Enrichment("current gloss", "current translation"),
      filename = "current.mp3",
      bytes = currentBytes
    )
    val google = FakeGoogle: (_, index) =>
      index match
        case 0 => firstStarted.complete(()).void *> releaseFirst.get.as(stalePreparation)
        case 1 => IO.pure(currentPreparation)
        case _ => IO.raiseError(new IllegalStateException("unexpected Google call"))
    val anki = FakeAnki()
    val harness = newHarness(google, anki, permits = 2L)
    val reviewer = harness.reviewer(ownerA)
    val staleRequest = ClientMessage.PrepareSelection(
      "prepare-stale",
      utterance.id,
      otherSelection
    )
    val currentRequest = ClientMessage.PrepareSelection(
      "prepare-current",
      utterance.id,
      selection
    )

    val (staleResponse, currentResponse) = (for
      staleFiber <- reviewer.handle(staleRequest).start
      _ <- firstStarted.get
      current <- reviewer.handle(currentRequest)
      _ <- releaseFirst.complete(())
      stale <- staleFiber.joinWithNever
    yield stale -> current).unsafeRunSync()

    assertEquals(
      staleResponse,
      ServerMessage.RequestFailed(
        "prepare-stale",
        "selection preparation was superseded"
      )
    )
    val current = expectPrepared(currentResponse)
    assertEquals(current.requestId, "prepare-current")
    assertEquals(current.selection, selection)
    assertEquals(current.enrichment, currentPreparation.enrichment)
    assertEquals(current.audio.audioId, "audio-1")
    assertEquals(current.audio.filename, "current.mp3")

    val storedCurrent = harness.store
      .find(current.audio.audioId)
      .unsafeRunSync()
      .getOrElse(fail("current preparation was not stored"))
    assert(storedCurrent.bytes.sameElements(currentBytes))
    assert(harness.store.find("audio-2").unsafeRunSync().isEmpty)
    assertEquals(
      reviewer
        .handle(
          createRequest(
            "create-stale",
            audioId = "audio-2",
            span = otherSelection
          )
        )
        .unsafeRunSync(),
      ServerMessage.CardRejected(
        "create-stale",
        "prepared audio is unavailable for this owner",
        duplicate = false
      )
    )
    assert(harness.store.find(current.audio.audioId).unsafeRunSync().nonEmpty)
    assertEquals(
      google.calls,
      List(
        GoogleCall(sentence, otherSelection, "fa"),
        GoogleCall(sentence, selection, "fa")
      )
    )
    assertEquals(anki.calls, Nil)

  private def preparation(
      enrichment: Enrichment = googleEnrichment,
      sourceLanguage: String = "ar",
      filename: String = ttsFilename,
      bytes: Array[Byte] = audioBytes
  ): GooglePreparation =
    GooglePreparation(
      enrichment = enrichment,
      sourceLanguage = sourceLanguage,
      filename = filename,
      audioBytes = bytes.clone(),
      sha256 = sha256(bytes)
    )

  private def createRequest(
      requestId: String,
      audioId: String,
      deckName: String = selectedDeck,
      span: SelectionSpan = selection,
      targetGloss: String = editedGloss,
      sentenceTranslation: String = editedTranslation
  ): ClientMessage.CreateCard =
    ClientMessage.CreateCard(
      requestId = requestId,
      audioId = audioId,
      deckName = deckName,
      utteranceId = utterance.id,
      selection = span,
      targetGloss = targetGloss,
      sentenceTranslation = sentenceTranslation
    )

  private def expectedFields(filename: String): Map[String, String] =
    Map(
      "Text" -> s"alpha {{c1::beta}} gamma[sound:$filename]",
      "Back Extra" -> "",
      "Translation" -> s"$editedGloss : $editedTranslation"
    )

  private def expectPrepared(message: ServerMessage): ServerMessage.SelectionPrepared =
    message match
      case prepared: ServerMessage.SelectionPrepared => prepared
      case other => fail(s"expected SelectionPrepared, got $other")

  private def sha256(bytes: Array[Byte]): String =
    HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))

  private def newHarness(
      google: FakeGoogle,
      anki: FakeAnki,
      permits: Long = 1L
  ): Harness =
    val ids = AtomicInteger(0)
    val store = EphemeralAudioStore
      .create(
        IO.delay(s"audio-${ids.incrementAndGet()}"),
        IO.pure(10.minutes)
      )
      .unsafeRunSync()
    val gate = Semaphore[IO](permits).unsafeRunSync()
    Harness(google, anki, store, gate)

  private final case class GoogleCall(
      sentence: String,
      selection: SelectionSpan,
      artifactLanguage: String
  )

  private final class FakeGoogle(
      respond: (GoogleCall, Int) => IO[GooglePreparation]
  ) extends GoogleGateway:
    private val recorded = CopyOnWriteArrayList[GoogleCall]()
    private val nextIndex = AtomicInteger(0)

    def calls: List[GoogleCall] = recorded.asScala.toList

    override def prepare(
        sentence: String,
        selection: SelectionSpan,
        artifactLanguage: String
    ): IO[GooglePreparation] =
      val call = GoogleCall(sentence, selection, artifactLanguage)
      IO.delay {
        recorded.add(call)
        nextIndex.getAndIncrement()
      }.flatMap(index => respond(call, index))

  private object FakeGoogle:
    def apply(respond: (GoogleCall, Int) => IO[GooglePreparation]): FakeGoogle =
      new FakeGoogle(respond)

    def fixed(value: GooglePreparation): FakeGoogle =
      FakeGoogle((_, _) => IO.pure(value))

  private sealed trait AnkiCall
  private object AnkiCall:
    case object DeckNames extends AnkiCall
    final case class ValidateDestination(deckName: String) extends AnkiCall
    final case class StoreMediaFile(filename: String, bytes: List[Byte]) extends AnkiCall
    final case class AddNote(
        deckName: String,
        fields: Map[String, String],
        tags: List[String]
    ) extends AnkiCall

  private final class FakeAnki(
      availableDecks: List[String] = List("Reviewer Deck", "Selected Deck"),
      failFirstAdd: Option[Throwable] = None,
      addResult: Either[String, Long] = Right(987654321L)
  ) extends AnkiGateway:
    private val recorded = CopyOnWriteArrayList[AnkiCall]()
    private val addAttempts = AtomicInteger(0)

    def calls: List[AnkiCall] = recorded.asScala.toList

    override def deckNames: IO[List[String]] =
      record(AnkiCall.DeckNames).as(availableDecks)

    override def validateDestination(deckName: String): IO[Unit] =
      record(AnkiCall.ValidateDestination(deckName)) *>
        (if availableDecks.contains(deckName) then IO.unit
         else IO.raiseError(new IllegalStateException(s"Anki deck $deckName does not exist")))

    override def storeMediaFile(filename: String, bytes: Array[Byte]): IO[String] =
      record(AnkiCall.StoreMediaFile(filename, bytes.toList)).as(filename)

    override def addNote(
        deckName: String,
        fields: Map[String, String],
        tags: List[String]
    ): IO[Either[String, Long]] =
      record(AnkiCall.AddNote(deckName, fields, tags)) *> IO.defer:
        val attempt = addAttempts.getAndIncrement()
        failFirstAdd match
          case Some(error) if attempt == 0 => IO.raiseError(error)
          case _                           => IO.pure(addResult)

    private def record(call: AnkiCall): IO[Unit] = IO.delay:
      recorded.add(call)
      ()

  private final case class Harness(
      google: FakeGoogle,
      anki: FakeAnki,
      store: EphemeralAudioStore,
      gate: Semaphore[IO]
  ):
    def reviewer(ownerId: String): Reviewer =
      Reviewer(config, source, google, anki, store, ownerId, gate)
