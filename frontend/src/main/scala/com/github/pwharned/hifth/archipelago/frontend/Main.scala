package com.github.pwharned.hifth.frontend
import scala.scalajs.js.annotation.JSExportTopLevel
object Main:
  @JSExportTopLevel("init", moduleID = "main")
  def init(): Unit =
    AppBus.init()
    WsClient.connect()
