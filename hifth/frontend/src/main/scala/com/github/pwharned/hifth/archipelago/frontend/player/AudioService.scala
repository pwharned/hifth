package com.github.pwharned.hifth.frontend.player
import org.scalajs.dom
import org.scalajs.dom.HTMLAudioElement
import scala.scalajs.js
import com.raquo.airstream.state.Var
import scala.concurrent.Future
import scala.concurrent.Promise
import scala.util.Failure
import scala.util.Success
import scala.concurrent.ExecutionContext.Implicits.global

/** Owns the raw HTMLAudioElement and all volume/playback side-effects. */
trait AudioService:
  def play(): Unit
  def pause(): Unit
  def currentTimeMs: Double
  def applyTime(seekMs: Double): Unit
  def load(): Unit

  /** Called every RAF tick with the active-word mask state */
  def applyVolume(wordIsMasked: Boolean): Unit
  def dispose(): Unit
object AudioService:
  // Volume levels - easy to tune in one place
  val NormalVolume: Double = 1.0
  val MutedVolume: Double = 1.0f // audible but clearly quieter
  final class Live(audioUrl: String, startTimeMs: Double) extends AudioService:
    // val url = "audio" + audioUrl.split("/").last.split("\\.").head
    //
    private var currentTime = Var(0.0)
    val ready = Promise[Unit]()
    println(ready.isCompleted)
    private var el: HTMLAudioElement =
      dom.document.getElementById(audioUrl).asInstanceOf[HTMLAudioElement]
    def load(): Unit = {
      if (el == null) {
        println(s"Loading audio $audioUrl")
        el = dom.document
          .createElement("audio")
          .asInstanceOf[HTMLAudioElement]

        el.id = audioUrl
        el.src = audioUrl
        el.style.display = "none"
        el.addEventListener(
          "loadedmetadata",
          ((e: dom.Event) => {

            println(
              s"Audio was not already loaded, current time is ${el.currentTime}"
            )

            val targetTime = (startTimeMs / 1000.0)
            println(s"Target time is ${targetTime}")

            el.currentTime = targetTime
            el.addEventListener(
              "seeked",
              ((e: dom.Event) => {
                ready.trySuccess(())
                println(ready.isCompleted) // resolve HERE instead
                println(s"Current time  after seek: ${el.currentTime}")
              }): js.Function1[dom.Event, Unit]
            )

          }): js.Function1[dom.Event, Unit]
        )
        dom.document.body.appendChild(el)
      } else {
        println(s"Audio was already loaded, current time is ${el.currentTime}")
        val targetTime = (startTimeMs / 1000.0)
        println(s"Target time is ${targetTime}")
        el.currentTime = targetTime
        println(s"After setting target time: ${el.currentTime}")
      }

    }
    def applyTime(seekMs: Double): Unit = el.currentTime = seekMs / 1000.0
    def play(): Unit = {

      ready.future.onComplete { x =>
        x match
          case Failure(exception) => {
            println(exception.getMessage())

          }
          case Success(value) => {
            val targetTime = (startTimeMs / 1000.0)
            println(s"Target time is ${targetTime}")
            if el.currentTime < targetTime then el.currentTime = targetTime
            else ()
            println(s"After setting target time: ${el.currentTime}")
            el.play()
          }

      }

    }
    def pause(): Unit = el.pause()
    def currentTimeMs: Double = {
      ready.future.onComplete { x =>
        x match
          case Failure(exception) => currentTime.set(0.0)
          case Success(value)     => currentTime.set(el.currentTime * 1000.0)
      }
      currentTime.now()
    }
    def applyVolume(wordIsMasked: Boolean): Unit = {
      val target = if wordIsMasked then MutedVolume else NormalVolume
      // Smooth it slightly so it doesn't click
      // el.volume = target
    }
    def dispose(): Unit =
      el.pause()
      // el.src = ""
