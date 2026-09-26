package com.github.pwharned.hifth.frontend.islands
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import scala.scalajs.js.annotation.JSExportTopLevel
import com.github.pwharned.hifth.frontend.{AppBus, ConnectionState}
object ConnectionIsland:
  @JSExportTopLevel("mountConnection", moduleID = "connection")
  def mount(el: dom.Element): Unit =
    val statusText = AppBus.connectionState.signal.map:
      case ConnectionState.Connecting   => "Connecting..."
      case ConnectionState.Connected    => "Connected"
      case ConnectionState.Reconnecting => "Reconnecting..."
    val statusColor = AppBus.connectionState.signal.map:
      case ConnectionState.Connecting   => "orange"
      case ConnectionState.Connected    => "green"
      case ConnectionState.Reconnecting => "red"
    render(
      el,
      div(
        span(
          child.text <-- statusText,
          color <-- statusColor
        )
      )
    )
