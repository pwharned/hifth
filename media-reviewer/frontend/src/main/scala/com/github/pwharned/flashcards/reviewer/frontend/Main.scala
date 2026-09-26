package com.github.pwharned.flashcards.reviewer.frontend

import org.scalajs.dom
import scala.scalajs.js.annotation.JSExportTopLevel

object Main:
  @JSExportTopLevel("init", moduleID = "main")
  def init(): Unit =
    ReviewerStyles.install()
    AppBus.init()
    ReviewerApp.mount(mountElement())
    WsClient.connect()

  private def mountElement(): dom.Element =
    val ids = List("media-reviewer", "app-mount", "app", "app-container")
    ids.iterator
      .map(dom.document.getElementById)
      .find(_ != null)
      .getOrElse {
        val element = dom.document.createElement("div")
        element.id = "media-reviewer"
        dom.document.body.appendChild(element)
        element
      }
