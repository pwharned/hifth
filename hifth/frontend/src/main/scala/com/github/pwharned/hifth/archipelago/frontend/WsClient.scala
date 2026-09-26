package com.github.pwharned.hifth.frontend
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import scala.scalajs.js
import com.github.pwharned.hifth.shared.protocol.*
import com.github.plokhotnyuk.jsoniter_scala.core.*
object WsClient:
  private val buffer = collection.mutable.Queue[ClientMessage]()
  private var currentWs = Option.empty[dom.WebSocket]
  private var retryCount = 0
  private val maxBackoffMs = 30000
  private val baseBackoffMs = 1000
  private def backoffMs: Int =
    math.min(baseBackoffMs * math.pow(2, retryCount).toInt, maxBackoffMs)
  private def wsUrl: String =
    s"ws://${dom.window.location.host}/ws"
  private def flushBuffer(ws: dom.WebSocket): Unit =
    while buffer.nonEmpty && ws.readyState == dom.WebSocket.OPEN do
      ws.send(writeToString(buffer.dequeue()))
  private def tryConnect(): Unit =
    val ws = new dom.WebSocket(wsUrl)
    currentWs = Some(ws)
    ws.onopen = _ =>
      retryCount = 0
      AppBus.connectionState.set(ConnectionState.Connected)
      dom.document
        .getElementById("app-container")
        .classList
        .remove("disconnected")
      flushBuffer(ws)
      // Request full home state on every fresh connection
      AppBus.outgoing.emit(ClientMessage.RequestHomeState)
    ws.onmessage = (event: dom.MessageEvent) =>
      val msg = readFromString[ServerMessage](event.data.toString)
      AppBus.incoming.emit(msg)
    ws.onerror = (event: dom.Event) => dom.console.error(s"WS error: $event")
    ws.onclose = (event: dom.CloseEvent) =>
      currentWs = None
      AppBus.connectionState.set(ConnectionState.Reconnecting)
      dom.document.getElementById("app-container").classList.add("disconnected")
      val delay = backoffMs
      retryCount += 1
      dom.console.warn(
        s"WS closed, reconnecting in ${delay}ms (attempt $retryCount)"
      )
      js.timers.setTimeout(delay)(tryConnect())
  def connect(): Unit =
    AppBus.outgoing.events.foreach { msg =>
      currentWs match
        case Some(ws) if ws.readyState == dom.WebSocket.OPEN =>
          ws.send(writeToString(msg))
        case _ =>
          buffer.enqueue(msg)
    }(using unsafeWindowOwner)
    tryConnect()
