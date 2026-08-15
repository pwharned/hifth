package com.github.pwharned.hifth.frontend.islands
import com.raquo.laminar.api.L.*
import org.scalajs.dom
import scala.scalajs.js.annotation.JSExportTopLevel
import com.github.pwharned.hifth.frontend.{AppBus, AppPage}
object AppIsland:
  @JSExportTopLevel("mountApp", moduleID = "app")
  def mount(el: dom.Element): Unit =
    val island = div(
      height("100vh"),
      overflow("hidden"),
      child <-- AppBus.currentPage.signal.map:
        case AppPage.Home =>
          div(
            onMountCallback { ctx =>
              HomeIsland.mountInternal(ctx.thisNode.ref)
            }
          )
        case AppPage.Player(qhId) =>
          div(
            onMountCallback { ctx =>
              PlayerIsland.mount(ctx.thisNode.ref, qhId)
            }
          )
        case AppPage.SessionComplete(qhId, result) =>
          div(
            onMountCallback { ctx =>
              SessionCompleteIsland.mountInternal(
                ctx.thisNode.ref,
                qhId,
                result
              )
            }
          )
    )
    render(el, island)
