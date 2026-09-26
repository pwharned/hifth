package com.github.pwharned.hifth.backend
import cats.effect.{IO, IOApp, ExitCode, Resource}
import com.github.pwharned.hifth.backend.modules._
object Main extends IOApp:
  def run(args: List[String]): IO[ExitCode] =
    (for
      config <- Resource.eval(Config.load)
      db <- DatabaseModule.make
      services <- ServiceModule.make(db)
      ws <- WsModule.make(services)
      _ <- ServerModule.make(config, ws)
    yield ())
      .use(_ => IO.never)
      .as(ExitCode.Success)
