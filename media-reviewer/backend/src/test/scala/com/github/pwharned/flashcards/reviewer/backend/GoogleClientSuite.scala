package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.effect.unsafe.implicits.global
import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}
import com.sun.net.httpserver.{HttpExchange, HttpServer}

import java.io.IOException
import java.net.{InetSocketAddress, URI}
import java.net.http.{
  HttpClient,
  HttpHeaders,
  HttpRequest,
  HttpResponse,
  WebSocket,
  WebSocketHandshakeException
}
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.Optional
import java.util.concurrent.atomic.{AtomicInteger, AtomicLong}
import java.util.concurrent.{
  CancellationException,
  CompletableFuture,
  CompletionException,
  CopyOnWriteArrayList,
  CountDownLatch,
  ExecutionException,
  Executors,
  TimeUnit
}
import javax.net.ssl.SSLSession
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

  final class RecordingMicrosoftTransport(respond: MicrosoftSpeechRequest => IO[Array[Byte]])
      extends MicrosoftSpeechTransport:
    def this(result: IO[Array[Byte]]) = this(_ => result)

    private val recorded = new CopyOnWriteArrayList[MicrosoftSpeechRequest]()

    def requests: List[MicrosoftSpeechRequest] = recorded.asScala.toList

    override def synthesize(request: MicrosoftSpeechRequest): IO[Array[Byte]] =
      IO.delay:
        recorded.add(request)
        ()
      .flatMap(_ => respond(request))

  final class FakeHandshakeResponse(status: Int, date: Option[String])
      extends HttpResponse[Unit]:
    private val responseHeaders =
      val values = date.fold(Map.empty[String, List[String]])(value => Map("Date" -> List(value)))
      HttpHeaders.of(
        values.view.mapValues(_.asJava).toMap.asJava,
        (_, _) => true
      )

    override def statusCode(): Int = status
    override def request(): HttpRequest = null
    override def previousResponse(): Optional[HttpResponse[Unit]] = Optional.empty()
    override def headers(): HttpHeaders = responseHeaders
    override def body(): Unit = ()
    override def sslSession(): Optional[SSLSession] = Optional.empty()
    override def uri(): URI = URI.create("https://speech.platform.bing.com/")
    override def version(): HttpClient.Version = HttpClient.Version.HTTP_1_1

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

  def audioFrame(payload: Array[Byte]): Array[Byte] =
    binaryFrame(
      "X-RequestId:test\r\nContent-Type:audio/mpeg\r\nPath:audio\r\n",
      payload
    )

  def terminalAudioFrame: Array[Byte] = binaryFrame("Path:audio\r\n", Array.emptyByteArray)

  def textFrame(path: String, data: String = ""): String =
    s"X-RequestId:test\r\nPath:$path\r\n\r\n$data"

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

  test("Microsoft text preparation sanitizes, escapes, and splits like Clausula"):
    val input =
      "a\u0000b\u0009c\u000ad\u000be\u000cf\u000dg\u001fh\ud800i\ufffej & <tag> >"
    val sanitized = "a b\tc\nd e f\rg h i j & <tag> >"
    assertEquals(MicrosoftSpeechProtocol.sanitizeXmlText(input), sanitized)
    assertEquals(
      MicrosoftSpeechProtocol.escapeXml(sanitized),
      "a b\tc\nd e f\rg h i j &amp; &lt;tag&gt; &gt;"
    )

    assertEquals(
      MicrosoftSpeechProtocol.splitEscapedText("alpha\nbravo charlie delta", 20),
      Right(Vector("alpha", "bravo charlie delta"))
    )
    assertEquals(
      MicrosoftSpeechProtocol.splitEscapedText("alpha bravo charlie delta", 20),
      Right(Vector("alpha bravo charlie", "delta"))
    )

    val escaped = MicrosoftSpeechProtocol.escapeXml(
      "\u0633\u0644\u0627\u0645\ud83d\ude00&<\u062f\u0648\u0633\u062a>" +
        "\u0633\u0644\u0627\u0645\ud83d\ude00&"
    )
    val chunks = MicrosoftSpeechProtocol.splitEscapedText(escaped, 16)
    assertEquals(
      chunks,
      Right(
        Vector(
          "\u0633\u0644\u0627\u0645\ud83d\ude00",
          "&amp;&lt;\u062f\u0648\u0633",
          "\u062a&gt;\u0633\u0644\u0627\u0645",
          "\ud83d\ude00&amp;"
        )
      )
    )
    chunks.toOption.get.foreach: chunk =>
      assert(chunk.getBytes(StandardCharsets.UTF_8).length <= 16)

    val noWhitespace = MicrosoftSpeechProtocol.splitEscapedText("a" * 5000)
    assertEquals(
      noWhitespace.map(_.map(_.getBytes(StandardCharsets.UTF_8).length)),
      Right(Vector(4096, 904))
    )
    assertEquals(noWhitespace.map(_.mkString), Right("a" * 5000))
    assert(MicrosoftSpeechProtocol.splitEscapedText("&amp;", 4).isLeft)

    val defaultChunks = MicrosoftSpeechProtocol.prepareText(
      "\u0633\u0644\u0627\u0645 \ud83d\ude00 & " * 1000
    )
    assertEquals(
      defaultChunks.map(_.map(_.getBytes(StandardCharsets.UTF_8).length)),
      Right(Vector(4093, 4094, 4090, 4093, 3625))
    )
    assert(defaultChunks.toOption.get.forall(_.getBytes(StandardCharsets.UTF_8).length <= 4096))

  test("Microsoft Edge protocol vectors and frames exactly match Clausula"):
    assertEquals(MicrosoftSpeechProtocol.SecMsGecVersion, "1-143.0.3650.75")
    assertEquals(MicrosoftSpeechProtocol.TrustedClientToken, "6A5AA1D4EAFF4E9FB37E23D68491D6F4")
    assertEquals(MicrosoftSpeechProtocol.Voice, "fa-IR-DilaraNeural")
    assertEquals(MicrosoftSpeechProtocol.OutputFormat, "audio-24khz-48kbitrate-mono-mp3")
    assertEquals(MicrosoftSpeechProtocol.MaxTextChunkBytes, 4096)

    val gecVectors = Vector(
      (0L, "116444736000000000", "7ECB79D14E3AA576D2D79E6D487A1388156D91E614B1BE11C64226A29BC8DD8C"),
      (299999L, "116444736000000000", "7ECB79D14E3AA576D2D79E6D487A1388156D91E614B1BE11C64226A29BC8DD8C"),
      (300000L, "116444739000000000", "ED93F5AAE06C01D88654A1831CAC424F7BE4878E9D5850F7F66DDCBDD7ED95B9"),
      (1735689599999L, "133801629000000000", "4EDD3A5D81F2B34A223CE94402D9B089EE5A3A7658BEB984F5FF288C8F294F61"),
      (1735689600000L, "133801632000000000", "B0EDD22C7C09868E2F24C10264A8A3EB877773A7B6040B68AFA4FBCBABEA0238")
    )
    gecVectors.foreach: (now, ticks, hash) =>
      assertEquals(MicrosoftSpeechProtocol.filetimeTicks(now).toString, ticks)
      assertEquals(MicrosoftSpeechProtocol.secMsGec(now), hash)
    assertEquals(
      MicrosoftSpeechProtocol.filetimeTicks(299999L, 1L),
      MicrosoftSpeechProtocol.filetimeTicks(300000L)
    )
    assertEquals(
      MicrosoftSpeechProtocol.filetimeTicks(300001L, -2L),
      MicrosoftSpeechProtocol.filetimeTicks(0L)
    )
    assertEquals(
      MicrosoftSpeechProtocol.muid(Array.tabulate[Byte](16)(_.toByte)),
      "000102030405060708090A0B0C0D0E0F"
    )

    val epochMillis = 1735787045000L
    val timestamp = "Thu Jan 02 2025 03:04:05 GMT+0000 (Coordinated Universal Time)"
    assertEquals(MicrosoftSpeechProtocol.timestamp(epochMillis), timestamp)
    val config = MicrosoftSpeechProtocol.speechConfig(timestamp)
    val expectedConfig =
      s"X-Timestamp:$timestamp\r\n" +
        "Content-Type:application/json; charset=utf-8\r\n" +
        "Path:speech.config\r\n\r\n" +
        "{\"context\":{\"synthesis\":{\"audio\":{\"metadataoptions\":" +
        "{\"sentenceBoundaryEnabled\":\"false\",\"wordBoundaryEnabled\":\"true\"}," +
        "\"outputFormat\":\"audio-24khz-48kbitrate-mono-mp3\"}}}}\r\n"
    assertEquals(
      config.getBytes(StandardCharsets.UTF_8).toSeq,
      expectedConfig.getBytes(StandardCharsets.UTF_8).toSeq
    )

    val ssml = MicrosoftSpeechProtocol.ssml(
      "\u0633\u0644\u0627\u0645 &amp; &lt;x&gt;",
      "0123456789abcdef0123456789abcdef",
      timestamp
    )
    val expectedSsml =
      "X-RequestId:0123456789abcdef0123456789abcdef\r\n" +
        "Content-Type:application/ssml+xml\r\n" +
        s"X-Timestamp:${timestamp}Z\r\n" +
        "Path:ssml\r\n\r\n" +
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>" +
        "<voice name='fa-IR-DilaraNeural'>" +
        "<prosody pitch='+0Hz' rate='+0%' volume='+0%'>" +
        "\u0633\u0644\u0627\u0645 &amp; &lt;x&gt;</prosody></voice></speak>"
    assertEquals(
      ssml.getBytes(StandardCharsets.UTF_8).toSeq,
      expectedSsml.getBytes(StandardCharsets.UTF_8).toSeq
    )

    val audio = Array[Byte](1, 2, 3)
    val parsed = MicrosoftSpeechProtocol.parseBinaryFrame(audioFrame(audio))
    assertEquals(
      parsed.map(_.header),
      Right("X-RequestId:test\r\nContent-Type:audio/mpeg\r\nPath:audio\r\n")
    )
    assertEquals(parsed.map(_.payload.toSeq), Right(audio.toSeq))
    assertEquals(parsed.map(_.terminal), Right(false))
    assertEquals(MicrosoftSpeechProtocol.parseBinaryFrame(terminalAudioFrame).map(_.terminal), Right(true))
    assert(MicrosoftSpeechProtocol.parseBinaryFrame(Array[Byte](0, 5, 1)).isLeft)
    assert(MicrosoftSpeechProtocol.parseBinaryFrame(binaryFrame("Path:nope\r\n", audio)).isLeft)
    assert(
      MicrosoftSpeechProtocol.parseBinaryFrame(binaryFrame("Path:audio\r\n", audio)).isLeft
    )
    assert(MicrosoftSpeechProtocol.parseTextFrame("Path:turn.end\n\n").isLeft)
    assertEquals(
      MicrosoftSpeechProtocol.parseTextFrame(textFrame("turn.end")).map(_.path),
      Right("turn.end")
    )

    assertEquals(
      MicrosoftSpeechProtocol.uri(
        GoogleClient.LiveMicrosoftSpeechEndpoint,
        "A" * 64,
        "0123456789abcdef0123456789abcdef"
      ).toASCIIString,
      "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1" +
        "?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4" +
        "&ConnectionId=0123456789abcdef0123456789abcdef" +
        s"&Sec-MS-GEC=${"A" * 64}&Sec-MS-GEC-Version=1-143.0.3650.75"
    )

  test("JDK transport exposes handshake status and Date through completion wrappers"):
    val responseDate = "Thu, 01 Jan 1970 00:05:00 GMT"
    val handshake = new WebSocketHandshakeException(
      new FakeHandshakeResponse(403, Some(responseDate))
    )
    val normalized = JdkMicrosoftSpeechTransport
      .normalizeError(new CompletionException(handshake))
      .asInstanceOf[MicrosoftHandshakeError]

    assertEquals(normalized.status, 403)
    assertEquals(normalized.statusCode, 403)
    assertEquals(normalized.date, Some(responseDate))
    assertEquals(normalized.responseDate, Some(responseDate))
    assert(normalized.getCause eq handshake)

    val ordinary = new IOException("ordinary")
    assert(JdkMicrosoftSpeechTransport.normalizeError(new CompletionException(ordinary)) eq ordinary)

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

    val first = audioFrame(Array[Byte](1, 2))
    val second = audioFrame(Array[Byte](3, 4, 5))

    listener.onBinary(socket, ByteBuffer.wrap(first.take(3)), last = false)
    assert(!listener.result.isDone)
    listener.onBinary(socket, ByteBuffer.wrap(first.drop(3)), last = true)
    listener.onBinary(socket, ByteBuffer.wrap(second.dropRight(1)), last = false)
    listener.onBinary(socket, ByteBuffer.wrap(second.takeRight(1)), last = true)
    listener.onBinary(socket, ByteBuffer.wrap(terminalAudioFrame), last = true)
    assert(!listener.result.isDone)

    val turnEnd = textFrame("turn.end")
    listener.onText(socket, turnEnd.dropRight(5), last = false)
    assert(!listener.result.isDone)
    listener.onText(socket, turnEnd.takeRight(5), last = true)

    assertEquals(listener.result.get(1, TimeUnit.SECONDS).toSeq, Seq[Byte](1, 2, 3, 4, 5))
    assertEquals(socket.closeRequests, Vector(WebSocket.NORMAL_CLOSURE -> ""))
    assertEquals(socket.abortCalls, 0)
    assertEquals(socket.requested, 7L)
    assertEquals(socket.requestCalls, 7)

  test("Microsoft listener completes oversize, WebSocket error, send error, and early close failures"):
    val noAudioListener = new MicrosoftSpeechListener(listenerRequest())
    val noAudioSocket = new FakeWebSocket()
    noAudioListener.onOpen(noAudioSocket)
    noAudioListener.onText(noAudioSocket, textFrame("turn.end"), last = true)
    val noAudio = completedFailure(noAudioListener.result)
    assert(noAudio.getMessage.contains("empty audio"), noAudio.getMessage)
    assert(!noAudioListener.hasReceivedAudio)
    assertEquals(noAudioSocket.abortCalls, 1)

    val oversizeListener = new MicrosoftSpeechListener(listenerRequest(maxAudioBytes = 3))
    val oversizeSocket = new FakeWebSocket()
    oversizeListener.onOpen(oversizeSocket)
    oversizeListener.onBinary(
      oversizeSocket,
      ByteBuffer.wrap(audioFrame(Array[Byte](1, 2))),
      last = true
    )
    assert(!oversizeListener.result.isDone)
    oversizeListener.onBinary(
      oversizeSocket,
      ByteBuffer.wrap(audioFrame(Array[Byte](3, 4))),
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
      ByteBuffer.wrap(audioFrame(Array[Byte](9))),
      last = true
    )
    assert(closedListener.hasReceivedAudio)
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
    openListener.onText(openSocket, textFrame("turn.end"), last = true)
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

  test("Persian chunks use fresh sequential turns and concatenate their audio"):
    val turnIndex = new AtomicInteger(0)
    val transport = new RecordingMicrosoftTransport(_ =>
      turnIndex.getAndIncrement() match
        case 0 => IO.pure(Array[Byte](1, 2))
        case 1 => IO.pure(Array[Byte](3, 4, 5))
        case other => IO.raiseError(new IllegalStateException(s"unexpected turn $other"))
    )
    val translationIndex = new AtomicInteger(0)
    val idIndex = new AtomicInteger(0)
    val ids = Vector("1" * 32, "2" * 32, "3" * 32, "4" * 32)
    val muidIndex = new AtomicInteger(0)
    val sentence = "a" * 5000

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("fa")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker a $CloseMarker"), Some("fa")))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val result = microsoftClient(
        server,
        transport,
        randomId = () => ids(idIndex.getAndIncrement()),
        muidBytes = () =>
          val value = muidIndex.getAndIncrement().toByte
          Array.fill[Byte](16)(value)
      ).prepare(sentence, SelectionSpan(0, 1), "fa").unsafeRunSync()

      assertEquals(result.audioBytes.toSeq, Seq[Byte](1, 2, 3, 4, 5))
      assertEquals(transport.requests.size, 2)
      val first = transport.requests(0)
      val second = transport.requests(1)
      assert(first.uri.toASCIIString.contains(s"ConnectionId=${"1" * 32}"))
      assert(second.uri.toASCIIString.contains(s"ConnectionId=${"3" * 32}"))
      assert(first.messages(1).contains(s"X-RequestId:${"2" * 32}"))
      assert(second.messages(1).contains(s"X-RequestId:${"4" * 32}"))
      assert(first.messages(1).contains(s">${"a" * 4096}</prosody>"))
      assert(second.messages(1).contains(s">${"a" * 904}</prosody>"))
      assertEquals(first.headers.last, "Cookie" -> s"muid=${"00" * 16};")
      assertEquals(second.headers.last, "Cookie" -> s"muid=${"01" * 16};")
      assertEquals(first.maxAudioBytes, GoogleClient.MaxAudioBytes)
      assertEquals(second.maxAudioBytes, GoogleClient.MaxAudioBytes - 2)

  test("Persian multi-turn synthesis obeys the overall deadline and cancels the active turn"):
    val turnIndex = new AtomicInteger(0)
    val cancellations = new AtomicInteger(0)
    val transport = new RecordingMicrosoftTransport(_ =>
      if turnIndex.getAndIncrement() == 0 then IO.pure(Array[Byte](1))
      else
        IO.never[Array[Byte]].onCancel(
          IO.delay:
            cancellations.incrementAndGet()
            ()
        )
    )
    val translationIndex = new AtomicInteger(0)

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("fa")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker a $CloseMarker"), Some("fa")))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val error = intercept[IllegalStateException]:
        microsoftClient(
          server,
          transport,
          timeout = Duration.ofSeconds(5),
          overallTimeout = Duration.ofSeconds(1)
        ).prepare("a" * 5000, SelectionSpan(0, 1), "fa").unsafeRunSync()

      assert(error.getMessage.contains("preparation timed out"), error.getMessage)
      assertEquals(transport.requests.size, 2)
      assertEquals(cancellations.get(), 1)

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
          "&ConnectionId=11111111111111111111111111111111" +
          "&Sec-MS-GEC=BD721EDF522D70BE4575BAABDC730E8B6AE84F3A5FB57B5B31FE70FC89384262" +
          "&Sec-MS-GEC-Version=1-143.0.3650.75"
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
      assert(request.messages(1).contains("xml:lang='en-US'"))
      assert(request.messages(1).contains("<voice name='fa-IR-DilaraNeural'>"))
      assert(request.messages(1).contains("salaam &lt;&amp;&gt;"))

  test("a first pre-audio 403 uses server Date skew and retries the chunk with fresh identity"):
    val responseDate = "Thu, 01 Jan 1970 00:05:00 GMT"
    val (result, requests) = runFaResponses(
      Vector(
        Left(new MicrosoftHandshakeError(403, Some(responseDate))),
        Right(Array[Byte](7, 8))
      ),
      nowMillis = 0L
    )

    assertEquals(result.toOption.get.audioBytes.toSeq, Seq[Byte](7, 8))
    assertEquals(requests.size, 2)
    assert(
      requests(0).uri.toASCIIString.contains(
        "Sec-MS-GEC=7ECB79D14E3AA576D2D79E6D487A1388156D91E614B1BE11C64226A29BC8DD8C"
      )
    )
    assert(
      requests(1).uri.toASCIIString.contains(
        "Sec-MS-GEC=ED93F5AAE06C01D88654A1831CAC424F7BE4878E9D5850F7F66DDCBDD7ED95B9"
      )
    )
    assert(requests(0).uri != requests(1).uri)
    assert(requests(0).headers.last != requests(1).headers.last)
    assert(requests(0).messages(1) != requests(1).messages(1))

  test("403 retry rejects missing or malformed Date, repeat 403, non-403, and post-audio errors"):
    val validDate = "Thu, 01 Jan 1970 00:05:00 GMT"

    val (missingResult, missingRequests) = runFaResponses(
      Vector(Left(new MicrosoftHandshakeError(403, None))),
      nowMillis = 0L
    )
    assert(missingResult.left.toOption.get.getMessage.contains("did not include a Date"))
    assertEquals(missingRequests.size, 1)

    val (malformedResult, malformedRequests) = runFaResponses(
      Vector(Left(new MicrosoftHandshakeError(403, Some("not-a-date")))),
      nowMillis = 0L
    )
    assert(malformedResult.left.toOption.get.getMessage.contains("invalid Date"))
    assertEquals(malformedRequests.size, 1)

    val second403 = new MicrosoftHandshakeError(403, Some(validDate))
    val (secondResult, secondRequests) = runFaResponses(
      Vector(
        Left(new MicrosoftHandshakeError(403, Some(validDate))),
        Left(second403)
      ),
      nowMillis = 0L
    )
    assert(secondResult.left.toOption.get eq second403)
    assertEquals(secondRequests.size, 2)

    val non403 = new MicrosoftHandshakeError(429, Some(validDate))
    val (non403Result, non403Requests) = runFaResponses(Vector(Left(non403)), nowMillis = 0L)
    assert(non403Result.left.toOption.get eq non403)
    assertEquals(non403Requests.size, 1)

    val afterAudio = new MicrosoftHandshakeError(
      403,
      Some(validDate),
      audioReceived = true
    )
    val (afterAudioResult, afterAudioRequests) =
      runFaResponses(Vector(Left(afterAudio)), nowMillis = 0L)
    assert(afterAudioResult.left.toOption.get eq afterAudio)
    assertEquals(afterAudioRequests.size, 1)

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

  private def runFaResponses(
      responses: Vector[Either[Throwable, Array[Byte]]],
      nowMillis: Long
  ): (Either[Throwable, GooglePreparation], List[MicrosoftSpeechRequest]) =
    val responseIndex = new AtomicInteger(0)
    val transport = new RecordingMicrosoftTransport(_ =>
      responses.lift(responseIndex.getAndIncrement()) match
        case Some(response) => IO.fromEither(response)
        case None => IO.raiseError(new IllegalStateException("unexpected Microsoft speech request"))
    )
    val translationIndex = new AtomicInteger(0)
    val idIndex = new AtomicInteger(1)
    val muidIndex = new AtomicInteger(0)

    withServer(
      request =>
        request.path match
          case "/translate" if translationIndex.getAndIncrement() == 0 =>
            StubResponse.batch(batchexecute(List("sentence"), Some("fa")))
          case "/translate" =>
            StubResponse.batch(batchexecute(List(s"$OpenMarker x $CloseMarker"), Some("fa")))
          case _ => StubResponse.text("unexpected", 500)
    ): server =>
      val result = microsoftClient(
        server,
        transport,
        now = () => nowMillis,
        randomId = () => f"${idIndex.getAndIncrement()}%032x",
        muidBytes = () =>
          val value = muidIndex.getAndIncrement().toByte
          Array.fill[Byte](16)(value)
      ).prepare("x", SelectionSpan(0, 1), "fa").attempt.unsafeRunSync()
      result -> transport.requests

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
