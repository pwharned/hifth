package com.github.pwharned.hifth.frontend
import com.raquo.laminar.api.L.*
import com.github.pwharned.hifth.shared.protocol.*
import com.github.pwharned.hifth.shared.domain.*
enum ConnectionState:
  case Connecting
  case Connected
  case Reconnecting
object AppBus:
  val outgoing: EventBus[ClientMessage] = new EventBus
  val incoming: EventBus[ServerMessage] = new EventBus
  val connectionState: Var[ConnectionState] = Var(ConnectionState.Connecting)
  val srsEntries: Var[Map[Int, SRSEntry]] = Var(Map.empty)
  val sessionLogs: Var[List[SessionLog]] = Var(List.empty)
  val streakState: Var[Option[StreakState]] = Var(None)
  val currentPage: Var[AppPage] = Var(AppPage.Home)
  def init(): Unit =
    incoming.events.foreach {
      case ServerMessage.HomeStateLoaded(entries, logs, streak) =>
        srsEntries.set(entries.map(e => e.quarterHizbId -> e).toMap)
        sessionLogs.set(logs)
        streakState.set(Some(streak))
      case ServerMessage.SessionAccepted(updatedEntry, newLog, streak) =>
        srsEntries.update(_ + (updatedEntry.quarterHizbId -> updatedEntry))
        sessionLogs.update(newLog :: _)
        streakState.set(Some(streak))
      case _ => ()
    }(using unsafeWindowOwner)
