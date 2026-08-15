package com.github.pwharned.hifth.backend.modules
import cats.effect.{IO, Resource}
import com.github.pwharned.hifth.backend.MessageHandler
import com.github.pwharned.hifth.backend.services.*
import com.github.pwharned.hifth.shared.protocol.*
class ServiceModule(
    srs: SRSService
) extends MessageHandler[IO, ClientMessage, ServerMessage]:
  def handle(msg: ClientMessage): IO[Delivery[ServerMessage]] =
    srs.handle(msg)
object ServiceModule:
  def make(db: DatabaseModule): Resource[IO, ServiceModule] =
    for srs <- SRSService.make(db.srsRepository, db.sessionLogRepository)
    yield ServiceModule(srs)
