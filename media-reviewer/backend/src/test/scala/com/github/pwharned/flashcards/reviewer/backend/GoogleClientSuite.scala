package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.unsafe.implicits.global
import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}
import com.sun.net.httpserver.{HttpExchange, HttpServer}

import java.io.IOException
import java.net.{InetSocketAddress, URI, URLDecoder}
import java.net.http.HttpClient
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.{CopyOnWriteArrayList, CountDownLatch, Executors, TimeUnit}
import scala.jdk.CollectionConverters.*

private object GoogleClientTestSupport:
  final case class RecordedRequest(
      method: String,
      path: String,
      rawQuery: String,
      query: List[(String, String)],
      accept: Option[String],
      body: Array[Byte]
  )

  final case class StubResponse(
      bytes: Array[Byte],
      status: Int = 200,
      delayMillis: Long = 0,
      headers: Map[String, String] = Map.empty
  )

  object StubResponse:
    def json(
        value: String,
        status: Int = 200,
        delayMillis: Long = 0,
        headers: Map[String, String] = Map.empty
    ): StubResponse =
      StubResponse(
        value.getBytes(StandardCharsets.UTF_8),
        status,
        delayMillis,
        Map("Content-Type" -> "application/json; charset=utf-8") ++ headers
      )

    def audio(
        value: Array[Byte],
        status: Int = 200,
        delayMillis: Long = 0,
        headers: Map[String, String] = Map.empty
    ): StubResponse =
      StubResponse(
        value,
        status,
        delayMillis,
        Map("Content-Type" -> "audio/mpeg") ++ headers
      )

  final class StubServer(respond: RecordedRequest => StubResponse) extends AutoCloseable:
    private val recorded = new CopyOnWriteArrayList[RecordedRequest]()
    private val executor = Executors.newCachedThreadPool()
    private val server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0)
    server.createContext("/", (exchange: HttpExchange) => handle(exchange))
    server.setExecutor(executor)
    server.start()

    private val baseUri = URI.create(s"http://127.0.0.1:${server.getAddress.getPort}")

    val translationUri: URI = baseUri.resolve("/translate")
    val speechUri: URI = baseUri.resolve("/tts")

    def requests: List[RecordedRequest] = recorded.asScala.toList
    def requestsFor(path: String): List[RecordedRequest] = requests.filter(_.path == path)

    override def close(): Unit =
      server.stop(0)
      executor.shutdownNow()
      ()

    private def handle(exchange: HttpExchange): Unit =
      try
        val input = exchange.getRequestBody
        val body = try input.readAllBytes() finally input.close()
        val rawQuery = Option(exchange.getRequestURI.getRawQuery).getOrElse("")
        val request = RecordedRequest(
          exchange.getRequestMethod,
          exchange.getRequestURI.getPath,
          rawQuery,
          parseQuery(rawQuery),
          Option(exchange.getRequestHeaders.getFirst("Accept")),
          body
        )
        recorded.add(request)
        val response = respond(request)
        if response.delayMillis > 0 then Thread.sleep(response.delayMillis)
        response.headers.foreach((name, value) => exchange.getResponseHeaders.set(name, value))
        exchange.sendResponseHeaders(response.status, response.bytes.length.toLong)
        val output = exchange.getResponseBody
        try output.write(response.bytes)
        finally output.close()
      catch
        case _: IOException          => ()
        case _: InterruptedException => Thread.currentThread().interrupt()
      finally exchange.close()

  def client(
      server: StubServer,
      timeout: Duration = Duration.ofSeconds(2),
      overallTimeout: Duration = GoogleClient.DefaultOverallTimeout
  ): GoogleClient =
    val httpClient = HttpClient
      .newBuilder()
      .followRedirects(HttpClient.Redirect.NEVER)
      .build()
    GoogleClient.withEndpoints(
      server.translationUri,
      server.speechUri,
      httpClient,
      timeout,
      overallTimeout
    )

  def translationJson(parts: List[String], detected: Option[String]): String =
    val segments = parts
      .map(part => s"[${jsonString(part)},null,17,{\"ignored\":true}]")
      .mkString(",")
    s"[[$segments],null,${detected.fold("null")(jsonString)},42,{\"extra\":[true,false,null]}]"

  def objectTranslationJson(text: String, detected: String): String =
    s"{\"data\":{\"translations\":[{\"translatedText\":${jsonString(text)}," +
      s"\"detectedSourceLanguage\":${jsonString(detected)},\"confidence\":0.9}]}}"

  def queryValue(request: RecordedRequest, name: String): String =
    request.query.collectFirst { case (`name`, value) => value }.getOrElse("")

  private def parseQuery(rawQuery: String): List[(String, String)] =
    if rawQuery.isEmpty then Nil
    else
      rawQuery.split("&", -1).toList.map: field =>
        val separator = field.indexOf('=')
        val name = if separator < 0 then field else field.substring(0, separator)
        val value = if separator < 0 then "" else field.substring(separator + 1)
        decode(name) -> decode(value)

  private def decode(value: String): String =
    URLDecoder.decode(value, StandardCharsets.UTF_8)

  private def jsonString(value: String): String =
    val output = new StringBuilder("\"")
    value.foreach:
      case '"'  => output.append("\\\"")
      case '\\' => output.append("\\\\")
      case '\b' => output.append("\\b")
      case '\f' => output.append("\\f")
      case '\n' => output.append("\\n")
      case '\r' => output.append("\\r")
      case '\t' => output.append("\\t")
      case character if character < ' ' => output.append(f"\\u${character.toInt}%04x")
      case character => output.append(character)
    output.append('"').result()

class GoogleClientSuite extends munit.FunSuite:
  import GoogleClientTestSupport.*

  private val openMarker = "\u27e6"
  private val closeMarker = "\u27e7"

  test("prepare uses exact Google queries, marks the selected occurrence, and runs post-detection work concurrently"):
    val sentence = "uno dos uno"
    val marked = s"uno dos ${openMarker}uno$closeMarker"
    val selection = SelectionSpan(8, 11)
    val contextStarted = new CountDownLatch(1)
    val speechStarted = new CountDownLatch(1)
    val audio = Array[Byte](0, 1, -1)

    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(translationJson(List("The first ", "and last"), Some("ES")))
          case "/translate" =>
            contextStarted.countDown()
            if speechStarted.await(2, TimeUnit.SECONDS) then
              StubResponse.json(
                translationJson(List(s"before $openMarker", s"the latter$closeMarker after"), Some("es"))
              )
            else StubResponse.json("{\"error\":\"speech was not concurrent\"}", status = 500)
          case "/tts" =>
            speechStarted.countDown()
            if contextStarted.await(2, TimeUnit.SECONDS) then StubResponse.audio(audio)
            else StubResponse.json("{\"error\":\"context was not concurrent\"}", status = 500)
          case _ => StubResponse.json("{\"error\":\"unexpected path\"}", status = 500)
    ): server =>
      val result = client(server)
        .prepare(sentence, selection, artifactLanguage = "FR_ca")
        .unsafeRunSync()

      assertEquals(result.enrichment, Enrichment("the latter", "The first and last"))
      assertEquals(result.sourceLanguage, "es")
      assertEquals(result.contentType, "audio/mpeg")
      assertEquals(result.audioBytes.toSeq, audio.toSeq)
      val exposedAudio = result.audioBytes
      exposedAudio(0) = 99
      assert(!(exposedAudio eq result.audioBytes))
      assertEquals(result.audioBytes.toSeq, audio.toSeq)
      assertEquals(result.filename, "clausula_es_85ba0c2dabc1070f3061.mp3")
      assertEquals(
        result.sha256,
        "26a66b061e8f48f39927c312f25293959729eee95978e2892d49d3512a5cc092"
      )

      val translations = server.requestsFor("/translate")
      assertEquals(translations.map(_.method), List("GET", "GET"))
      assertEquals(translations.map(_.body.toSeq), List(Seq.empty, Seq.empty))
      assertEquals(
        translations.map(_.query),
        List(
          List("client" -> "gtx", "sl" -> "auto", "tl" -> "en", "dt" -> "t", "q" -> sentence),
          List("client" -> "gtx", "sl" -> "es", "tl" -> "en", "dt" -> "t", "q" -> marked)
        )
      )
      assertEquals(
        translations.map(_.rawQuery),
        List(
          "client=gtx&sl=auto&tl=en&dt=t&q=uno+dos+uno",
          "client=gtx&sl=es&tl=en&dt=t&q=uno+dos+%E2%9F%A6uno%E2%9F%A7"
        )
      )
      assertEquals(translations.map(_.accept), List(Some("application/json"), Some("application/json")))

      val speech = server.requestsFor("/tts")
      assertEquals(speech.map(_.method), List("GET"))
      assertEquals(
        speech.map(_.query),
        List(List("ie" -> "UTF-8", "client" -> "tw-ob", "tl" -> "es", "q" -> sentence))
      )
      assertEquals(speech.map(_.rawQuery), List("ie=UTF-8&client=tw-ob&tl=es&q=uno+dos+uno"))
      assertEquals(speech.map(_.accept), List(Some("audio/mpeg")))

  test("context translation falls back to the isolated exact selection when markers do not survive"):
    val sentence = "hola mundo"
    val marked = s"${openMarker}hola$closeMarker mundo"

    withServer(
      request =>
        (request.path, queryValue(request, "q")) match
          case ("/translate", `sentence`) =>
            StubResponse.json(objectTranslationJson("hello world", "es_MX"))
          case ("/translate", `marked`) =>
            StubResponse.json(translationJson(List("hello world"), None))
          case ("/translate", "hola") =>
            StubResponse.json(objectTranslationJson("hello", "es-MX"))
          case ("/tts", _) => StubResponse.audio(Array[Byte](7))
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val result = client(server)
        .prepare(sentence, SelectionSpan(0, 4), artifactLanguage = "pt")
        .unsafeRunSync()

      assertEquals(result.enrichment, Enrichment("hello", "hello world"))
      assertEquals(result.sourceLanguage, "es-mx")
      val translations = server.requestsFor("/translate")
      assertEquals(translations.map(request => queryValue(request, "q")), List(sentence, marked, "hola"))
      assertEquals(translations.map(request => queryValue(request, "sl")), List("auto", "es-mx", "es-mx"))
      assert(translations.forall(request => queryValue(request, "tl") == "en"))
      assertEquals(
        server.requestsFor("/tts").head.query,
        List("ie" -> "UTF-8", "client" -> "tw-ob", "tl" -> "es-mx", "q" -> sentence)
      )

  test("context marking avoids delimiter collisions and still marks the selected repeated occurrence"):
    val selected = openMarker + "uno" + closeMarker
    val sentence = selected + " then " + selected
    val start = sentence.lastIndexOf(selected)
    val alternativeOpen = "\u27ea"
    val alternativeClose = "\u27eb"
    val marked =
      sentence.substring(0, start) + alternativeOpen + selected + alternativeClose +
        sentence.substring(start + selected.length)
    val contextualResult =
      openMarker + "wrong" + closeMarker + " then " + alternativeOpen + "the latter" +
        alternativeClose

    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(translationJson(List("first then second"), Some("es")))
          case "/translate" =>
            StubResponse.json(translationJson(List(contextualResult), Some("es")))
          case "/tts" => StubResponse.audio(Array[Byte](1))
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val result = client(server)
        .prepare(sentence, SelectionSpan(start, start + selected.length), "es")
        .unsafeRunSync()

      assertEquals(result.enrichment.targetGloss, "the latter")
      val translations = server.requestsFor("/translate")
      assertEquals(translations.size, 2)
      assertEquals(queryValue(translations(1), "q"), marked)

  test("root arrays never infer source language from a third translation segment"):
    val sentence = "uno dos tres"

    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(
              translationJson(List("one ", "two ", "third-segment"), None)
            )
          case "/translate" =>
            StubResponse.json(
              translationJson(List(s"$openMarker one $closeMarker two three"), None)
            )
          case "/tts" => StubResponse.audio(Array[Byte](1))
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val result = client(server)
        .prepare(sentence, SelectionSpan(0, 3), artifactLanguage = "fr")
        .unsafeRunSync()

      assertEquals(result.sourceLanguage, "fr")
      assertEquals(result.enrichment.sentenceTranslation, "one two third-segment")
      val contextual = server.requestsFor("/translate")(1)
      assertEquals(queryValue(contextual, "sl"), "fr")
      assertEquals(queryValue(server.requestsFor("/tts").head, "tl"), "fr")

  test("speech chunks prefer whitespace, preserve surrogate pairs, remain sequential, and concatenate bytes"):
    val firstChunk = "sel " + "a" * 166
    val secondChunk = "b" * 179
    val finalChunk = "\ud83d\ude00" + "c" * 5
    val sentence = firstChunk + " " + secondChunk + finalChunk
    val expectedChunks = List(firstChunk, secondChunk, finalChunk)
    val parts = Vector(Array[Byte](1, 2), Array[Byte](3), Array[Byte](4, 5))
    val partIndex = new AtomicInteger(0)
    val active = new AtomicInteger(0)
    val maximumActive = new AtomicInteger(0)

    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(translationJson(List("whole sentence"), Some("ja")))
          case "/translate" =>
            StubResponse.json(translationJson(List(s"$openMarker selection $closeMarker"), Some("ja")))
          case "/tts" =>
            val nowActive = active.incrementAndGet()
            maximumActive.accumulateAndGet(nowActive, Math.max)
            try
              Thread.sleep(20)
              StubResponse.audio(parts(partIndex.getAndIncrement()))
            finally active.decrementAndGet()
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val result = client(server)
        .prepare(sentence, SelectionSpan(0, 3), artifactLanguage = "ja")
        .unsafeRunSync()

      val requests = server.requestsFor("/tts")
      val chunks = requests.map(request => queryValue(request, "q"))
      assertEquals(chunks, expectedChunks)
      assert(chunks.forall(_.length <= GoogleClient.MaxTtsChunkUtf16))
      assert(chunks.forall(chunk => !Character.isHighSurrogate(chunk.last)))
      assert(chunks.forall(chunk => !Character.isLowSurrogate(chunk.head)))
      assertEquals(maximumActive.get(), 1)
      assertEquals(result.audioBytes.toSeq, Seq[Byte](1, 2, 3, 4, 5))

  test("input, endpoint, source-language, and JSON failures are explicit"):
    withServer(_ => StubResponse.json("[]", status = 500)): server =>
      val gateway = client(server)
      val tooLong = "x" * (GoogleClient.MaxSentenceUtf16 + 1)
      val cases = List(
        ("", SelectionSpan(0, 1), "es", "sentence must not be empty"),
        ("x", SelectionSpan(0, 1), "   ", "artifact language must not be empty"),
        ("x", SelectionSpan(0, 2), "es", "invalid exact selection span"),
        (" x", SelectionSpan(0, 1), "es", "selection must not be empty"),
        ("x\ud800", SelectionSpan(0, 1), "es", "sentence contains malformed UTF-16"),
        ("x", SelectionSpan(0, 1), "\udc00", "artifact language contains malformed UTF-16"),
        (tooLong, SelectionSpan(0, 1), "es", "UTF-16 unit limit")
      )

      cases.foreach: (sentence, selection, language, expected) =>
        val error = intercept[IllegalArgumentException]:
          gateway.prepare(sentence, selection, language).unsafeRunSync()
        assert(error.getMessage.contains(expected), error.getMessage)
      assertEquals(server.requests, Nil)

    withServer(
      request =>
        if request.path == "/translate" then
          StubResponse.json(translationJson(List("x"), None))
        else StubResponse.audio(Array[Byte](1))
    ): server =>
      val error = intercept[IllegalStateException]:
        client(server).prepare("x", SelectionSpan(0, 1), "auto").unsafeRunSync()
      assert(error.getMessage.contains("did not detect a source language"))
      assertEquals(server.requests.map(_.path), List("/translate"))

    withServer(_ => StubResponse.json("not-json")): server =>
      val error = intercept[IllegalStateException]:
        client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
      assert(error.getMessage.contains("invalid translation response"))

    withServer(_ => StubResponse.json("[]")): server =>
      val redirectingClient = HttpClient
        .newBuilder()
        .followRedirects(HttpClient.Redirect.ALWAYS)
        .build()
      val error = intercept[IllegalArgumentException]:
        GoogleClient.withEndpoints(
          server.translationUri,
          server.speechUri,
          redirectingClient
        )
      assert(error.getMessage.contains("must not follow redirects"))

    assertEquals(
      GoogleClient.LiveTranslationEndpoint,
      URI.create("https://translate.googleapis.com/translate_a/single")
    )
    assertEquals(
      GoogleClient.LiveSpeechEndpoint,
      URI.create("https://translate.googleapis.com/translate_tts")
    )

  test("responses require status 200 and the service-specific Content-Type"):
    val translationBody = translationJson(List("x"), Some("es")).getBytes(StandardCharsets.UTF_8)

    withServer(_ => StubResponse.json(translationJson(List("x"), Some("es")), status = 206)):
      server =>
        val error = intercept[IllegalStateException]:
          client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
        assert(error.getMessage.contains("HTTP status 206"), error.getMessage)

    val translationMimeCases = List(
      StubResponse(translationBody) -> "no Content-Type",
      StubResponse(translationBody, headers = Map("Content-Type" -> "text/plain")) ->
        "expected application/json"
    )
    translationMimeCases.foreach: (response, expectedMessage) =>
      withServer(_ => response): server =>
        val error = intercept[IllegalStateException]:
          client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
        assert(error.getMessage.contains(expectedMessage), error.getMessage)

    def withSpeechResponse(speechResponse: StubResponse)(request: RecordedRequest): StubResponse =
      request.path match
        case "/translate" if queryValue(request, "sl") == "auto" =>
          StubResponse.json(translationJson(List("sentence"), Some("es")))
        case "/translate" =>
          StubResponse.json(translationJson(List(s"$openMarker x $closeMarker"), Some("es")))
        case "/tts" => speechResponse
        case _ => StubResponse.json("[]", status = 500)

    val speechCases = List(
      StubResponse(Array[Byte](1)) -> "no Content-Type",
      StubResponse(Array[Byte](1), headers = Map("Content-Type" -> "application/octet-stream")) ->
        "expected audio/mpeg",
      StubResponse.audio(Array[Byte](1), status = 206) -> "HTTP status 206"
    )
    speechCases.foreach: (response, expectedMessage) =>
      withServer(withSpeechResponse(response)): server =>
        val error = intercept[IllegalStateException]:
          client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
        assert(error.getMessage.contains(expectedMessage), error.getMessage)

  test("HTTP status, redirects, request timeout, empty audio, and total audio size are enforced"):
    withServer(_ => StubResponse.json("busy", status = 503)): server =>
      val error = intercept[IllegalStateException]:
        client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
      assert(error.getMessage.contains("HTTP status 503"), error.getMessage)

    withServer(_ => StubResponse.json("moved", status = 302, headers = Map("Location" -> "/elsewhere"))):
      server =>
        val error = intercept[IllegalStateException]:
          client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
        assert(error.getMessage.contains("redirects are not allowed"), error.getMessage)
        assertEquals(server.requests.map(_.path), List("/translate"))

    withServer(_ => StubResponse.json(translationJson(List("x"), Some("es")), delayMillis = 250)):
      server =>
        val error = intercept[IllegalStateException]:
          client(server, Duration.ofMillis(40))
            .prepare("x", SelectionSpan(0, 1), "es")
            .unsafeRunSync()
        assert(error.getMessage.contains("timed out"), error.getMessage)

    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(translationJson(List("sentence"), Some("es")))
          case "/translate" =>
            StubResponse.json(translationJson(List(s"$openMarker x $closeMarker"), Some("es")))
          case "/tts" => StubResponse.audio(Array.emptyByteArray)
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val error = intercept[IllegalStateException]:
        client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
      assert(error.getMessage.contains("empty audio"), error.getMessage)

    val firstPart = Array.fill[Byte](6 * 1024 * 1024)(1)
    val secondPart = Array.fill[Byte](5 * 1024 * 1024)(2)
    val speechIndex = new AtomicInteger(0)
    val longSentence = "x" * 181
    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(translationJson(List("sentence"), Some("es")))
          case "/translate" =>
            StubResponse.json(translationJson(List(s"$openMarker x $closeMarker"), Some("es")))
          case "/tts" =>
            if speechIndex.getAndIncrement() == 0 then StubResponse.audio(firstPart)
            else StubResponse.audio(secondPart)
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val error = intercept[IllegalStateException]:
        client(server, Duration.ofSeconds(5))
          .prepare(longSentence, SelectionSpan(0, 1), "es")
          .unsafeRunSync()
      assert(error.getMessage.contains("10 MiB"), error.getMessage)
      assertEquals(server.requestsFor("/tts").size, 2)

  test("the overall preparation deadline interrupts an in-flight synchronous request"):
    val speechStarted = new CountDownLatch(1)
    val sentence = "x" * 181

    withServer(
      request =>
        request.path match
          case "/translate" if queryValue(request, "sl") == "auto" =>
            StubResponse.json(translationJson(List("sentence"), Some("es")))
          case "/translate" =>
            StubResponse.json(translationJson(List(s"$openMarker x $closeMarker"), Some("es")))
          case "/tts" =>
            speechStarted.countDown()
            StubResponse.audio(Array[Byte](1), delayMillis = 5000)
          case _ => StubResponse.json("[]", status = 500)
    ): server =>
      val startedAt = System.nanoTime()
      val error = intercept[IllegalStateException]:
        client(
          server,
          timeout = Duration.ofSeconds(10),
          overallTimeout = Duration.ofSeconds(1)
        ).prepare(sentence, SelectionSpan(0, 1), "es").unsafeRunSync()
      val elapsedMillis = (System.nanoTime() - startedAt) / 1000000L

      assert(error.getMessage.contains("preparation timed out"), error.getMessage)
      assertEquals(speechStarted.getCount, 0L)
      assert(elapsedMillis < 3000, s"cancellation took $elapsedMillis ms")

  private def withServer[A](respond: RecordedRequest => StubResponse)(test: StubServer => A): A =
    val server = new StubServer(respond)
    try test(server)
    finally server.close()
