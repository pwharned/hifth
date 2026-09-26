package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.unsafe.implicits.global
import com.github.plokhotnyuk.jsoniter_scala.core.{
  JsonValueCodec,
  readFromString,
  writeToString
}
import com.github.plokhotnyuk.jsoniter_scala.macros.JsonCodecMaker
import com.sun.net.httpserver.{HttpExchange, HttpServer}

import java.net.{InetSocketAddress, URI}
import java.nio.charset.StandardCharsets
import java.util.concurrent.CopyOnWriteArrayList
import scala.collection.mutable
import scala.jdk.CollectionConverters.*

private object ExternalClientsTestSupport:
  final case class DeckNamesRequest(action: String, version: Int)
  final case class DeckNamesResponse(result: Option[List[String]], error: Option[String])

  final case class ModelFieldNamesParams(modelName: String)
  final case class ModelFieldNamesRequest(
      action: String,
      version: Int,
      params: ModelFieldNamesParams
  )
  final case class ModelFieldNamesResponse(result: Option[List[String]], error: Option[String])
  final case class ModelTemplatesResponse(
      result: Option[Map[String, Map[String, String]]],
      error: Option[String]
  )
  final case class ActionRequest(action: String, version: Int)

  final case class StoreMediaParams(filename: String, data: String)
  final case class StoreMediaRequest(action: String, version: Int, params: StoreMediaParams)

  final case class NoteOptions(allowDuplicate: Boolean)
  final case class Note(
      deckName: String,
      modelName: String,
      fields: Map[String, String],
      options: NoteOptions,
      tags: List[String]
  )
  final case class AddNoteParams(note: Note)
  final case class AddNoteRequest(action: String, version: Int, params: AddNoteParams)

  given JsonValueCodec[DeckNamesRequest] = JsonCodecMaker.make
  given JsonValueCodec[DeckNamesResponse] = JsonCodecMaker.make
  given JsonValueCodec[ModelFieldNamesParams] = JsonCodecMaker.make
  given JsonValueCodec[ModelFieldNamesRequest] = JsonCodecMaker.make
  given JsonValueCodec[ModelFieldNamesResponse] = JsonCodecMaker.make
  given JsonValueCodec[ModelTemplatesResponse] = JsonCodecMaker.make
  given JsonValueCodec[ActionRequest] = JsonCodecMaker.make
  given JsonValueCodec[StoreMediaParams] = JsonCodecMaker.make
  given JsonValueCodec[StoreMediaRequest] = JsonCodecMaker.make
  given JsonValueCodec[NoteOptions] = JsonCodecMaker.make
  given JsonValueCodec[Note] = JsonCodecMaker.make
  given JsonValueCodec[AddNoteParams] = JsonCodecMaker.make
  given JsonValueCodec[AddNoteRequest] = JsonCodecMaker.make

  final case class StubResponse(body: String, status: Int = 200, dropConnection: Boolean = false)
  final case class RecordedRequest(method: String, path: String, body: String)

  final class StubServer(configuredResponses: Map[String, List[StubResponse]]) extends AutoCloseable:
    private val responseQueues = configuredResponses.iterator
      .map((path, responses) => path -> mutable.Queue.from(responses))
      .toMap
    private val recordedRequests = new CopyOnWriteArrayList[RecordedRequest]()
    private val server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0)
    server.createContext("/", (exchange: HttpExchange) => handle(exchange))
    server.start()

    val baseUri: URI = URI.create(s"http://127.0.0.1:${server.getAddress.getPort}/")

    def requests: List[RecordedRequest] = recordedRequests.asScala.toList

    override def close(): Unit = server.stop(0)

    private def handle(exchange: HttpExchange): Unit =
      try
        val input = exchange.getRequestBody
        val body =
          try String(input.readAllBytes(), StandardCharsets.UTF_8)
          finally input.close()
        val request = RecordedRequest(
          exchange.getRequestMethod,
          exchange.getRequestURI.getPath,
          body
        )
        recordedRequests.add(request)

        val response = this.synchronized:
          responseQueues
            .get(request.path)
            .filter(_.nonEmpty)
            .map(_.dequeue())
            .getOrElse(
              StubResponse(
                s"{\"error\":\"unexpected request ${request.method} ${request.path}\"}",
                500
              )
            )
        if !response.dropConnection then
          val bytes = response.body.getBytes(StandardCharsets.UTF_8)
          exchange.getResponseHeaders.set("Content-Type", "application/json; charset=utf-8")
          exchange.sendResponseHeaders(response.status, bytes.length.toLong)
          val output = exchange.getResponseBody
          try output.write(bytes)
          finally output.close()
      finally exchange.close()

class ExternalClientsSuite extends munit.FunSuite:
  import ExternalClientsTestSupport.*
  import ExternalClientsTestSupport.given

  test("modelFieldNames requires Text, Back Extra, and Translation"):
    val requiredFields = List("Text", "Back Extra", "Translation")
    val selectedDecks = List("Deck A", "Deck B")
    val deckNames = writeToString(DeckNamesResponse(Some(selectedDecks), None))
    val validTemplates = ModelTemplatesResponse(
      Some(
        Map(
          "Card 1" -> Map(
            "Front" -> "{{cloze:Text}}",
            "Back" -> "{{cloze:Text}}<hr>{{Translation}}"
          )
        )
      ),
      None
    )
    val responses =
      List(
        deckNames,
        writeToString(ModelFieldNamesResponse(Some(requiredFields :+ "Other"), None)),
        writeToString(validTemplates)
      ) ++
        requiredFields.flatMap: missing =>
          List(
            deckNames,
            writeToString(
              ModelFieldNamesResponse(Some(requiredFields.filterNot(_ == missing)), None)
            )
          )

    withServer(Map("/" -> responses.map(StubResponse(_)))): server =>
      val clients = new ExternalClients(
        testConfig(
          server.baseUri,
          deckName = "Frontend Default",
          ankiModelName = "Cloze Plus"
        )
      )

      clients.validateDestination("Deck A").unsafeRunSync()
      requiredFields.foreach: missing =>
        val error = intercept[IllegalStateException]:
          clients.validateDestination("Deck B").unsafeRunSync()
        assertEquals(
          error.getMessage,
          s"Anki model Cloze Plus is missing fields: $missing"
        )

      val expected = ModelFieldNamesRequest(
        "modelFieldNames",
        6,
        ModelFieldNamesParams("Cloze Plus")
      )
      assertEquals(server.requests.map(_.method), List.fill(9)("POST"))
      assertEquals(server.requests.map(_.path), List.fill(9)("/"))
      assertEquals(
        server.requests.map(request => readFromString[ActionRequest](request.body).action),
        List(
          "deckNames",
          "modelFieldNames",
          "modelTemplates",
          "deckNames",
          "modelFieldNames",
          "deckNames",
          "modelFieldNames",
          "deckNames",
          "modelFieldNames"
        )
      )
      List(0, 3, 5, 7).foreach: index =>
        assertEquals(
          readFromString[DeckNamesRequest](server.requests(index).body),
          DeckNamesRequest("deckNames", 6)
        )
      List(1, 4, 6, 8).foreach: index =>
        assertEquals(readFromString[ModelFieldNamesRequest](server.requests(index).body), expected)

  test("deckNames returns distinct sorted nonempty names with the exact payload"):
    val response = DeckNamesResponse(
      Some(List("Zulu", "", "Default", "Alpha", "Default", "   ")),
      None
    )
    withServer(Map("/" -> List(StubResponse(writeToString(response))))): server =>
      val clients = new ExternalClients(testConfig(server.baseUri))

      assertEquals(clients.deckNames.unsafeRunSync(), List("Alpha", "Default", "Zulu"))
      assertEquals(server.requests.map(_.method), List("POST"))
      assertEquals(server.requests.head.body, """{"action":"deckNames","version":6}""")
      assertEquals(
        readFromString[DeckNamesRequest](server.requests.head.body),
        DeckNamesRequest("deckNames", 6)
      )

  test("validateDestination rejects empty and missing requested decks before model validation"):
    withServer(
      Map("/" -> List(StubResponse(writeToString(DeckNamesResponse(Some(List("Default")), None)))))
    ): server =>
      val clients = new ExternalClients(
        testConfig(server.baseUri, deckName = "Frontend Default")
      )

      val emptyError = intercept[IllegalArgumentException]:
        clients.validateDestination("   ").unsafeRunSync()
      assertEquals(emptyError.getMessage, "Anki deck name must not be empty")
      assertEquals(server.requests, Nil)

      val error = intercept[IllegalStateException]:
        clients.validateDestination("Study Deck").unsafeRunSync()

      assertEquals(error.getMessage, "Anki deck Study Deck does not exist")
      assertEquals(server.requests.map(_.method), List("POST"))
      assertEquals(server.requests.head.body, """{"action":"deckNames","version":6}""")
      assertEquals(
        readFromString[DeckNamesRequest](server.requests.head.body),
        DeckNamesRequest("deckNames", 6)
      )

  test("deckNames reports AnkiConnect errors exactly"):
    val response = DeckNamesResponse(None, Some("collection is unavailable"))
    withServer(Map("/" -> List(StubResponse(writeToString(response))))): server =>
      val clients = new ExternalClients(testConfig(server.baseUri))

      val error = intercept[IllegalStateException](clients.deckNames.unsafeRunSync())

      assertEquals(
        error.getMessage,
        "AnkiConnect deckNames failed: collection is unavailable"
      )
      assertEquals(server.requests.size, 1)
      assertEquals(
        readFromString[DeckNamesRequest](server.requests.head.body),
        DeckNamesRequest("deckNames", 6)
      )

  test("model templates must cloze Text and render Translation"):
    val decks = writeToString(DeckNamesResponse(Some(List("Default")), None))
    val fields = writeToString(
      ModelFieldNamesResponse(Some(List("Text", "Back Extra", "Translation")), None)
    )
    val invalidTemplates = writeToString(
      ModelTemplatesResponse(
        Some(Map("Card 1" -> Map("Front" -> "{{Text}}", "Back" -> "{{Back Extra}}"))),
        None
      )
    )
    withServer(
      Map("/" -> List(StubResponse(decks), StubResponse(fields), StubResponse(invalidTemplates)))
    ): server =>
      val clients: AnkiGateway = new ExternalClients(testConfig(server.baseUri))
      val error = intercept[IllegalStateException]:
        clients.validateDestination("Default").unsafeRunSync()
      assert(error.getMessage.contains("must cloze Text and render Translation"))

  test("storeMediaFile and addNote send exact AnkiConnect payloads"):
    withServer(
      Map(
        "/" -> List(
          StubResponse("""{"result":"clip-1.mp3","error":null}"""),
          StubResponse("""{"result":987654321,"error":null}""")
        )
      )
    ): server =>
      val clients = new ExternalClients(
        testConfig(
          server.baseUri,
          deckName = "Frontend Default",
          ankiModelName = "Cloze Plus"
        )
      )
      val fields = Map(
        "Text" -> "alpha {{c1::beta}} gamma[sound:clip-1.mp3]",
        "Back Extra" -> "context",
        "Translation" -> "second letter : alpha second letter gamma"
      )
      val tags = List("media-reviewer", "language::grc")

      val stored = clients
        .storeMediaFile("clip-1.mp3", Array[Byte](0, 1, -1))
        .unsafeRunSync()
      val added = clients.addNote("Study Deck", fields, tags).unsafeRunSync()

      assertEquals(stored, "clip-1.mp3")
      assertEquals(added, Right(987654321L))
      assertEquals(
        readFromString[StoreMediaRequest](server.requests.head.body),
        StoreMediaRequest(
          "storeMediaFile",
          6,
          StoreMediaParams("clip-1.mp3", "AAH/")
        )
      )
      assertEquals(
        readFromString[AddNoteRequest](server.requests(1).body),
        AddNoteRequest(
          "addNote",
          6,
          AddNoteParams(
            Note(
              deckName = "Study Deck",
              modelName = "Cloze Plus",
              fields = fields,
              options = NoteOptions(allowDuplicate = false),
              tags = tags
            )
          )
        )
      )

  test("addNote distinguishes duplicate, definite API error, and missing note ID"):
    val duplicateError = "cannot create note because it is a Duplicate"
    withServer(
      Map(
        "/" -> List(
          StubResponse(s"{\"result\":null,\"error\":\"$duplicateError\"}"),
          StubResponse("{\"result\":null,\"error\":\"duplicate check failed\"}"),
          StubResponse("{\"result\":null,\"error\":\"deck was not found\"}"),
          StubResponse("""{"result":null,"error":null}""")
        )
      )
    ): server =>
      val clients = new ExternalClients(testConfig(server.baseUri))

      assertEquals(
        clients.addNote("Deck One", Map("Text" -> "one"), Nil).unsafeRunSync(),
        Left(duplicateError)
      )
      val ambiguousDuplicate = intercept[IllegalStateException]:
        clients.addNote("Deck Two", Map("Text" -> "two"), Nil).unsafeRunSync()
      assertEquals(
        ambiguousDuplicate.getMessage,
        "AnkiConnect addNote failed: duplicate check failed"
      )
      val nonDuplicate = intercept[IllegalStateException]:
        clients.addNote("Deck Three", Map("Text" -> "three"), Nil).unsafeRunSync()
      assertEquals(nonDuplicate.getMessage, "AnkiConnect addNote failed: deck was not found")
      val missingResult = intercept[AnkiOutcomeUnknown]:
        clients.addNote("Deck Four", Map("Text" -> "four"), Nil).unsafeRunSync()
      assertEquals(
        missingResult.getMessage,
        "AnkiConnect addNote outcome is unknown: response contained neither an error nor a note ID"
      )
      assertEquals(server.requests.map(_.path), List.fill(4)("/"))
      assertEquals(
        server.requests.map(request => readFromString[AddNoteRequest](request.body).params.note.deckName),
        List("Deck One", "Deck Two", "Deck Three", "Deck Four")
      )

  test("addNote classifies malformed JSON and HTTP failures as unknown outcomes"):
    withServer(
      Map(
        "/" -> List(
          StubResponse("not JSON"),
          StubResponse("""{"error":"service unavailable"}""", status = 503)
        )
      )
    ): server =>
      val clients = new ExternalClients(testConfig(server.baseUri))

      val malformed = intercept[AnkiOutcomeUnknown]:
        clients.addNote("Deck One", Map("Text" -> "one"), Nil).unsafeRunSync()
      assert(malformed.getCause.isInstanceOf[IllegalStateException])
      assert(malformed.getMessage.contains("AnkiConnect returned invalid JSON"))

      val http = intercept[AnkiOutcomeUnknown]:
        clients.addNote("Deck Two", Map("Text" -> "two"), Nil).unsafeRunSync()
      assert(http.getCause.isInstanceOf[IllegalStateException])
      assert(http.getMessage.contains("AnkiConnect request failed (503): service unavailable"))

  test("addNote classifies a dropped request as an unknown outcome"):
    withServer(Map("/" -> List(StubResponse("", dropConnection = true)))): server =>
      val clients = new ExternalClients(testConfig(server.baseUri))

      val error = intercept[AnkiOutcomeUnknown]:
        clients.addNote("Study Deck", Map("Text" -> "one"), Nil).unsafeRunSync()

      assert(error.getCause != null)

  private def withServer[A](responses: Map[String, List[StubResponse]])(
      test: StubServer => A
  ): A =
    val server = new StubServer(responses)
    try test(server)
    finally server.close()

  private def testConfig(
      baseUri: URI,
      deckName: String = "Default",
      ankiModelName: String = "Cloze"
  ): Config =
    val defaults = Config.parse(List("artifact.json")) match
      case Right(Some(config)) => config
      case result              => throw new IllegalStateException(s"could not build test config: $result")
    defaults.copy(
      ankiUrl = baseUri,
      deckName = deckName,
      ankiModelName = ankiModelName
    )
