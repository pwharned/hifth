package com.github.pwharned.flashcards.reviewer.backend

import cats.effect.IO
import com.github.plokhotnyuk.jsoniter_scala.core.*
import com.github.plokhotnyuk.jsoniter_scala.macros.JsonCodecMaker

import java.io.IOException
import java.net.{Proxy, ProxySelector, SocketAddress, URI}
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.Base64

private object ExternalJson:
  final case class DeckNamesRequest(action: String, version: Int)
  final case class DeckNamesResponse(
      result: Option[List[String]] = None,
      error: Option[String] = None
  )

  final case class StoreMediaParams(filename: String, data: String)
  final case class StoreMediaRequest(action: String, version: Int, params: StoreMediaParams)
  final case class StoreMediaResponse(result: Option[String] = None, error: Option[String] = None)

  final case class ModelFieldNamesParams(modelName: String)
  final case class ModelFieldNamesRequest(
      action: String,
      version: Int,
      params: ModelFieldNamesParams
  )
  final case class ModelFieldNamesResponse(
      result: Option[List[String]] = None,
      error: Option[String] = None
  )
  final case class ModelTemplatesRequest(
      action: String,
      version: Int,
      params: ModelFieldNamesParams
  )
  final case class ModelTemplatesResponse(
      result: Option[Map[String, Map[String, String]]] = None,
      error: Option[String] = None
  )

  final case class NoteOptions(allowDuplicate: Boolean)
  final case class AnkiNote(
      deckName: String,
      modelName: String,
      fields: Map[String, String],
      options: NoteOptions,
      tags: List[String]
  )
  final case class AddNoteParams(note: AnkiNote)
  final case class AddNoteRequest(action: String, version: Int, params: AddNoteParams)
  final case class AddNoteResponse(result: Option[Long] = None, error: Option[String] = None)
  final case class RemoteError(error: Option[String] = None)

  given JsonValueCodec[DeckNamesRequest] = JsonCodecMaker.make
  given JsonValueCodec[DeckNamesResponse] = JsonCodecMaker.make
  given JsonValueCodec[StoreMediaParams] = JsonCodecMaker.make
  given JsonValueCodec[StoreMediaRequest] = JsonCodecMaker.make
  given JsonValueCodec[StoreMediaResponse] = JsonCodecMaker.make
  given JsonValueCodec[ModelFieldNamesParams] = JsonCodecMaker.make
  given JsonValueCodec[ModelFieldNamesRequest] = JsonCodecMaker.make
  given JsonValueCodec[ModelFieldNamesResponse] = JsonCodecMaker.make
  given JsonValueCodec[ModelTemplatesRequest] = JsonCodecMaker.make
  given JsonValueCodec[ModelTemplatesResponse] = JsonCodecMaker.make
  given JsonValueCodec[NoteOptions] = JsonCodecMaker.make
  given JsonValueCodec[AnkiNote] = JsonCodecMaker.make
  given JsonValueCodec[AddNoteParams] = JsonCodecMaker.make
  given JsonValueCodec[AddNoteRequest] = JsonCodecMaker.make
  given JsonValueCodec[AddNoteResponse] = JsonCodecMaker.make
  given JsonValueCodec[RemoteError] = JsonCodecMaker.make

private[backend] trait AnkiGateway:
  def deckNames: IO[List[String]]
  def validateDestination(deckName: String): IO[Unit]
  def storeMediaFile(filename: String, bytes: Array[Byte]): IO[String]
  def addNote(
      deckName: String,
      fields: Map[String, String],
      tags: List[String]
  ): IO[Either[String, Long]]

private[backend] final class AnkiOutcomeUnknown(message: String, cause: Throwable = null)
    extends RuntimeException(message, cause)

final class ExternalClients(config: Config) extends AnkiGateway:
  import ExternalJson.*

  private val DuplicateNoteError = "cannot create note because it is a duplicate"

  private val client = HttpClient
    .newBuilder()
    .connectTimeout(Duration.ofSeconds(10))
    .followRedirects(HttpClient.Redirect.NEVER)
    .proxy(NoProxySelector)
    .build()

  private object NoProxySelector extends ProxySelector:
    override def select(uri: URI): java.util.List[Proxy] = java.util.List.of(Proxy.NO_PROXY)
    override def connectFailed(uri: URI, address: SocketAddress, error: IOException): Unit = ()

  override def deckNames: IO[List[String]] =
    val request = DeckNamesRequest("deckNames", 6)
    post[DeckNamesRequest, DeckNamesResponse](config.ankiUrl, request, "AnkiConnect").flatMap:
      response =>
        response.error.filter(_.trim.nonEmpty) match
          case Some(error) =>
            IO.raiseError(new IllegalStateException(s"AnkiConnect deckNames failed: $error"))
          case None =>
            IO.pure(response.result.getOrElse(Nil).filter(_.trim.nonEmpty).distinct.sorted)

  override def validateDestination(deckName: String): IO[Unit] =
    if deckName.trim.isEmpty then
      IO.raiseError(new IllegalArgumentException("Anki deck name must not be empty"))
    else
      deckNames.flatMap: available =>
        if available.contains(deckName) then validateNoteFields
        else
          IO.raiseError(
            new IllegalStateException(s"Anki deck $deckName does not exist")
          )

  private def validateNoteFields: IO[Unit] =
    val request = ModelFieldNamesRequest(
      "modelFieldNames",
      6,
      ModelFieldNamesParams(config.ankiModelName)
    )
    post[ModelFieldNamesRequest, ModelFieldNamesResponse](
      config.ankiUrl,
      request,
      "AnkiConnect"
    ).flatMap: response =>
      response.error.filter(_.trim.nonEmpty) match
        case Some(error) =>
          IO.raiseError(
            new IllegalStateException(s"AnkiConnect modelFieldNames failed: $error")
          )
        case None =>
          val required = Set("Text", "Back Extra", "Translation")
          val available = response.result.getOrElse(Nil).toSet
          val missing = required -- available
          if missing.nonEmpty then
            IO.raiseError(
              new IllegalStateException(
                s"Anki model ${config.ankiModelName} is missing fields: ${missing.toList.sorted.mkString(", ")}"
              )
            )
          else if response.result.flatMap(_.headOption).contains("Text") then validateTemplates
          else
            IO.raiseError(
              new IllegalStateException(
                s"Anki model ${config.ankiModelName} must use Text as its first field for duplicate detection"
              )
            )

  private def validateTemplates: IO[Unit] =
    val request = ModelTemplatesRequest(
      "modelTemplates",
      6,
      ModelFieldNamesParams(config.ankiModelName)
    )
    post[ModelTemplatesRequest, ModelTemplatesResponse](
      config.ankiUrl,
      request,
      "AnkiConnect"
    ).flatMap: response =>
      response.error.filter(_.trim.nonEmpty) match
        case Some(error) =>
          IO.raiseError(
            new IllegalStateException(s"AnkiConnect modelTemplates failed: $error")
          )
        case None =>
          val valid = response.result.toList
            .flatMap(_.values)
            .exists: template =>
              template.get("Front").exists(_.contains("{{cloze:Text}}")) &&
                template.get("Back").exists(_.contains("{{Translation}}"))
          if valid then IO.unit
          else
            IO.raiseError(
              new IllegalStateException(
                s"Anki model ${config.ankiModelName} must cloze Text and render Translation"
              )
            )

  override def storeMediaFile(filename: String, bytes: Array[Byte]): IO[String] =
    val request = StoreMediaRequest(
      "storeMediaFile",
      6,
      StoreMediaParams(filename, Base64.getEncoder.encodeToString(bytes))
    )
    post[StoreMediaRequest, StoreMediaResponse](config.ankiUrl, request, "AnkiConnect").flatMap:
      response =>
        response.error.filter(_.trim.nonEmpty) match
          case Some(error) =>
            IO.raiseError(new IllegalStateException(s"AnkiConnect storeMediaFile failed: $error"))
          case None =>
            response.result.filter(_.trim.nonEmpty) match
              case Some(storedFilename) => IO.pure(storedFilename)
              case None =>
                IO.raiseError(
                  new IllegalStateException("AnkiConnect storeMediaFile returned no filename")
                )

  override def addNote(
      deckName: String,
      fields: Map[String, String],
      tags: List[String]
  ): IO[Either[String, Long]] =
    val request = AddNoteRequest(
      "addNote",
      6,
      AddNoteParams(
        note(deckName, fields, tags)
      )
    )
    val submitted =
      post[AddNoteRequest, AddNoteResponse](config.ankiUrl, request, "AnkiConnect")
        .handleErrorWith: error =>
          IO.raiseError(
            new AnkiOutcomeUnknown(
              s"AnkiConnect addNote outcome is unknown: ${errorMessage(error)}",
              error
            )
          )
    submitted.flatMap:
      response =>
        response.error.filter(_.trim.nonEmpty) match
          case Some(error) if error.trim.equalsIgnoreCase(DuplicateNoteError) =>
            IO.pure(Left(error))
          case Some(error) =>
            IO.raiseError(new IllegalStateException(s"AnkiConnect addNote failed: $error"))
          case None =>
            response.result match
              case Some(noteId) => IO.pure(Right(noteId))
              case None =>
                IO.raiseError(
                  new AnkiOutcomeUnknown(
                    "AnkiConnect addNote outcome is unknown: response contained neither an error nor a note ID"
                  )
                )

  private def note(
      deckName: String,
      fields: Map[String, String],
      tags: List[String]
  ): AnkiNote =
    AnkiNote(
      deckName = deckName,
      modelName = config.ankiModelName,
      fields = fields,
      options = NoteOptions(allowDuplicate = false),
      tags = tags
    )

  private def post[A, B](uri: URI, value: A, service: String)(using
      JsonValueCodec[A],
      JsonValueCodec[B]
  ): IO[B] =
    IO.blocking:
      val body = writeToString(value)
      val request = HttpRequest
        .newBuilder(uri)
        .timeout(Duration.ofMinutes(2))
        .header("Content-Type", "application/json; charset=utf-8")
        .header("Accept", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
        .build()
      val response = client.send(
        request,
        HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)
      )
      if response.statusCode() < 200 || response.statusCode() >= 300 then
        val detail = remoteError(response.body())
        throw new IllegalStateException(
          s"$service request failed (${response.statusCode()}): $detail"
        )
      try readFromString[B](response.body())
      catch
        case error: Throwable =>
          throw new IllegalStateException(
            s"$service returned invalid JSON: ${errorMessage(error)}",
            error
          )

  private def remoteError(body: String): String =
    val parsed =
      try readFromString[RemoteError](body).error
      catch case _: Throwable => None
    concise(parsed.getOrElse(body))

  private def errorMessage(error: Throwable): String =
    concise(Option(error.getMessage).getOrElse(error.getClass.getSimpleName))

  private def concise(value: String): String =
    val singleLine = value.trim.replaceAll("\\s+", " ")
    if singleLine.isEmpty then "empty response" else singleLine.take(500)
