package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.effect.unsafe.implicits.global
import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}
import com.sun.net.httpserver.{HttpExchange, HttpServer}

import java.io.IOException
import java.net.{InetSocketAddress, URI}
import java.net.http.{HttpClient, WebSocket}
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.concurrent.atomic.{AtomicInteger, AtomicLong}
import java.util.concurrent.{
  CancellationException,
  CompletableFuture,
  CopyOnWriteArrayList,
  CountDownLatch,
  ExecutionException,
  Executors,
  TimeUnit
}
import scala.jdk.CollectionConverters.*

private object GoogleClientTestSupport:
  final case class RecordedRequest(
      method: String,
      path: String,
      rawQuery: String,
      headers: Map[String, List[String]],
      body: Array[Byte]
  ):
    def header(name: String): Option[String] =
      headers.get(name.toLowerCase(java.util.Locale.ROOT)).flatMap(_.headOption)

    def bodyText: String = new String(body, StandardCharsets.UTF_8)

  final case class StubResponse(
      bytes: Array[Byte],
      status: Int = 200,
      delayMillis: Long = 0,
      headers: Map[String, String] = Map.empty
  )

  object StubResponse:
    def batch(
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

    def text(value: String, status: Int, delayMillis: Long = 0): StubResponse =
      StubResponse(
        value.getBytes(StandardCharsets.UTF_8),
        status,
        delayMillis,
        Map("Content-Type" -> "text/plain; charset=utf-8")
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
        val headers = exchange.getRequestHeaders.asScala.iterator.map: (name, values) =>
          name.toLowerCase(java.util.Locale.ROOT) -> values.asScala.toList
        val request = RecordedRequest(
          exchange.getRequestMethod,
          exchange.getRequestURI.getPath,
          Option(exchange.getRequestURI.getRawQuery).getOrElse(""),
          headers.toMap,
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

  final class RecordingMicrosoftTransport(result: IO[Array[Byte]])
      extends MicrosoftSpeechTransport:
    private val recorded = new CopyOnWriteArrayList[MicrosoftSpeechRequest]()

    def requests: List[MicrosoftSpeechRequest] = recorded.asScala.toList

    override def synthesize(request: MicrosoftSpeechRequest): IO[Array[Byte]] =
      IO.delay:
        recorded.add(request)
        ()
      .flatMap(_ => result)

  final class FakeWebSocket(pauseFirstText: Boolean = false) extends WebSocket:
    private val texts = new CopyOnWriteArrayList[(String, Boolean)]()
    private val closes = new CopyOnWriteArrayList[(Int, String)]()
    private val requestedTotal = new AtomicLong(0L)
    private val requestCallCount = new AtomicInteger(0)
    private val abortCallCount = new AtomicInteger(0)
    private val firstTextResult = new CompletableFuture[WebSocket]()

    def sentTexts: Vector[(String, Boolean)] = texts.asScala.toVector
    def closeRequests: Vector[(Int, String)] = closes.asScala.toVector
    def requested: Long = requestedTotal.get()
    def requestCalls: Int = requestCallCount.get()
    def abortCalls: Int = abortCallCount.get()

    def completeFirstText(): Unit =
      firstTextResult.complete(this)
      ()

    def failFirstText(error: Throwable): Unit =
      firstTextResult.completeExceptionally(error)
      ()

    override def sendText(data: CharSequence, last: Boolean): CompletableFuture[WebSocket] =
      val index = texts.size()
      texts.add(data.toString -> last)
      if pauseFirstText && index == 0 then firstTextResult
      else CompletableFuture.completedFuture(this)

    override def sendBinary(data: ByteBuffer, last: Boolean): CompletableFuture[WebSocket] =
      CompletableFuture.completedFuture(this)

    override def sendPing(message: ByteBuffer): CompletableFuture[WebSocket] =
      CompletableFuture.completedFuture(this)

    override def sendPong(message: ByteBuffer): CompletableFuture[WebSocket] =
      CompletableFuture.completedFuture(this)

    override def sendClose(statusCode: Int, reason: String): CompletableFuture[WebSocket] =
      closes.add(statusCode -> reason)
      CompletableFuture.completedFuture(this)

    override def request(n: Long): Unit =
      requestedTotal.addAndGet(n)
      requestCallCount.incrementAndGet()
      ()

    override def getSubprotocol(): String = ""
    override def isOutputClosed(): Boolean = closes.size() > 0 || abortCalls > 0
    override def isInputClosed(): Boolean = abortCalls > 0

    override def abort(): Unit =
      abortCallCount.incrementAndGet()
      ()

  val OpenMarker = "\u27e6"
  val CloseMarker = "\u27e7"

  val ExactSpecialRpc: String =
    "f.req=%5B%5B%5B%22MkEWBc%22%2C%22%5B%5B%5C%22uno%20dos%20%2B%20!'()" +
      "%5C%22%2C%5C%22auto%5C%22%2C%5C%22en%5C%22%2Ctrue%5D%2C%5B1%5D%5D" +
      "%22%2Cnull%2C%22generic%22%5D%5D%5D&"

  def client(
      server: StubServer,
      timeout: Duration = Duration.ofSeconds(2),
      overallTimeout: Duration = GoogleClient.DefaultOverallTimeout
  ): GoogleClient =
    GoogleClient.withEndpoints(
      server.translationUri,
      server.speechUri,
      HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build(),
      timeout,
      overallTimeout
    )

  def microsoftClient(
      server: StubServer,
      transport: MicrosoftSpeechTransport,
      timeout: Duration = Duration.ofSeconds(2),
      overallTimeout: Duration = GoogleClient.DefaultOverallTimeout,
      now: () => Long = () => 1704164645678L,
      randomId: () => String = () => "11111111111111111111111111111111",
      muidBytes: () => Array[Byte] = () => Array.tabulate[Byte](16)(_.toByte)
  ): GoogleClient =
    GoogleClient.withMicrosoftTransport(
      server.translationUri,
      server.speechUri,
      HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build(),
      transport,
      timeout,
      overallTimeout,
      now,
      randomId,
      muidBytes
    )

  def batchexecute(parts: List[String], detected: Option[String]): String =
    val response = parts.map(part => s"[${jsonString(part)},null]").mkString("[", ",", "]")
    wrapInner(s"[[null,null,${detected.fold("null")(jsonString)}],[$response]]")

  def nestedBatchexecute(parts: List[String], detected: Option[String]): String =
    val sentences = parts.map(part => s"[${jsonString(part)},null]").mkString("[", ",", "]")
    val response = s"[[null,null,null,null,null,$sentences]]"
    wrapInner(s"[[null,null,${detected.fold("null")(jsonString)}],[$response]]")

  def binaryFrame(header: String, payload: Array[Byte]): Array[Byte] =
    val headerBytes = header.getBytes(StandardCharsets.UTF_8)
    Array(((headerBytes.length >>> 8) & 0xff).toByte, (headerBytes.length & 0xff).toByte) ++
      headerBytes ++ payload

  def listenerRequest(
      messages: Vector[String] = Vector("speech.config", "ssml"),
      maxAudioBytes: Int = 16
  ): MicrosoftSpeechRequest =
    MicrosoftSpeechRequest(
      URI.create("wss://example.test/speech"),
      Vector.empty,
      messages,
      Duration.ofSeconds(1),
      maxAudioBytes
    )

  def successfulTranslations(speechResponse: StubResponse): RecordedRequest => StubResponse =
    val translationIndex = new AtomicInteger(0)
    request =>
      request.path match
        case "/translate" if translationIndex.getAndIncrement() == 0 =>
          StubResponse.batch(batchexecute(List("sentence"), Some("es")))
        case "/translate" =>
          StubResponse.batch(batchexecute(List(s"$OpenMarker x $CloseMarker"), Some("es")))
        case "/tts" => speechResponse
        case _ => StubResponse.text("unexpected", 500)

  private def wrapInner(inner: String): String =
    s")]}'\n42\n[[\"wrb.fr\",\"MkEWBc\",${jsonString(inner)},null,\"generic\"]]\n"

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

  test("MkEWBc requests use Clausula's exact body, headers, parser, and one-request TTS URL"):
    val sentence = "  uno dos + !'()  "
    val selection = SelectionSpan(2, 5)
    val marked = s"  $OpenMarker${sentence.substring(2, 5)}$CloseMarker${sentence.substring(5)}"
    val contextStarted = new CountDownLatch(1)
    val speechStarted = new CountDownLatch(1)
    val translationIndex = new AtomicInteger(0)
    val audio = Array[Byte](0, 1, -1)

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List(" sentence ", "translation "), Some("ES")))
          case "/translate" =>
            contextStarted.countDown()
            if speechStarted.await(2, TimeUnit.SECONDS) then
              StubResponse.batch(
                batchexecute(List(s"before $OpenMarker", s"the latter$CloseMarker after"), Some("es"))
              )
            else StubResponse.text("speech was not concurrent", 500)
          case "/tts" =>
            speechStarted.countDown()
            if contextStarted.await(2, TimeUnit.SECONDS) then StubResponse.audio(audio)
            else StubResponse.text("context was not concurrent", 500)
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val result = client(server).prepare(sentence, selection, "FR_ca").unsafeRunSync()

      assertEquals(result.enrichment, Enrichment("the latter", "sentence translation"))
      assertEquals(result.sourceLanguage, "es")
      assertEquals(result.filename, "clausula_es_7e2b4fc52d00389a.mp3")
      assertEquals(result.audioBytes.toSeq, audio.toSeq)
      val exposed = result.audioBytes
      exposed(0) = 99
      assertEquals(result.audioBytes.toSeq, audio.toSeq)

      val translations = server.requestsFor("/translate")
      assertEquals(translations.map(_.method), List("POST", "POST"))
      assertEquals(translations.head.bodyText, ExactSpecialRpc)
      assertEquals(
        translations(1).bodyText,
        GoogleTranslationRpc.packageRpc(marked, "es", "en")
      )
      translations.foreach: request =>
        assertEquals(request.rawQuery, "")
        assertEquals(request.header("Content-Type"), Some(GoogleClient.TranslationContentType))
        assertEquals(request.header("User-Agent"), Some(GoogleClient.UserAgent))
        assertEquals(request.header("Referer"), Some(GoogleClient.Referer))
        assertEquals(request.header("Accept"), None)

      val speech = server.requestsFor("/tts")
      assertEquals(speech.size, 1)
      assertEquals(speech.head.method, "GET")
      assertEquals(
        speech.head.rawQuery,
        "ie=UTF-8&q=%20%20uno%20dos%20%2B%20!'()%20%20&tl=es&client=tw-ob"
      )
      assertEquals(speech.head.header("Referer"), Some(GoogleClient.Referer))
      assertEquals(speech.head.header("User-Agent"), Some(GoogleClient.UserAgent))
      assertEquals(speech.head.header("Accept"), None)
      assertEquals(speech.head.header("Content-Type"), None)

  test("packageRpc and batchexecute parsing match Clausula's exact shapes"):
    assertEquals(
      GoogleTranslationRpc.packageRpc("  uno dos + !'()  ", "auto", "en"),
      ExactSpecialRpc
    )
    assertEquals(
      JavaScriptEncoding.encodeURIComponent("a b+c/?!~*'()"),
      "a%20b%2Bc%2F%3F!~*'()"
    )

    val multi = batchexecute(List(" first ", " second ", "third"), Some("FA"))
    assertEquals(
      GoogleTranslationJson.parse(multi.getBytes(StandardCharsets.UTF_8)),
      Right("first second third" -> Some("FA"))
    )

    val nested = nestedBatchexecute(List(" one ", " two "), None)
    assertEquals(
      GoogleTranslationJson.parse(nested.getBytes(StandardCharsets.UTF_8)),
      Right("one two" -> None)
    )

    val missing = GoogleTranslationJson.parse("[]\n".getBytes(StandardCharsets.UTF_8))
    assert(missing.left.exists(_.contains("MkEWBc response line is missing")))
    val wrongShape = "[[\"wrb.fr\",\"MkEWBc\",7]]"
    assert(
      GoogleTranslationJson.parse(wrongShape.getBytes(StandardCharsets.UTF_8)).left
        .exists(_.contains("outer JSON[0][2] is not a string"))
    )

  test("HTTP 400 extracts xsrf from the read body and retries exactly once"):
    val translationIndex = new AtomicInteger(0)
    val token = "tok +/?!~"

    withServer(
      request =>
        request.path match
          case "/translate" =>
            translationIndex.getAndIncrement() match
              case 0 => StubResponse.text(s"prefix \"xsrf\",\"$token\" suffix", 400)
              case 1 => StubResponse.batch(batchexecute(List("hello"), Some("es")))
              case _ =>
                StubResponse.batch(batchexecute(List(s"$OpenMarker hello $CloseMarker"), Some("es")))
          case "/tts" => StubResponse.audio(Array[Byte](1))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      client(server).prepare("hola", SelectionSpan(0, 4), "es").unsafeRunSync()

      val requests = server.requestsFor("/translate")
      val base = GoogleTranslationRpc.packageRpc("hola", "auto", "en")
      assertEquals(requests.size, 3)
      assertEquals(requests(0).bodyText, base)
      assertEquals(requests(1).bodyText, base + "&at=tok%20%2B%2F%3F!~")
      assertEquals(
        requests(2).bodyText,
        GoogleTranslationRpc.packageRpc(s"${OpenMarker}hola$CloseMarker", "es", "en")
      )

    val attempts = new AtomicInteger(0)
    withServer(
      request =>
        if request.path == "/translate" then
          attempts.incrementAndGet()
          StubResponse.text("\"xsrf\",\"again\"", 400)
        else StubResponse.audio(Array[Byte](1))
    ): server =>
      val error = intercept[IllegalStateException]:
        client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
      assert(error.getMessage.contains("HTTP status 400"), error.getMessage)
      assertEquals(attempts.get(), 2)

  test("the contextual request marks the selected occurrence and falls back to isolated MkEWBc"):
    val sentence = "hola hola"
    val marked = s"hola ${OpenMarker}hola$CloseMarker"
    val translationIndex = new AtomicInteger(0)

    withServer(
      request =>
        request.path match
          case "/translate" =>
            translationIndex.getAndIncrement() match
              case 0 => StubResponse.batch(batchexecute(List("hello hello"), Some("es_MX")))
              case 1 => StubResponse.batch(batchexecute(List("hello hello"), None))
              case _ => StubResponse.batch(batchexecute(List("hello"), Some("es-MX")))
          case "/tts" => StubResponse.audio(Array[Byte](7))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val result = client(server)
        .prepare(sentence, SelectionSpan(5, 9), "pt")
        .unsafeRunSync()

      assertEquals(result.enrichment, Enrichment("hello", "hello hello"))
      assertEquals(result.sourceLanguage, "es-mx")
      assertEquals(
        server.requestsFor("/translate").map(_.bodyText),
        List(
          GoogleTranslationRpc.packageRpc(sentence, "auto", "en"),
          GoogleTranslationRpc.packageRpc(marked, "es-mx", "en"),
          GoogleTranslationRpc.packageRpc("hola", "es-mx", "en")
        )
      )
      assertEquals(server.requestsFor("/tts").size, 1)

  test("filenames hash the sentence through 200 UTF-16 units and the selection above 200"):
    val translationIndex = new AtomicInteger(0)
    val sentence200 = "word" + "x" * 196
    val sentence201 = "word" + "x" * 197

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() % 2 == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("es")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker word $CloseMarker"), Some("es")))
          case "/tts" => StubResponse.audio(Array[Byte](1))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val gateway = client(server)
      val atLimit = gateway.prepare(sentence200, SelectionSpan(0, 4), "es").unsafeRunSync()
      val aboveLimit = gateway.prepare(sentence201, SelectionSpan(0, 4), "es").unsafeRunSync()

      assertEquals(atLimit.filename, "clausula_es_390ad3d91946c395.mp3")
      assertEquals(aboveLimit.filename, "clausula_es_98c1eb4ee9347674.mp3")
      assertEquals(server.requestsFor("/tts").size, 2)

  test("translation and TTS require status 200 and service-specific MIME types"):
    val translationCases = List(
      StubResponse.text("busy", 503) -> "HTTP status 503",
      StubResponse(
        batchexecute(List("x"), Some("es")).getBytes(StandardCharsets.UTF_8)
      ) -> "no Content-Type",
      StubResponse(
        batchexecute(List("x"), Some("es")).getBytes(StandardCharsets.UTF_8),
        headers = Map("Content-Type" -> "text/plain")
      ) -> "expected application/json",
      StubResponse.text("moved", 302).copy(headers = Map("Location" -> "/elsewhere")) ->
        "redirects are not allowed"
    )
    translationCases.foreach: (response, expected) =>
      withServer(_ => response): server =>
        val error = intercept[IllegalStateException]:
          client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
        assert(error.getMessage.contains(expected), error.getMessage)

    val speechCases = List(
      StubResponse.audio(Array[Byte](1), status = 206) -> "HTTP status 206",
      StubResponse(Array[Byte](1)) -> "no Content-Type",
      StubResponse(Array[Byte](1), headers = Map("Content-Type" -> "application/octet-stream")) ->
        "expected audio/mpeg",
      StubResponse.audio(Array.emptyByteArray) -> "empty audio"
    )
    speechCases.foreach: (response, expected) =>
      withServer(successfulTranslations(response)): server =>
        val error = intercept[IllegalStateException]:
          client(server).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
        assert(error.getMessage.contains(expected), error.getMessage)

  test("response size, per-request timeout, and overall cancellation limits remain enforced"):
    val oversizedTranslation = StubResponse(
      Array.fill[Byte](2 * 1024 * 1024 + 1)('x'.toByte),
      headers = Map("Content-Type" -> "application/json")
    )
    withServer(_ => oversizedTranslation): server =>
      val error = intercept[IllegalStateException]:
        client(server, Duration.ofSeconds(5))
          .prepare("x", SelectionSpan(0, 1), "es")
          .unsafeRunSync()
      assert(error.getMessage.contains("response exceeded"), error.getMessage)

    val oversizedAudio = StubResponse.audio(
      Array.fill[Byte](GoogleClient.MaxAudioBytes + 1)(1)
    )
    withServer(successfulTranslations(oversizedAudio)): server =>
      val error = intercept[IllegalStateException]:
        client(server, Duration.ofSeconds(5))
          .prepare("x", SelectionSpan(0, 1), "es")
          .unsafeRunSync()
      assert(error.getMessage.contains("10 MiB"), error.getMessage)
      assertEquals(server.requestsFor("/tts").size, 1)

    withServer(
      _ => StubResponse.batch(batchexecute(List("x"), Some("es")), delayMillis = 250)
    ): server =>
      val error = intercept[IllegalStateException]:
        client(server, Duration.ofMillis(40))
          .prepare("x", SelectionSpan(0, 1), "es")
          .unsafeRunSync()
      assert(error.getMessage.contains("timed out"), error.getMessage)

    val speechStarted = new CountDownLatch(1)
    val translationIndex = new AtomicInteger(0)
    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("es")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker x $CloseMarker"), Some("es")))
          case "/tts" =>
            speechStarted.countDown()
            StubResponse.audio(Array[Byte](1), delayMillis = 5000)
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val startedAt = System.nanoTime()
      val error = intercept[IllegalStateException]:
        client(
          server,
          timeout = Duration.ofSeconds(10),
          overallTimeout = Duration.ofSeconds(1)
        ).prepare("x", SelectionSpan(0, 1), "es").unsafeRunSync()
      val elapsedMillis = (System.nanoTime() - startedAt) / 1000000L
      assert(error.getMessage.contains("preparation timed out"), error.getMessage)
      assertEquals(speechStarted.getCount, 0L)
      assert(elapsedMillis < 3000, s"cancellation took $elapsedMillis ms")

  test("input limits, endpoint policy, and live endpoints remain unchanged except provider URLs"):
    withServer(_ => StubResponse.text("unexpected", 500)): server =>
      val gateway = client(server)
      val cases = List(
        ("", SelectionSpan(0, 1), "es", "sentence must not be empty"),
        ("x", SelectionSpan(0, 1), " ", "artifact language must not be empty"),
        ("x\ud800", SelectionSpan(0, 1), "es", "malformed UTF-16"),
        ("x" * (GoogleClient.MaxSentenceUtf16 + 1), SelectionSpan(0, 1), "es", "UTF-16 unit limit")
      )
      cases.foreach: (sentence, selection, language, expected) =>
        val error = intercept[IllegalArgumentException]:
          gateway.prepare(sentence, selection, language).unsafeRunSync()
        assert(error.getMessage.contains(expected), error.getMessage)
      assertEquals(server.requests, Nil)

      val redirecting = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.ALWAYS).build()
      val redirectError = intercept[IllegalArgumentException]:
        GoogleClient.withEndpoints(server.translationUri, server.speechUri, redirecting)
      assert(redirectError.getMessage.contains("must not follow redirects"))

    assertEquals(
      GoogleClient.LiveTranslationEndpoint,
      URI.create("https://translate.google.com/_/TranslateWebserverUi/data/batchexecute")
    )
    assertEquals(
      GoogleClient.LiveSpeechEndpoint,
      URI.create("https://translate.googleapis.com/translate_tts")
    )

  test("Microsoft Edge protocol primitives match Clausula and parse audio framing"):
    val epochMillis = 1704164645678L
    assertEquals(MicrosoftSpeechProtocol.SecMsGecVersion, "1-143.0.3650.75")
    assertEquals(MicrosoftSpeechProtocol.TrustedClientToken, "6A5AA1D4EAFF4E9FB37E23D68491D6F4")
    assertEquals(MicrosoftSpeechProtocol.Voice, "fa-IR-DilaraNeural")
    assertEquals(MicrosoftSpeechProtocol.OutputFormat, "audio-24khz-48kbitrate-mono-mp3")
    assertEquals(
      MicrosoftSpeechProtocol.secMsGec(epochMillis),
      "BD721EDF522D70BE4575BAABDC730E8B6AE84F3A5FB57B5B31FE70FC89384262"
    )
    assertEquals(MicrosoftSpeechProtocol.timestamp(epochMillis), "2024-01-02T03:04:05.678Z")
    assertEquals(
      MicrosoftSpeechProtocol.muid(Array.tabulate[Byte](16)(_.toByte)),
      "000102030405060708090A0B0C0D0E0F"
    )

    val config = MicrosoftSpeechProtocol.speechConfig("2024-01-02T03:04:05.678Z")
    assertEquals(
      config,
      "X-Timestamp:2024-01-02T03:04:05.678Z\r\n" +
        "Content-Type:application/json; charset=utf-8\r\n" +
        "Path:speech.config\r\n\r\n" +
        "{\"context\":{\"synthesis\":{\"audio\":{\"metadataoptions\":" +
        "{\"sentenceBoundaryEnabled\":false,\"wordBoundaryEnabled\":true}," +
        "\"outputFormat\":\"audio-24khz-48kbitrate-mono-mp3\"}}}}"
    )

    val ssml = MicrosoftSpeechProtocol.ssml(
      "<&>\"'",
      "abc123",
      "2024-01-02T03:04:05.678Z"
    )
    assertEquals(
      ssml,
      "X-RequestId:abc123\r\n" +
        "Content-Type:application/ssml+xml\r\n" +
        "X-Timestamp:2024-01-02T03:04:05.678ZZ\r\n" +
        "Path:ssml\r\n\r\n" +
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='fa'>" +
        "<voice name='fa-IR-DilaraNeural'>" +
        "<prosody pitch='+0Hz' rate='+0%' volume='+0%'>&lt;&amp;&gt;&quot;&apos;" +
        "</prosody></voice></speak>"
    )

    val audio = Array[Byte](1, 2, 3)
    val parsed = MicrosoftSpeechProtocol.parseBinaryFrame(binaryFrame("Path:audio\r\n", audio))
    assertEquals(parsed.map(_.header), Right("Path:audio\r\n"))
    assertEquals(parsed.map(_.payload.toSeq), Right(audio.toSeq))
    assert(MicrosoftSpeechProtocol.parseBinaryFrame(Array[Byte](0, 5, 1)).isLeft)

  test("Microsoft listener sends commands in order and requests inbound demand on open"):
    val request = listenerRequest(messages = Vector("config-command", "ssml-command"))
    val listener = new MicrosoftSpeechListener(request)
    val socket = new FakeWebSocket(pauseFirstText = true)

    listener.onOpen(socket)

    assertEquals(socket.sentTexts, Vector("config-command" -> true))
    assertEquals(socket.requested, 1L)
    assertEquals(socket.requestCalls, 1)
    assert(!listener.result.isDone)

    socket.completeFirstText()

    assertEquals(
      socket.sentTexts,
      Vector("config-command" -> true, "ssml-command" -> true)
    )
    assertEquals(socket.requested, 1L)
    listener.abort()

  test("Microsoft listener combines fragmented audio frames and completes on fragmented text turn.end"):
    val listener = new MicrosoftSpeechListener(listenerRequest())
    val socket = new FakeWebSocket()
    listener.onOpen(socket)

    val first = binaryFrame("X-RequestId:one\r\nPath:audio\r\n", Array[Byte](1, 2))
    val second = binaryFrame("X-RequestId:one\r\nPath:audio\r\n", Array[Byte](3, 4, 5))

    listener.onBinary(socket, ByteBuffer.wrap(first.take(3)), last = false)
    assert(!listener.result.isDone)
    listener.onBinary(socket, ByteBuffer.wrap(first.drop(3)), last = true)
    listener.onBinary(socket, ByteBuffer.wrap(second.dropRight(1)), last = false)
    listener.onBinary(socket, ByteBuffer.wrap(second.takeRight(1)), last = true)
    assert(!listener.result.isDone)

    listener.onText(socket, "Path:turn.", last = false)
    assert(!listener.result.isDone)
    listener.onText(socket, "end\r\n", last = true)

    assertEquals(listener.result.get(1, TimeUnit.SECONDS).toSeq, Seq[Byte](1, 2, 3, 4, 5))
    assertEquals(socket.closeRequests, Vector(WebSocket.NORMAL_CLOSURE -> ""))
    assertEquals(socket.abortCalls, 0)
    assertEquals(socket.requested, 6L)
    assertEquals(socket.requestCalls, 6)

  test("Microsoft listener completes oversize, WebSocket error, send error, and early close failures"):
    val oversizeListener = new MicrosoftSpeechListener(listenerRequest(maxAudioBytes = 3))
    val oversizeSocket = new FakeWebSocket()
    oversizeListener.onOpen(oversizeSocket)
    oversizeListener.onBinary(
      oversizeSocket,
      ByteBuffer.wrap(binaryFrame("Path:audio\r\n", Array[Byte](1, 2))),
      last = true
    )
    assert(!oversizeListener.result.isDone)
    oversizeListener.onBinary(
      oversizeSocket,
      ByteBuffer.wrap(binaryFrame("Path:audio\r\n", Array[Byte](3, 4))),
      last = true
    )
    val oversize = completedFailure(oversizeListener.result)
    assert(oversize.getMessage.contains("10 MiB"), oversize.getMessage)
    assertEquals(oversizeSocket.abortCalls, 1)

    val socketErrorListener = new MicrosoftSpeechListener(listenerRequest())
    val socketErrorSocket = new FakeWebSocket()
    val socketError = new IOException("socket failed")
    socketErrorListener.onOpen(socketErrorSocket)
    socketErrorListener.onError(socketErrorSocket, socketError)
    assert(completedFailure(socketErrorListener.result) eq socketError)
    assertEquals(socketErrorSocket.abortCalls, 1)

    val sendErrorListener = new MicrosoftSpeechListener(listenerRequest())
    val sendErrorSocket = new FakeWebSocket(pauseFirstText = true)
    sendErrorListener.onOpen(sendErrorSocket)
    sendErrorSocket.failFirstText(new IOException("send failed"))
    assertEquals(completedFailure(sendErrorListener.result).getMessage, "send failed")
    assertEquals(sendErrorSocket.sentTexts, Vector("speech.config" -> true))
    assertEquals(sendErrorSocket.abortCalls, 1)

    val closedListener = new MicrosoftSpeechListener(listenerRequest())
    val closedSocket = new FakeWebSocket()
    closedListener.onOpen(closedSocket)
    closedListener.onBinary(
      closedSocket,
      ByteBuffer.wrap(binaryFrame("Path:audio\r\n", Array[Byte](9))),
      last = true
    )
    closedListener.onClose(closedSocket, 1001, "early")
    val closed = completedFailure(closedListener.result)
    assert(closed.getMessage.contains("WebSocket closed: 1001 early"), closed.getMessage)
    assertEquals(closedSocket.abortCalls, 0)

  test("Microsoft listener abort completes cancellation and aborts current or later sockets"):
    val openListener = new MicrosoftSpeechListener(listenerRequest())
    val openSocket = new FakeWebSocket()
    openListener.onOpen(openSocket)
    openListener.abort()

    assert(completedFailure(openListener.result).isInstanceOf[CancellationException])
    assertEquals(openSocket.abortCalls, 1)
    val demandAtCancellation = openSocket.requested
    openListener.onText(openSocket, "Path:turn.end\r\n", last = true)
    openListener.abort()
    assertEquals(openSocket.abortCalls, 1)
    assertEquals(openSocket.requested, demandAtCancellation)
    assertEquals(openSocket.closeRequests, Vector.empty)

    val beforeOpenListener = new MicrosoftSpeechListener(listenerRequest())
    beforeOpenListener.abort()
    assert(completedFailure(beforeOpenListener.result).isInstanceOf[CancellationException])
    val laterSocket = new FakeWebSocket()
    beforeOpenListener.onOpen(laterSocket)
    assertEquals(laterSocket.abortCalls, 1)
    assertEquals(laterSocket.sentTexts, Vector.empty)
    assertEquals(laterSocket.requested, 0L)

  test("fa uses the injectable Microsoft transport with deterministic IDs, cookie, and messages"):
    val audio = Array[Byte](4, 5, 6)
    val transport = new RecordingMicrosoftTransport(IO.pure(audio))
    val translationIndex = new AtomicInteger(0)
    val idIndex = new AtomicInteger(0)
    val ids = Vector(
      "11111111-1111-1111-1111-111111111111",
      "22222222-2222-2222-2222-222222222222"
    )

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("fa")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker greeting $CloseMarker"), Some("fa")))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val result = microsoftClient(
        server,
        transport,
        randomId = () => ids(idIndex.getAndIncrement())
      ).prepare("salaam <&>", SelectionSpan(0, 6), "fa").unsafeRunSync()

      assertEquals(result.sourceLanguage, "fa")
      assertEquals(result.audioBytes.toSeq, audio.toSeq)
      assertEquals(server.requestsFor("/tts"), Nil)
      assertEquals(transport.requests.size, 1)

      val request = transport.requests.head
      assertEquals(
        request.uri.toASCIIString,
        "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1" +
          "?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4" +
          "&Sec-MS-GEC=BD721EDF522D70BE4575BAABDC730E8B6AE84F3A5FB57B5B31FE70FC89384262" +
          "&Sec-MS-GEC-Version=1-143.0.3650.75" +
          "&ConnectionId=11111111111111111111111111111111"
      )
      assertEquals(
        request.headers,
        Vector(
          "Pragma" -> "no-cache",
          "Cache-Control" -> "no-cache",
          "Origin" -> "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
          "User-Agent" -> (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
              "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
          ),
          "Accept-Encoding" -> "gzip, deflate, br, zstd",
          "Accept-Language" -> "en-US,en;q=0.9",
          "Cookie" -> "muid=000102030405060708090A0B0C0D0E0F;"
        )
      )
      assertEquals(request.maxAudioBytes, GoogleClient.MaxAudioBytes)
      assertEquals(request.requestTimeout, Duration.ofSeconds(2))
      assert(request.messages.head.contains("Path:speech.config"))
      assert(request.messages.head.contains("audio-24khz-48kbitrate-mono-mp3"))
      assert(request.messages(1).contains("X-RequestId:22222222222222222222222222222222"))
      assert(request.messages(1).contains("<voice name='fa-IR-DilaraNeural'>"))
      assert(request.messages(1).contains("salaam &lt;&amp;&gt;"))

  test("fa transport timeout completes instead of hanging"):
    val transport = new RecordingMicrosoftTransport(IO.never[Array[Byte]])
    val translationIndex = new AtomicInteger(0)

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("fa")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker x $CloseMarker"), Some("fa")))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val error = intercept[IllegalStateException]:
        microsoftClient(server, transport, timeout = Duration.ofMillis(50))
          .prepare("x", SelectionSpan(0, 1), "fa")
          .unsafeRunSync()
      assert(error.getMessage.contains("Microsoft speech request timed out"), error.getMessage)
      assertEquals(transport.requests.size, 1)

  private def completedFailure(future: CompletableFuture[?]): Throwable =
    try
      future.get(1, TimeUnit.SECONDS)
      fail("expected future to complete exceptionally")
    catch
      case error: ExecutionException  => error.getCause
      case error: CancellationException => error

  private def withServer[A](respond: RecordedRequest => StubResponse)(test: StubServer => A): A =
    val server = new StubServer(respond)
    try test(server)
    finally server.close()
