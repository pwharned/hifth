package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import cats.syntax.all.*
import com.github.pwharned.flashcards.shared.domain.{Enrichment, SelectionSpan}

import java.io.{ByteArrayOutputStream, IOException}
import java.net.{URI, URLEncoder}
import java.net.http.{HttpClient, HttpRequest, HttpResponse, HttpTimeoutException}
import java.nio.{ByteBuffer, CharBuffer}
import java.nio.charset.{CodingErrorAction, StandardCharsets}
import java.security.MessageDigest
import java.time.Duration
import java.util.{HexFormat, Locale}
import java.util.concurrent.{CompletableFuture, CompletionStage, Flow}
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

private[backend] object GoogleClient:
  private[backend] val LiveTranslationEndpoint: URI =
    URI.create("https://translate.googleapis.com/translate_a/single")
  private[backend] val LiveSpeechEndpoint: URI =
    URI.create("https://translate.googleapis.com/translate_tts")
  private[backend] val DefaultRequestTimeout: Duration = Duration.ofSeconds(30)
  private[backend] val DefaultOverallTimeout: Duration = Duration.ofMinutes(2)
  private[backend] val MaxSentenceUtf16: Int = 5000
  private[backend] val MaxTtsChunkUtf16: Int = 180
  private[backend] val MaxAudioBytes: Int = 10 * 1024 * 1024

  private val ConnectTimeout = Duration.ofSeconds(10)

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
      DefaultOverallTimeout
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
      overallTimeout
    )

private[backend] final class GoogleClient private (
    httpClient: HttpClient,
    translationEndpoint: URI,
    speechEndpoint: URI,
    requestTimeout: Duration,
    overallTimeout: Duration
) extends GoogleGateway:
  import GoogleClient.*

  private final case class ValidInput(selectedText: String, artifactLanguage: String)
  private final case class Translation(text: String, detectedLanguage: Option[String])
  private final case class Markers(open: String, close: String)

  private val TargetLanguage = "en"
  private val MarkerPairs = Vector(
    Markers("\u27e6", "\u27e7"),
    Markers("\u27ea", "\u27eb"),
    Markers("\u3016", "\u3017"),
    Markers("\u301a", "\u301b")
  )
  private val MaxTranslationResponseBytes = 2 * 1024 * 1024

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
        filename = audioFilename(sentence, sourceLanguage)
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
    val uri = queryUri(
      translationEndpoint,
      List(
        "client" -> "gtx",
        "sl" -> sourceLanguage,
        "tl" -> TargetLanguage,
        "dt" -> "t",
        "q" -> text
      )
    )
    getBytes(
      uri,
      s"Google $operation",
      "application/json",
      MaxTranslationResponseBytes,
      s"Google $operation response exceeded $MaxTranslationResponseBytes bytes"
    ).flatMap: bytes =>
      IO.fromEither(
        GoogleTranslationJson.parse(bytes).left.map: message =>
          new IllegalStateException(
            s"Google $operation returned an invalid translation response: $message"
          )
      ).map(parsed => Translation(parsed._1, parsed._2))

  private def synthesize(sentence: String, sourceLanguage: String): IO[Array[Byte]] =
    val chunks = splitForTts(sentence)
    IO.defer:
      val output = new ByteArrayOutputStream()
      chunks
        .foldLeft(IO.pure(output)): (current, chunk) =>
          current.flatMap: audio =>
            val remaining = MaxAudioBytes - audio.size()
            if remaining <= 0 then
              IO.raiseError(
                new IllegalStateException("Google speech audio exceeds the 10 MiB limit")
              )
            else
              val uri = queryUri(
                speechEndpoint,
                List(
                  "ie" -> "UTF-8",
                  "client" -> "tw-ob",
                  "tl" -> sourceLanguage,
                  "q" -> chunk
                )
              )
              getBytes(
                uri,
                "Google speech request",
                "audio/mpeg",
                remaining,
                "Google speech audio exceeds the 10 MiB limit"
              ).flatMap: part =>
                if part.isEmpty then
                  IO.raiseError(new IllegalStateException("Google speech returned empty audio"))
                else
                  IO.delay:
                    audio.write(part)
                    audio
        .flatMap: audio =>
          if audio.size() == 0 then
            IO.raiseError(new IllegalStateException("Google speech returned empty audio"))
          else IO.pure(audio.toByteArray)

  private def splitForTts(sentence: String): List[String] =
    val chunks = List.newBuilder[String]
    var remaining = stripWhitespace(sentence)
    while remaining.length > MaxTtsChunkUtf16 do
      var splitAt = -1
      var index = MaxTtsChunkUtf16
      while splitAt < 0 && index > MaxTtsChunkUtf16 / 2 do
        if isWhitespace(remaining.charAt(index)) then splitAt = index
        index -= 1
      if splitAt < 0 then splitAt = MaxTtsChunkUtf16
      if splitsSurrogatePair(remaining, splitAt) then splitAt -= 1

      val chunk = stripWhitespace(remaining.substring(0, splitAt))
      if chunk.nonEmpty then chunks += chunk
      remaining = stripWhitespace(remaining.substring(splitAt))
    if remaining.nonEmpty then chunks += remaining
    chunks.result()

  private def getBytes(
      uri: URI,
      operation: String,
      accept: String,
      maxBytes: Int,
      tooLargeMessage: String
  ): IO[Array[Byte]] = IO.interruptibleMany:
    val request = HttpRequest
      .newBuilder(uri)
      .timeout(requestTimeout)
      .header("Accept", accept)
      .GET()
      .build()
    val handler = new HttpResponse.BodyHandler[Array[Byte]]:
      override def apply(
          responseInfo: HttpResponse.ResponseInfo
      ): HttpResponse.BodySubscriber[Array[Byte]] =
        if responseInfo.statusCode() == 200 &&
          responseContentType(responseInfo.headers()).contains(accept)
        then
          new LimitedByteArraySubscriber(maxBytes, tooLargeMessage)
        else HttpResponse.BodySubscribers.replacing[Array[Byte]](Array.emptyByteArray)

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

    if response.statusCode() != 200 then
      val redirect = response.statusCode() >= 300 && response.statusCode() < 400
      val detail = if redirect then "redirects are not allowed" else "request failed"
      throw new IllegalStateException(
        s"$operation $detail with HTTP status ${response.statusCode()}"
      )
    responseContentType(response.headers()) match
      case None =>
        throw new IllegalStateException(
          s"$operation returned no Content-Type; expected $accept"
        )
      case Some(actual) if actual != accept =>
        throw new IllegalStateException(
          s"$operation returned Content-Type '$actual'; expected $accept"
        )
      case _ => ()
    response.body()

  private def responseContentType(headers: java.net.http.HttpHeaders): Option[String] =
    val value = headers.firstValue("Content-Type")
    Option.when(value.isPresent)(value.get())
      .map(_.takeWhile(_ != ';').trim.toLowerCase(Locale.ROOT))
      .filter(_.nonEmpty)

  private def queryUri(endpoint: URI, parameters: List[(String, String)]): URI =
    val query = parameters
      .map: (name, value) =>
        s"${encodeQuery(name)}=${encodeQuery(value)}"
      .mkString("&")
    URI.create(s"${endpoint.toASCIIString}?$query")

  private def encodeQuery(value: String): String =
    URLEncoder.encode(value, StandardCharsets.UTF_8)

  private def audioFilename(sentence: String, language: String): String =
    val identity = s"$language\u0000$sentence".getBytes(StandardCharsets.UTF_8)
    val identityHash = sha256(identity).take(20)
    val safeLanguage = language.replaceAll("[^a-z0-9-]", "_")
    s"clausula_${safeLanguage}_${identityHash}.mp3"

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

  private def stripWhitespace(value: String): String =
    var start = 0
    var end = value.length
    while start < end && isWhitespace(value.charAt(start)) do start += 1
    while end > start && isWhitespace(value.charAt(end - 1)) do end -= 1
    value.substring(start, end)

  private def isWhitespace(character: Char): Boolean =
    Character.isWhitespace(character) || Character.isSpaceChar(character)

  private def splitsSurrogatePair(value: String, index: Int): Boolean =
    index > 0 && index < value.length &&
      Character.isHighSurrogate(value.charAt(index - 1)) &&
      Character.isLowSurrogate(value.charAt(index))

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

private object GoogleTranslationJson:
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
      json <- new Parser(removeXssiPrefix(text)).parse()
      translated = translationParts(json).mkString.trim
      _ <- Either.cond(translated.nonEmpty, (), "translated text is empty or missing")
      detected = detectedLanguage(json).map(_.trim).filter(_.nonEmpty)
    yield translated -> detected

  private def decodeUtf8(bytes: Array[Byte]): Either[String, String] =
    try
      val decoder = StandardCharsets.UTF_8
        .newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
      val decoded: CharBuffer = decoder.decode(ByteBuffer.wrap(bytes))
      Right(decoded.toString)
    catch case error: Throwable => Left(s"response is not valid UTF-8: ${error.getMessage}")

  private def removeXssiPrefix(value: String): String =
    val trimmed = value.dropWhile(isJsonWhitespace)
    if trimmed.startsWith(")]}'") then
      val newline = trimmed.indexOf('\n')
      if newline >= 0 then trimmed.substring(newline + 1) else ""
    else trimmed

  private def translationParts(value: JsonValue): Vector[String] = value match
    case ArrayValue(root) =>
      root.headOption match
        case Some(segment @ ArrayValue(fields)) if fields.headOption.exists(isString) =>
          segmentText(segment).toVector
        case Some(ArrayValue(segments)) => segments.flatMap(segmentText)
        case Some(other)                => objectTranslationParts(other)
        case None                       => Vector.empty
    case other => objectTranslationParts(other)

  private def objectTranslationParts(value: JsonValue): Vector[String] = value match
    case ObjectValue(fields) =>
      directText(fields).toVector match
        case values if values.nonEmpty => values
        case _ =>
          List("sentences", "translations", "data", "result")
            .iterator
            .flatMap(fields.get)
            .map(containerTranslationParts)
            .find(_.nonEmpty)
            .getOrElse(Vector.empty)
    case ArrayValue(values) => values.flatMap(segmentText)
    case _                  => Vector.empty

  private def containerTranslationParts(value: JsonValue): Vector[String] = value match
    case ArrayValue(values) => values.flatMap(segmentText)
    case objectValue: ObjectValue => objectTranslationParts(objectValue)
    case StringValue(value) => Vector(value)
    case _ => Vector.empty

  private def segmentText(value: JsonValue): Option[String] = value match
    case ArrayValue(fields) => fields.headOption.collect { case StringValue(text) => text }
    case ObjectValue(fields) => directText(fields)
    case StringValue(text) => Some(text)
    case _ => None

  private def directText(fields: Map[String, JsonValue]): Option[String] =
    List("trans", "translatedText", "translation", "text")
      .iterator
      .flatMap(fields.get)
      .collectFirst { case StringValue(value) => value }

  private def detectedLanguage(value: JsonValue): Option[String] = value match
    case ArrayValue(values) if hasTranslationSegmentContainer(values) =>
      values.lift(2).collect { case StringValue(language) => language }
    case ArrayValue(_) => None
    case ObjectValue(fields) => detectedLanguageInObject(fields)
    case _ => None

  private def hasTranslationSegmentContainer(values: Vector[JsonValue]): Boolean =
    values.headOption.exists:
      case ArrayValue(segments) =>
        segments.headOption.forall:
          case ArrayValue(_) | ObjectValue(_) | NullValue => true
          case _                                           => false
      case _ => false

  private def detectedLanguageInObject(fields: Map[String, JsonValue]): Option[String] =
    directLanguage(fields).orElse:
      List("data", "translations", "sentences", "result")
        .iterator
        .flatMap(fields.get)
        .map(detectedLanguageInContainer)
        .collectFirst { case Some(language) => language }

  private def detectedLanguageInContainer(value: JsonValue): Option[String] = value match
    case ObjectValue(fields) => detectedLanguageInObject(fields)
    case ArrayValue(values) =>
      values.iterator
        .collect { case ObjectValue(fields) => detectedLanguageInObject(fields) }
        .collectFirst { case Some(language) => language }
    case _ => None

  private def directLanguage(fields: Map[String, JsonValue]): Option[String] =
    List("src", "source", "sourceLanguage", "detectedLanguage", "detectedSourceLanguage")
      .iterator
      .flatMap(fields.get)
      .collect { case StringValue(language) => language }
      .find(_.trim.nonEmpty)

  private def isString(value: JsonValue): Boolean = value match
    case StringValue(_) => true
    case _              => false

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
