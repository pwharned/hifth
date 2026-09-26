package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.syntax.all.*
import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}

import java.io.{ByteArrayOutputStream, IOException}
import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse, HttpTimeoutException, WebSocket}
import java.nio.{ByteBuffer, CharBuffer}
import java.nio.charset.{CodingErrorAction, StandardCharsets}
import java.security.{MessageDigest, SecureRandom}
import java.time.{Duration, Instant, ZoneOffset}
import java.time.format.DateTimeFormatter
import java.util.{HexFormat, Locale, UUID}
import java.util.concurrent.{CancellationException, CompletableFuture, CompletionStage, Flow}
import scala.concurrent.duration.DurationLong

private[backend] trait GoogleGateway:
  def prepare(
      sentence: String,
      selection: SelectionSpan,
      artifactLanguage: String
  ): IO[GooglePreparation]

private[backend] final class GooglePreparation private (
    val enrichment: Enrichment,
    val sourceLanguage: String,
    val filename: String,
    private val storedAudioBytes: Array[Byte],
    val sha256: String
):
  val contentType: String = "audio/mpeg"
  def audioBytes: Array[Byte] = storedAudioBytes.clone()

private[backend] object GooglePreparation:
  def apply(
      enrichment: Enrichment,
      sourceLanguage: String,
      filename: String,
      audioBytes: Array[Byte],
      sha256: String
  ): GooglePreparation =
    new GooglePreparation(enrichment, sourceLanguage, filename, audioBytes.clone(), sha256)

private[backend] final case class MicrosoftSpeechRequest(
    uri: URI,
    headers: Vector[(String, String)],
    messages: Vector[String],
    requestTimeout: Duration,
    maxAudioBytes: Int
)

private[backend] trait MicrosoftSpeechTransport:
  def synthesize(request: MicrosoftSpeechRequest): IO[Array[Byte]]

private[backend] object GoogleClient:
  private[backend] val LiveTranslationEndpoint: URI =
    URI.create("https://translate.google.com/_/TranslateWebserverUi/data/batchexecute")
  private[backend] val LiveSpeechEndpoint: URI =
    URI.create("https://translate.googleapis.com/translate_tts")
  private[backend] val LiveMicrosoftSpeechEndpoint: URI =
    URI.create("wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1")
  private[backend] val DefaultRequestTimeout: Duration = Duration.ofSeconds(30)
  private[backend] val DefaultOverallTimeout: Duration = Duration.ofMinutes(2)
  private[backend] val MaxSentenceUtf16: Int = 5000
  private[backend] val MaxAudioBytes: Int = 10 * 1024 * 1024

  private[backend] val UserAgent: String =
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36"
  private[backend] val Referer: String = "https://translate.google.com/"
  private[backend] val TranslationContentType: String =
    "application/x-www-form-urlencoded;charset=utf-8"

  private val ConnectTimeout = Duration.ofSeconds(10)
  private val SecureRandomSource = new SecureRandom()
  private val CurrentTimeMillis: () => Long = () => System.currentTimeMillis()
  private val RandomId: () => String = () => UUID.randomUUID().toString.replace("-", "")
  private val RandomMuidBytes: () => Array[Byte] = () =>
    val bytes = new Array[Byte](16)
    SecureRandomSource.nextBytes(bytes)
    bytes

  def live: GoogleClient =
    val httpClient = HttpClient
      .newBuilder()
      .connectTimeout(ConnectTimeout)
      .followRedirects(HttpClient.Redirect.NEVER)
      .build()
    new GoogleClient(
      httpClient,
      LiveTranslationEndpoint,
      LiveSpeechEndpoint,
      DefaultRequestTimeout,
      DefaultOverallTimeout,
      new JdkMicrosoftSpeechTransport(httpClient),
      CurrentTimeMillis,
      RandomId,
      RandomMuidBytes
    )

  private[backend] def withEndpoints(
      translationEndpoint: URI,
      speechEndpoint: URI,
      httpClient: HttpClient,
      requestTimeout: Duration = DefaultRequestTimeout,
      overallTimeout: Duration = DefaultOverallTimeout
  ): GoogleClient =
    new GoogleClient(
      httpClient,
      translationEndpoint,
      speechEndpoint,
      requestTimeout,
      overallTimeout,
      new JdkMicrosoftSpeechTransport(httpClient),
      CurrentTimeMillis,
      RandomId,
      RandomMuidBytes
    )

  private[backend] def withMicrosoftTransport(
      translationEndpoint: URI,
      speechEndpoint: URI,
      httpClient: HttpClient,
      microsoftTransport: MicrosoftSpeechTransport,
      requestTimeout: Duration = DefaultRequestTimeout,
      overallTimeout: Duration = DefaultOverallTimeout,
      currentTimeMillis: () => Long = CurrentTimeMillis,
      randomId: () => String = RandomId,
      randomMuidBytes: () => Array[Byte] = RandomMuidBytes
  ): GoogleClient =
    new GoogleClient(
      httpClient,
      translationEndpoint,
      speechEndpoint,
      requestTimeout,
      overallTimeout,
      microsoftTransport,
      currentTimeMillis,
      randomId,
      randomMuidBytes
    )

private[backend] final class GoogleClient private (
    httpClient: HttpClient,
    translationEndpoint: URI,
    speechEndpoint: URI,
    requestTimeout: Duration,
    overallTimeout: Duration,
    microsoftTransport: MicrosoftSpeechTransport,
    currentTimeMillis: () => Long,
    randomId: () => String,
    randomMuidBytes: () => Array[Byte]
) extends GoogleGateway:
  import GoogleClient.*

  private final case class ValidInput(selectedText: String, artifactLanguage: String)
  private final case class Translation(text: String, detectedLanguage: Option[String])
  private final case class Markers(open: String, close: String)
  private final case class RawResponse(
      statusCode: Int,
      headers: java.net.http.HttpHeaders,
      body: Array[Byte]
  )

  private val TargetLanguage = "en"
  private val MarkerPairs = Vector(
    Markers("\u27e6", "\u27e7"),
    Markers("\u27ea", "\u27eb"),
    Markers("\u3016", "\u3017"),
    Markers("\u301a", "\u301b")
  )
  private val MaxTranslationResponseBytes = 2 * 1024 * 1024
  private val XsrfPattern = "\"xsrf\",\"([^\"]+)\"".r

  require(httpClient.followRedirects() == HttpClient.Redirect.NEVER,
    "Google HTTP client must not follow redirects")
  require(!requestTimeout.isZero && !requestTimeout.isNegative,
    "Google request timeout must be positive")
  require(!overallTimeout.isZero && !overallTimeout.isNegative,
    "Google overall timeout must be positive")
  validateEndpoint(translationEndpoint, "translation")
  validateEndpoint(speechEndpoint, "speech")

  override def prepare(
      sentence: String,
      selection: SelectionSpan,
      artifactLanguage: String
  ): IO[GooglePreparation] =
    val operation =
      for
        input <- IO.fromEither(validateInput(sentence, selection, artifactLanguage))
        sentenceResult <- translate(sentence, "auto", "sentence translation")
        sourceLanguage <- IO.fromEither(
          resolveSourceLanguage(sentenceResult.detectedLanguage, input.artifactLanguage)
        )
        prepared <- (
          translateSelection(sentence, selection, input.selectedText, sourceLanguage),
          synthesize(sentence, sourceLanguage)
        ).parTupled
        (targetGloss, audioBytes) = prepared
        enrichment <- IO.fromEither(
          Enrichment(targetGloss, sentenceResult.text).validate.left.map: message =>
            new IllegalStateException(s"Google returned invalid enrichment: $message")
        )
        filename = audioFilename(sentence, input.selectedText, sourceLanguage)
        audioSha256 = sha256(audioBytes)
      yield GooglePreparation(
        enrichment,
        sourceLanguage,
        filename,
        audioBytes,
        audioSha256
      )
    operation.timeoutTo(
      overallTimeout.toNanos.nanos,
      IO.raiseError(
        new IllegalStateException(
          s"Google preparation timed out after ${overallTimeout.toMillis} ms"
        )
      )
    )

  private def validateInput(
      sentence: String,
      selection: SelectionSpan,
      artifactLanguage: String
  ): Either[Throwable, ValidInput] =
    for
      _ <- requireValue(sentence.trim.nonEmpty, "sentence must not be empty")
      _ <- requireValue(isWellFormedUtf16(sentence), "sentence contains malformed UTF-16")
      _ <- requireValue(
        sentence.length <= MaxSentenceUtf16,
        s"sentence exceeds the $MaxSentenceUtf16 UTF-16 unit limit"
      )
      _ <- requireValue(artifactLanguage.trim.nonEmpty, "artifact language must not be empty")
      _ <- requireValue(
        isWellFormedUtf16(artifactLanguage),
        "artifact language contains malformed UTF-16"
      )
      _ <- selection.validate(sentence).left.map: message =>
        new IllegalArgumentException(s"invalid exact selection span: $message")
      selected <- selection.extract(sentence).left.map: message =>
        new IllegalArgumentException(s"invalid exact selection span: $message")
      _ <- requireValue(selected.trim.nonEmpty, "selection must not be empty")
    yield ValidInput(selected, normalizeLanguage(artifactLanguage))

  private def requireValue(condition: Boolean, message: String): Either[Throwable, Unit] =
    Either.cond(condition, (), new IllegalArgumentException(message))

  private def resolveSourceLanguage(
      detectedLanguage: Option[String],
      artifactLanguage: String
  ): Either[Throwable, String] =
    val detected = detectedLanguage
      .map(normalizeLanguage)
      .filter(language => language.nonEmpty && language != "auto")
    val resolved = detected.getOrElse(artifactLanguage)
    Either.cond(
      resolved.nonEmpty && resolved != "auto",
      resolved,
      new IllegalStateException(
        "Google did not detect a source language and the artifact language is unknown"
      )
    )

  private def translateSelection(
      sentence: String,
      selection: SelectionSpan,
      selectedText: String,
      sourceLanguage: String
  ): IO[String] =
    val markers = chooseMarkers(sentence)
    val markedSentence =
      sentence.substring(0, selection.startUtf16) + markers.open + selectedText + markers.close +
        sentence.substring(selection.endUtf16)

    translate(markedSentence, sourceLanguage, "contextual selection translation").flatMap:
      contextual =>
        extractMarked(contextual.text, markers) match
          case Some(value) => IO.pure(value)
          case None =>
            translate(selectedText, sourceLanguage, "isolated selection translation").map(_.text)

  private def chooseMarkers(sentence: String): Markers =
    MarkerPairs.find(markersAbsent(sentence, _)).getOrElse:
      Iterator
        .from(1)
        .map(index => Markers(s"\u27e6$index\u27e6", s"\u27e7$index\u27e7"))
        .find(markersAbsent(sentence, _))
        .get

  private def markersAbsent(sentence: String, markers: Markers): Boolean =
    !sentence.contains(markers.open) && !sentence.contains(markers.close)

  private def extractMarked(value: String, markers: Markers): Option[String] =
    val start = value.indexOf(markers.open)
    if start < 0 then None
    else
      val contentStart = start + markers.open.length
      val end = value.indexOf(markers.close, contentStart)
      Option.when(end >= contentStart)(value.substring(contentStart, end).trim).filter(_.nonEmpty)

  private def translate(text: String, sourceLanguage: String, operation: String): IO[Translation] =
    val requestBody = GoogleTranslationRpc.packageRpc(text, sourceLanguage, TargetLanguage)
    postTranslation(requestBody, operation).flatMap: firstResponse =>
      val finalResponse =
        if firstResponse.statusCode == 400 then
          val responseText = new String(firstResponse.body, StandardCharsets.UTF_8)
          XsrfPattern.findFirstMatchIn(responseText) match
            case Some(tokenMatch) =>
              postTranslation(
                requestBody + "&at=" + JavaScriptEncoding.encodeURIComponent(tokenMatch.group(1)),
                operation
              )
            case None => IO.pure(firstResponse)
        else IO.pure(firstResponse)

      finalResponse.flatMap: response =>
        successfulBody(response, s"Google $operation", "application/json").flatMap: bytes =>
          IO.fromEither(
            GoogleTranslationJson.parse(bytes).left.map: message =>
              new IllegalStateException(
                s"Google $operation returned an invalid translation response: $message"
              )
          ).map(parsed => Translation(parsed._1, parsed._2))

  private def postTranslation(requestBody: String, operation: String): IO[RawResponse] =
    val request = HttpRequest
      .newBuilder(translationEndpoint)
      .timeout(requestTimeout)
      .header("Content-Type", TranslationContentType)
      .header("User-Agent", UserAgent)
      .header("Referer", Referer)
      .POST(HttpRequest.BodyPublishers.ofString(requestBody, StandardCharsets.UTF_8))
      .build()
    execute(
      request,
      s"Google $operation",
      MaxTranslationResponseBytes,
      s"Google $operation response exceeded $MaxTranslationResponseBytes bytes"
    )

  private def synthesize(sentence: String, sourceLanguage: String): IO[Array[Byte]] =
    if sourceLanguage == "fa" then synthesizePersian(sentence)
    else synthesizeGoogle(sentence, sourceLanguage)

  private def synthesizeGoogle(sentence: String, sourceLanguage: String): IO[Array[Byte]] =
    val uri = URI.create(
      s"${speechEndpoint.toASCIIString}?ie=UTF-8&q=${JavaScriptEncoding.encodeURIComponent(sentence)}" +
        s"&tl=$sourceLanguage&client=tw-ob"
    )
    val request = HttpRequest
      .newBuilder(uri)
      .timeout(requestTimeout)
      .header("Referer", Referer)
      .header("User-Agent", UserAgent)
      .GET()
      .build()
    execute(
      request,
      "Google speech request",
      MaxAudioBytes,
      "Google speech audio exceeds the 10 MiB limit"
    ).flatMap: response =>
      successfulBody(response, "Google speech request", "audio/mpeg").flatMap: bytes =>
        validateAudio(bytes, "Google speech")

  private def synthesizePersian(sentence: String): IO[Array[Byte]] =
    val secMsGec = MicrosoftSpeechProtocol.secMsGec(currentTimeMillis())
    val connectionId = randomId().replace("-", "")
    val muid = MicrosoftSpeechProtocol.muid(randomMuidBytes())
    val configTimestamp = MicrosoftSpeechProtocol.timestamp(currentTimeMillis())
    val requestId = randomId().replace("-", "")
    val ssmlTimestamp = MicrosoftSpeechProtocol.timestamp(currentTimeMillis())
    val request = MicrosoftSpeechRequest(
      uri = MicrosoftSpeechProtocol.uri(LiveMicrosoftSpeechEndpoint, secMsGec, connectionId),
      headers = MicrosoftSpeechProtocol.headers(muid),
      messages = Vector(
        MicrosoftSpeechProtocol.speechConfig(configTimestamp),
        MicrosoftSpeechProtocol.ssml(sentence, requestId, ssmlTimestamp)
      ),
      requestTimeout = requestTimeout,
      maxAudioBytes = MaxAudioBytes
    )

    microsoftTransport
      .synthesize(request)
      .timeoutTo(
        requestTimeout.toNanos.nanos,
        IO.raiseError(
          new IllegalStateException(
            s"Microsoft speech request timed out after ${requestTimeout.toMillis} ms"
          )
        )
      )
      .flatMap(bytes => validateAudio(bytes, "Microsoft speech"))

  private def validateAudio(bytes: Array[Byte], provider: String): IO[Array[Byte]] =
    if bytes.isEmpty then
      IO.raiseError(new IllegalStateException(s"$provider returned empty audio"))
    else if bytes.length > MaxAudioBytes then
      IO.raiseError(new IllegalStateException(s"$provider audio exceeds the 10 MiB limit"))
    else IO.pure(bytes)

  private def execute(
      request: HttpRequest,
      operation: String,
      maxBytes: Int,
      tooLargeMessage: String
  ): IO[RawResponse] = IO.interruptibleMany:
    val handler = new HttpResponse.BodyHandler[Array[Byte]]:
      override def apply(
          responseInfo: HttpResponse.ResponseInfo
      ): HttpResponse.BodySubscriber[Array[Byte]] =
        new LimitedByteArraySubscriber(maxBytes, tooLargeMessage)

    val response =
      try httpClient.send(request, handler)
      catch
        case error: HttpTimeoutException =>
          throw new IllegalStateException(
            s"$operation timed out after ${requestTimeout.toMillis} ms",
            error
          )
        case error: InterruptedException =>
          Thread.currentThread().interrupt()
          throw error
        case error: IOException =>
          findTooLarge(error) match
            case Some(tooLarge) =>
              throw new IllegalStateException(tooLarge.getMessage, error)
            case None =>
              throw new IllegalStateException(
                s"$operation failed: ${errorMessage(error)}",
                error
              )
        case error: RuntimeException =>
          findTooLarge(error) match
            case Some(tooLarge) =>
              throw new IllegalStateException(tooLarge.getMessage, error)
            case None => throw error

    RawResponse(response.statusCode(), response.headers(), response.body())

  private def successfulBody(
      response: RawResponse,
      operation: String,
      expectedContentType: String
  ): IO[Array[Byte]] = IO.delay:
    if response.statusCode != 200 then
      val redirect = response.statusCode >= 300 && response.statusCode < 400
      val detail = if redirect then "redirects are not allowed" else "request failed"
      throw new IllegalStateException(
        s"$operation $detail with HTTP status ${response.statusCode}"
      )
    responseContentType(response.headers) match
      case None =>
        throw new IllegalStateException(
          s"$operation returned no Content-Type; expected $expectedContentType"
        )
      case Some(actual) if actual != expectedContentType =>
        throw new IllegalStateException(
          s"$operation returned Content-Type '$actual'; expected $expectedContentType"
        )
      case _ => response.body

  private def responseContentType(headers: java.net.http.HttpHeaders): Option[String] =
    val value = headers.firstValue("Content-Type")
    Option.when(value.isPresent)(value.get())
      .map(_.takeWhile(_ != ';').trim.toLowerCase(Locale.ROOT))
      .filter(_.nonEmpty)

  private def audioFilename(sentence: String, selectedText: String, language: String): String =
    val text = if sentence.length <= 200 then sentence else selectedText
    val textHash = sha256(text.getBytes(StandardCharsets.UTF_8)).take(16)
    s"clausula_${language}_${textHash}.mp3"

  private def sha256(bytes: Array[Byte]): String =
    HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))

  private def normalizeLanguage(value: String): String =
    value.trim.toLowerCase(Locale.ROOT).replace('_', '-')

  private def validateEndpoint(endpoint: URI, name: String): Unit =
    val scheme = Option(endpoint.getScheme).map(_.toLowerCase(Locale.ROOT))
    require(
      scheme.contains("http") || scheme.contains("https"),
      s"Google $name endpoint must be an absolute HTTP(S) URI"
    )
    require(Option(endpoint.getHost).exists(_.nonEmpty),
      s"Google $name endpoint must have a host")
    require(endpoint.getRawQuery == null && endpoint.getRawFragment == null,
      s"Google $name endpoint must not contain a query or fragment")
    require(endpoint.getRawUserInfo == null,
      s"Google $name endpoint must not contain user information")

  private def isWellFormedUtf16(value: String): Boolean =
    var index = 0
    while index < value.length do
      val character = value.charAt(index)
      if Character.isHighSurrogate(character) then
        if index + 1 >= value.length || !Character.isLowSurrogate(value.charAt(index + 1)) then
          return false
        index += 2
      else if Character.isLowSurrogate(character) then return false
      else index += 1
    true

  private def findTooLarge(error: Throwable): Option[ResponseTooLarge] =
    Iterator
      .iterate(Option(error))(_.flatMap(current => Option(current.getCause)))
      .takeWhile(_.nonEmpty)
      .flatten
      .collectFirst { case tooLarge: ResponseTooLarge => tooLarge }

  private def errorMessage(error: Throwable): String =
    Option(error.getMessage).map(_.trim).filter(_.nonEmpty)
      .getOrElse(error.getClass.getSimpleName)

private final class ResponseTooLarge(message: String) extends RuntimeException(message)

private final class LimitedByteArraySubscriber(limit: Int, tooLargeMessage: String)
    extends HttpResponse.BodySubscriber[Array[Byte]]:
  private val result = new CompletableFuture[Array[Byte]]()
  private val output = new ByteArrayOutputStream(math.min(limit, 8192))
  private var subscription: Flow.Subscription = null
  private var size = 0
  private var done = false

  override def getBody(): CompletionStage[Array[Byte]] = result

  override def onSubscribe(value: Flow.Subscription): Unit =
    if subscription != null then value.cancel()
    else
      subscription = value
      value.request(1)

  override def onNext(buffers: java.util.List[ByteBuffer]): Unit =
    if !done then
      val iterator = buffers.iterator()
      while iterator.hasNext && !done do
        val buffer = iterator.next()
        val length = buffer.remaining()
        if length > limit - size then fail(new ResponseTooLarge(tooLargeMessage))
        else
          val bytes = new Array[Byte](length)
          buffer.get(bytes)
          output.write(bytes)
          size += length
      if !done then subscription.request(1)

  override def onError(error: Throwable): Unit =
    if !done then
      done = true
      result.completeExceptionally(error)

  override def onComplete(): Unit =
    if !done then
      done = true
      result.complete(output.toByteArray)

  private def fail(error: Throwable): Unit =
    done = true
    subscription.cancel()
    result.completeExceptionally(error)

private[backend] object JavaScriptEncoding:
  def encodeURIComponent(value: String): String =
    val output = new StringBuilder(value.length)
    value.getBytes(StandardCharsets.UTF_8).foreach: byte =>
      val unsigned = byte & 0xff
      val character = unsigned.toChar
      if isEncodeUriComponentSafe(unsigned, character) then output.append(character)
      else output.append('%').append(f"$unsigned%02X")
    output.result()

  def trim(value: String): String =
    var start = 0
    var end = value.length
    while start < end && isEcmaScriptWhitespace(value.charAt(start)) do start += 1
    while end > start && isEcmaScriptWhitespace(value.charAt(end - 1)) do end -= 1
    value.substring(start, end)

  def jsonString(value: String): String =
    val output = new StringBuilder(value.length + 2)
    output.append('"')
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

  private def isEncodeUriComponentSafe(unsigned: Int, character: Char): Boolean =
    (unsigned >= 'a' && unsigned <= 'z') ||
      (unsigned >= 'A' && unsigned <= 'Z') ||
      (unsigned >= '0' && unsigned <= '9') ||
      "-_.!~*'()".contains(character)

  private def isEcmaScriptWhitespace(character: Char): Boolean =
    (character >= '\u0009' && character <= '\u000d') ||
      character == '\u0020' ||
      character == '\u00a0' ||
      character == '\u1680' ||
      (character >= '\u2000' && character <= '\u200a') ||
      character == '\u2028' ||
      character == '\u2029' ||
      character == '\u202f' ||
      character == '\u205f' ||
      character == '\u3000' ||
      character == '\ufeff'

private[backend] object GoogleTranslationRpc:
  def packageRpc(text: String, sourceLanguage: String, targetLanguage: String): String =
    val parameter =
      s"[[${JavaScriptEncoding.jsonString(JavaScriptEncoding.trim(text))}," +
        s"${JavaScriptEncoding.jsonString(sourceLanguage)}," +
        s"${JavaScriptEncoding.jsonString(targetLanguage)},true],[1]]"
    val rpc =
      s"[[[\"MkEWBc\",${JavaScriptEncoding.jsonString(parameter)},null,\"generic\"]]]"
    s"f.req=${JavaScriptEncoding.encodeURIComponent(rpc)}&"

private[backend] final case class MicrosoftBinaryFrame(header: String, payload: Array[Byte])

private[backend] object MicrosoftSpeechProtocol:
  val SecMsGecVersion = "1-143.0.3650.75"
  val TrustedClientToken = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
  val Voice = "fa-IR-DilaraNeural"
  val OutputFormat = "audio-24khz-48kbitrate-mono-mp3"
  val BrowserOrigin = "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold"
  val BrowserUserAgent =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"

  private val WindowsEpochSeconds = 11644473600L
  private val TicksPerSecond = 10000000L
  private val TimestampFormatter = DateTimeFormatter
    .ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.ROOT)
    .withZone(ZoneOffset.UTC)

  def secMsGec(epochMillis: Long, clockSkewSeconds: Long = 0L): String =
    val epochSeconds = Math.floorDiv(epochMillis, 1000L) + clockSkewSeconds
    val intervalSeconds =
      Math.floorDiv(epochSeconds + WindowsEpochSeconds, 300L) * 300L
    val ticks = intervalSeconds * TicksPerSecond
    val source = s"$ticks$TrustedClientToken".getBytes(StandardCharsets.UTF_8)
    HexFormat.of().withUpperCase()
      .formatHex(MessageDigest.getInstance("SHA-256").digest(source))

  def muid(bytes: Array[Byte]): String =
    require(bytes.length == 16, "Microsoft speech MUID must contain 16 bytes")
    HexFormat.of().withUpperCase().formatHex(bytes)

  def timestamp(epochMillis: Long): String =
    TimestampFormatter.format(Instant.ofEpochMilli(epochMillis))

  def uri(endpoint: URI, secMsGec: String, connectionId: String): URI =
    URI.create(
      s"${endpoint.toASCIIString}?TrustedClientToken=$TrustedClientToken" +
        s"&Sec-MS-GEC=$secMsGec" +
        s"&Sec-MS-GEC-Version=$SecMsGecVersion" +
        s"&ConnectionId=$connectionId"
    )

  def headers(muid: String): Vector[(String, String)] =
    Vector(
      "Pragma" -> "no-cache",
      "Cache-Control" -> "no-cache",
      "Origin" -> BrowserOrigin,
      "User-Agent" -> BrowserUserAgent,
      "Accept-Encoding" -> "gzip, deflate, br, zstd",
      "Accept-Language" -> "en-US,en;q=0.9",
      "Cookie" -> s"muid=$muid;"
    )

  def speechConfig(timestamp: String): String =
    s"X-Timestamp:$timestamp\r\n" +
      "Content-Type:application/json; charset=utf-8\r\n" +
      "Path:speech.config\r\n\r\n" +
      s"{\"context\":{\"synthesis\":{\"audio\":{\"metadataoptions\":" +
      s"{\"sentenceBoundaryEnabled\":false,\"wordBoundaryEnabled\":true}," +
      s"\"outputFormat\":\"$OutputFormat\"}}}}"

  def ssml(sentence: String, requestId: String, timestamp: String): String =
    s"X-RequestId:$requestId\r\n" +
      "Content-Type:application/ssml+xml\r\n" +
      s"X-Timestamp:${timestamp}Z\r\n" +
      "Path:ssml\r\n\r\n" +
      "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='fa'>" +
      s"<voice name='$Voice'>" +
      "<prosody pitch='+0Hz' rate='+0%' volume='+0%'>" +
      escapeXml(sentence) +
      "</prosody></voice></speak>"

  def parseBinaryFrame(bytes: Array[Byte]): Either[String, MicrosoftBinaryFrame] =
    if bytes.length < 2 then Left("Microsoft speech binary frame is missing its header length")
    else
      val headerLength = ((bytes(0) & 0xff) << 8) | (bytes(1) & 0xff)
      if headerLength > bytes.length - 2 then
        Left("Microsoft speech binary frame has an invalid header length")
      else
        decodeUtf8(bytes, 2, headerLength).map: header =>
          MicrosoftBinaryFrame(header, bytes.slice(headerLength + 2, bytes.length))

  private def escapeXml(value: String): String =
    val output = new StringBuilder(value.length)
    value.foreach:
      case '&'  => output.append("&amp;")
      case '<'  => output.append("&lt;")
      case '>'  => output.append("&gt;")
      case '"'  => output.append("&quot;")
      case '\'' => output.append("&apos;")
      case character => output.append(character)
    output.result()

  private def decodeUtf8(
      bytes: Array[Byte],
      offset: Int,
      length: Int
  ): Either[String, String] =
    try
      val decoder = StandardCharsets.UTF_8
        .newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
      Right(decoder.decode(ByteBuffer.wrap(bytes, offset, length)).toString)
    catch case error: Throwable =>
      Left(s"Microsoft speech binary header is not valid UTF-8: ${error.getMessage}")

private final class JdkMicrosoftSpeechTransport(httpClient: HttpClient)
    extends MicrosoftSpeechTransport:
  override def synthesize(request: MicrosoftSpeechRequest): IO[Array[Byte]] = IO.defer:
    val listener = new MicrosoftSpeechListener(request)
    val builder = httpClient.newWebSocketBuilder().connectTimeout(request.requestTimeout)
    request.headers.foreach((name, value) => builder.header(name, value))
    val connection = builder.buildAsync(request.uri, listener)

    IO.fromCompletableFuture(IO.pure(connection))
      .flatMap: _ =>
        IO.fromCompletableFuture(IO.pure(listener.result))
      .onCancel(
        IO.delay:
          connection.cancel(true)
          listener.abort()
      )

private[backend] final class MicrosoftSpeechListener(request: MicrosoftSpeechRequest)
    extends WebSocket.Listener:
  private val MaxProtocolBytes = 64 * 1024
  private val resultFuture = new CompletableFuture[Array[Byte]]()
  private val audio = new ByteArrayOutputStream()
  private val binaryMessage = new ByteArrayOutputStream()
  private val textMessage = new StringBuilder
  private var socket: WebSocket = null
  private var done = false

  def result: CompletableFuture[Array[Byte]] = resultFuture

  def abort(): Unit = synchronized:
    if !done then
      done = true
      resultFuture.completeExceptionally(
        new CancellationException("Microsoft speech request was cancelled")
      )
      if socket != null then socket.abort()

  override def onOpen(webSocket: WebSocket): Unit = synchronized:
    socket = webSocket
    if done then webSocket.abort()
    else
      var sent = CompletableFuture.completedFuture(webSocket)
      request.messages.foreach: message =>
        sent = sent.thenCompose(_ => webSocket.sendText(message, true))
      sent.whenComplete: (_, error) =>
        if error != null then fail(error, webSocket)
      webSocket.request(1)

  override def onText(
      webSocket: WebSocket,
      data: CharSequence,
      last: Boolean
  ): CompletionStage[?] =
    synchronized:
      if !done then
        if data.length() > MaxProtocolBytes - textMessage.length then
          fail(new IllegalStateException("Microsoft speech text message is too large"), webSocket)
        else
          textMessage.append(data)
          if last then
            val message = textMessage.result()
            textMessage.clear()
            if message.contains("Path:turn.end") then finish(webSocket)
      if !done then webSocket.request(1)
    CompletableFuture.completedFuture(null)

  override def onBinary(
      webSocket: WebSocket,
      data: ByteBuffer,
      last: Boolean
  ): CompletionStage[?] =
    synchronized:
      if !done then
        val length = data.remaining()
        val maxMessageBytes = request.maxAudioBytes + MaxProtocolBytes
        if length > maxMessageBytes - binaryMessage.size() then
          fail(new ResponseTooLarge("Microsoft speech audio exceeds the 10 MiB limit"), webSocket)
        else
          val bytes = new Array[Byte](length)
          data.get(bytes)
          binaryMessage.write(bytes)
          if last then
            val message = binaryMessage.toByteArray
            binaryMessage.reset()
            handleBinary(message, webSocket)
      if !done then webSocket.request(1)
    CompletableFuture.completedFuture(null)

  override def onClose(
      webSocket: WebSocket,
      statusCode: Int,
      reason: String
  ): CompletionStage[?] =
    synchronized:
      if !done then
        fail(
          new IllegalStateException(s"Microsoft speech WebSocket closed: $statusCode $reason"),
          webSocket,
          abortSocket = false
        )
    CompletableFuture.completedFuture(null)

  override def onError(webSocket: WebSocket, error: Throwable): Unit = synchronized:
    if !done then fail(error, webSocket)

  private def handleBinary(message: Array[Byte], webSocket: WebSocket): Unit =
    MicrosoftSpeechProtocol.parseBinaryFrame(message) match
      case Left(error) => fail(new IllegalStateException(error), webSocket)
      case Right(frame) =>
        if frame.header.contains("Path:audio") && frame.payload.nonEmpty then
          if frame.payload.length > request.maxAudioBytes - audio.size() then
            fail(new ResponseTooLarge("Microsoft speech audio exceeds the 10 MiB limit"), webSocket)
          else audio.write(frame.payload)
        if !done && frame.header.contains("Path:turn.end") then finish(webSocket)

  private def finish(webSocket: WebSocket): Unit =
    if audio.size() == 0 then
      fail(new IllegalStateException("Microsoft speech returned empty audio"), webSocket)
    else
      done = true
      resultFuture.complete(audio.toByteArray)
      webSocket.sendClose(WebSocket.NORMAL_CLOSURE, "")
      ()

  private def fail(
      error: Throwable,
      webSocket: WebSocket,
      abortSocket: Boolean = true
  ): Unit = synchronized:
    if !done then
      done = true
      resultFuture.completeExceptionally(error)
      if abortSocket then webSocket.abort()

private[backend] object GoogleTranslationJson:
  private enum JsonValue:
    case StringValue(value: String)
    case ArrayValue(values: Vector[JsonValue])
    case ObjectValue(values: Map[String, JsonValue])
    case NumberValue(value: String)
    case BooleanValue(value: Boolean)
    case NullValue

  import JsonValue.*

  def parse(bytes: Array[Byte]): Either[String, (String, Option[String])] =
    for
      text <- decodeUtf8(bytes)
      line <- Either.fromOption(
        text.split("\n", -1).iterator.find(_.contains("MkEWBc")),
        "MkEWBc response line is missing"
      )
      outer <- new Parser(line).parse()
      outerRows <- asArray(outer, "outer JSON")
      outerRow <- element(outerRows, 0, "outer JSON[0]")
      outerFields <- asArray(outerRow, "outer JSON[0]")
      encodedInnerValue <- element(outerFields, 2, "outer JSON[0][2]")
      encodedInner <- asString(encodedInnerValue, "outer JSON[0][2]")
      inner <- new Parser(encodedInner).parse()
      parsed <- parseInner(inner)
    yield parsed

  private def decodeUtf8(bytes: Array[Byte]): Either[String, String] =
    try
      val decoder = StandardCharsets.UTF_8
        .newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
      val decoded: CharBuffer = decoder.decode(ByteBuffer.wrap(bytes))
      Right(decoded.toString)
    catch case error: Throwable => Left(s"response is not valid UTF-8: ${error.getMessage}")

  private def parseInner(value: JsonValue): Either[String, (String, Option[String])] =
    for
      root <- asArray(value, "inner JSON")
      metadataValue <- element(root, 0, "inner JSON[0]")
      metadata <- asArray(metadataValue, "inner JSON[0]")
      responseContainerValue <- element(root, 1, "inner JSON[1]")
      responseContainer <- asArray(responseContainerValue, "inner JSON[1]")
      responseValue <- element(responseContainer, 0, "inner JSON[1][0]")
      response <- asArray(responseValue, "inner JSON[1][0]")
      translated <- translationText(response)
      detected <- detectedLanguage(metadata)
    yield translated -> detected

  private def translationText(response: Vector[JsonValue]): Either[String, String] =
    val parts = response match
      case Vector(ArrayValue(fields)) if fields.length > 5 =>
        asArray(fields(5), "inner JSON[1][0][0][5]").flatMap: sentences =>
          sentences.zipWithIndex.foldLeft(Right(Vector.empty): Either[String, Vector[String]]):
            case (current, (sentence, index)) =>
              for
                values <- current
                fields <- asArray(sentence, s"translation sentence[$index]")
                value <- element(fields, 0, s"translation sentence[$index][0]")
                text <- asString(value, s"translation sentence[$index][0]")
              yield values :+ JavaScriptEncoding.trim(text)
      case segments =>
        segments.zipWithIndex.foldLeft(Right(Vector.empty): Either[String, Vector[String]]):
          case (current, (segment, index)) =>
            current.flatMap: values =>
              segment match
                case ArrayValue(fields) =>
                  fields.headOption match
                    case Some(StringValue(text)) if text.nonEmpty =>
                      Right(values :+ JavaScriptEncoding.trim(text))
                    case Some(NullValue) | Some(StringValue(_)) | None => Right(values)
                    case Some(_) => Left(s"translation segment[$index][0] is not a string")
                case _ => Right(values)

    parts.flatMap: values =>
      val translated = JavaScriptEncoding.trim(values.mkString(" "))
      Either.cond(translated.nonEmpty, translated, "translated text is empty or missing")

  private def detectedLanguage(metadata: Vector[JsonValue]): Either[String, Option[String]] =
    metadata.lift(2) match
      case Some(StringValue(language)) =>
        Right(Option(JavaScriptEncoding.trim(language)).filter(_.nonEmpty))
      case Some(NullValue) | None => Right(None)
      case Some(_) => Left("inner JSON[0][2] is not a string or null")

  private def asArray(value: JsonValue, path: String): Either[String, Vector[JsonValue]] =
    value match
      case ArrayValue(values) => Right(values)
      case _                  => Left(s"$path is not an array")

  private def asString(value: JsonValue, path: String): Either[String, String] =
    value match
      case StringValue(text) => Right(text)
      case _                 => Left(s"$path is not a string")

  private def element(
      values: Vector[JsonValue],
      index: Int,
      path: String
  ): Either[String, JsonValue] =
    values.lift(index).toRight(s"$path is missing")

  private def isJsonWhitespace(character: Char): Boolean =
    character == ' ' || character == '\t' || character == '\r' || character == '\n'

  private final class JsonFailure(message: String) extends RuntimeException(message)

  private final class Parser(input: String):
    private val MaxDepth = 100
    private var index = 0

    def parse(): Either[String, JsonValue] =
      try
        skipWhitespace()
        val value = parseValue(0)
        skipWhitespace()
        if index != input.length then fail("unexpected trailing content")
        Right(value)
      catch case error: JsonFailure => Left(error.getMessage)

    private def parseValue(depth: Int): JsonValue =
      if depth > MaxDepth then fail(s"JSON nesting exceeds $MaxDepth levels")
      if index >= input.length then fail("unexpected end of input")
      input.charAt(index) match
        case '"' => StringValue(parseString())
        case '[' => parseArray(depth + 1)
        case '{' => parseObject(depth + 1)
        case 't' => parseLiteral("true", BooleanValue(true))
        case 'f' => parseLiteral("false", BooleanValue(false))
        case 'n' => parseLiteral("null", NullValue)
        case '-' => NumberValue(parseNumber())
        case character if character >= '0' && character <= '9' => NumberValue(parseNumber())
        case character => fail(s"unexpected character '$character'")

    private def parseArray(depth: Int): JsonValue =
      index += 1
      skipWhitespace()
      val values = Vector.newBuilder[JsonValue]
      if consume(']') then ArrayValue(values.result())
      else
        var complete = false
        while !complete do
          values += parseValue(depth)
          skipWhitespace()
          if consume(']') then complete = true
          else
            expect(',')
            skipWhitespace()
        ArrayValue(values.result())

    private def parseObject(depth: Int): JsonValue =
      index += 1
      skipWhitespace()
      var fields = Map.empty[String, JsonValue]
      if consume('}') then ObjectValue(fields)
      else
        var complete = false
        while !complete do
          if index >= input.length || input.charAt(index) != '"' then
            fail("object key must be a string")
          val name = parseString()
          skipWhitespace()
          expect(':')
          skipWhitespace()
          fields = fields.updated(name, parseValue(depth))
          skipWhitespace()
          if consume('}') then complete = true
          else
            expect(',')
            skipWhitespace()
        ObjectValue(fields)

    private def parseString(): String =
      expect('"')
      val output = new StringBuilder
      while index < input.length do
        val character = input.charAt(index)
        index += 1
        character match
          case '"' => return output.result()
          case '\\' => appendEscape(output)
          case value if value < ' ' => fail("unescaped control character in string")
          case value if Character.isHighSurrogate(value) =>
            if index >= input.length || !Character.isLowSurrogate(input.charAt(index)) then
              fail("unpaired high surrogate in string")
            output.append(value)
            output.append(input.charAt(index))
            index += 1
          case value if Character.isLowSurrogate(value) =>
            fail("unpaired low surrogate in string")
          case value => output.append(value)
      fail("unterminated string")

    private def appendEscape(output: StringBuilder): Unit =
      if index >= input.length then fail("unterminated escape sequence")
      val escaped = input.charAt(index)
      index += 1
      escaped match
        case '"' => output.append('"')
        case '\\' => output.append('\\')
        case '/' => output.append('/')
        case 'b' => output.append('\b')
        case 'f' => output.append('\f')
        case 'n' => output.append('\n')
        case 'r' => output.append('\r')
        case 't' => output.append('\t')
        case 'u' => appendUnicodeEscape(output)
        case value => fail(s"invalid escape sequence \\$value")

    private def appendUnicodeEscape(output: StringBuilder): Unit =
      val first = parseHexCodeUnit()
      if Character.isHighSurrogate(first) then
        if index + 2 > input.length || input.charAt(index) != '\\' || input.charAt(index + 1) != 'u'
        then fail("escaped high surrogate is not followed by a low surrogate")
        index += 2
        val second = parseHexCodeUnit()
        if !Character.isLowSurrogate(second) then
          fail("escaped high surrogate is not followed by a low surrogate")
        output.append(first)
        output.append(second)
      else if Character.isLowSurrogate(first) then fail("unpaired escaped low surrogate")
      else output.append(first)

    private def parseHexCodeUnit(): Char =
      if index + 4 > input.length then fail("incomplete Unicode escape")
      var value = 0
      var count = 0
      while count < 4 do
        val digit = Character.digit(input.charAt(index), 16)
        if digit < 0 then fail("invalid Unicode escape")
        value = value * 16 + digit
        index += 1
        count += 1
      value.toChar

    private def parseNumber(): String =
      val start = index
      if consume('-') && index >= input.length then fail("incomplete number")
      if consume('0') then
        if index < input.length && input.charAt(index).isDigit then fail("leading zero in number")
      else
        requireDigit("number must contain an integer part")
        while index < input.length && input.charAt(index).isDigit do index += 1
      if consume('.') then
        requireDigit("fraction must contain a digit")
        while index < input.length && input.charAt(index).isDigit do index += 1
      if index < input.length && (input.charAt(index) == 'e' || input.charAt(index) == 'E') then
        index += 1
        if index < input.length && (input.charAt(index) == '+' || input.charAt(index) == '-') then
          index += 1
        requireDigit("exponent must contain a digit")
        while index < input.length && input.charAt(index).isDigit do index += 1
      input.substring(start, index)

    private def requireDigit(message: String): Unit =
      if index >= input.length || !input.charAt(index).isDigit then fail(message)

    private def parseLiteral(literal: String, value: JsonValue): JsonValue =
      if !input.regionMatches(index, literal, 0, literal.length) then
        fail(s"invalid literal, expected $literal")
      index += literal.length
      value

    private def skipWhitespace(): Unit =
      while index < input.length && isJsonWhitespace(input.charAt(index)) do index += 1

    private def consume(expected: Char): Boolean =
      if index < input.length && input.charAt(index) == expected then
        index += 1
        true
      else false

    private def expect(expected: Char): Unit =
      if !consume(expected) then fail(s"expected '$expected'")

    private def fail(message: String): Nothing =
      throw new JsonFailure(s"$message at offset $index")
