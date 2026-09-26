package com.github.pwharned.flashcards.reviewer.frontend

import com.github.plokhotnyuk.jsoniter_scala.core.{readFromString, writeToString}
import com.github.pwharned.flashcards.shared.protocol.{ClientMessage, ServerMessage}
import com.raquo.laminar.api.L.unsafeWindowOwner
import org.scalajs.dom
import scala.scalajs.js
import scala.util.control.NonFatal

object WsClient:
  private var currentSocket = Option.empty[dom.WebSocket]
  private var retryCount = 0
  private var started = false

  private val baseBackoffMs = 1000
  private val maxBackoffMs = 30000

  def connect(): Unit =
    if !started then
      started = true
      AppBus.connectionState.set(ConnectionState.Connecting)
      AppBus.outgoing.events.foreach(send)(using unsafeWindowOwner)
      tryConnect()

  private def websocketUrl: String =
    val scheme = if dom.window.location.protocol == "https:" then "wss" else "ws"
    s"$scheme://${dom.window.location.host}/ws"

  private def send(message: ClientMessage): Unit =
    currentSocket match
      case Some(socket) if socket.readyState == dom.WebSocket.OPEN =>
        try socket.send(writeToString[ClientMessage](message))
        catch
          case NonFatal(error) =>
            AppBus.transportSendFailed(
              message,
              errorMessage("The request could not be sent", error)
            )
            retire(socket)
      case _ =>
        AppBus.transportSendFailed(
          message,
          "The reviewer is disconnected; wait for it to reconnect."
        )

  private def tryConnect(): Unit =
    if currentSocket.nonEmpty then ()
    else
      try
        val socket = new dom.WebSocket(websocketUrl)
        currentSocket = Some(socket)

        socket.onopen = (_: dom.Event) =>
          if currentSocket.contains(socket) then
            retryCount = 0
            AppBus.connectionState.set(ConnectionState.Connected)
            if socket.readyState == dom.WebSocket.OPEN then AppBus.requestSource()

        socket.onmessage = (event: dom.MessageEvent) =>
          if currentSocket.contains(socket) then
            try AppBus.incoming.emit(readFromString[ServerMessage](event.data.toString))
            catch
              case NonFatal(error) =>
                AppBus.reportTransportError(errorMessage("Invalid server message", error))
                retire(socket)

        socket.onerror = (_: dom.Event) =>
          if currentSocket.contains(socket) then
            dom.console.error("Media reviewer WebSocket error")

        socket.onclose = (_: dom.CloseEvent) =>
          if currentSocket.contains(socket) then
            currentSocket = None
            AppBus.transportDisconnected()
            scheduleReconnect()
      catch
        case NonFatal(error) =>
          AppBus.reportTransportError(errorMessage("Unable to open WebSocket", error))
          currentSocket = None
          scheduleReconnect()

  private def retire(socket: dom.WebSocket): Unit =
    if currentSocket.contains(socket) then
      currentSocket = None
      try socket.close()
      catch case NonFatal(_) => ()
      AppBus.transportDisconnected()
      scheduleReconnect()

  private def scheduleReconnect(): Unit =
    AppBus.connectionState.set(ConnectionState.Reconnecting)
    val exponent = math.min(retryCount, 5)
    val delay = math.min(baseBackoffMs * math.pow(2, exponent).toInt, maxBackoffMs)
    retryCount += 1
    js.timers.setTimeout(delay)(refreshSessionAndConnect())

  private def refreshSessionAndConnect(): Unit =
    val request = new dom.XMLHttpRequest()
    request.open("GET", s"/?session-refresh=${js.Date.now().toLong}", async = true)
    request.withCredentials = true
    request.timeout = 10000
    request.setRequestHeader("Cache-Control", "no-cache")
    request.onloadend = (_: dom.ProgressEvent) => tryConnect()
    try request.send()
    catch case NonFatal(_) => tryConnect()

  private def errorMessage(context: String, error: Throwable): String =
    Option(error.getMessage).filter(_.nonEmpty).fold(context)(message => s"$context: $message")
