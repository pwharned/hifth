package com.github.pwharned.hifth.backend.modules
import cats.effect.{IO, Resource}
import com.github.pwharned.hifth.backend.services.*
class DatabaseModule(
    val srsRepository: SRSRepository[IO],
    val sessionLogRepository: SessionLogRepository[IO]
)
object DatabaseModule:
  def make: Resource[IO, DatabaseModule] =
    for
      srs <- InMemorySRSRepository.make
      logs <- InMemorySessionLogRepository.make
    yield DatabaseModule(srs, logs)
