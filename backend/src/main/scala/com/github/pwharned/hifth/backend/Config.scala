package com.github.pwharned.hifth.backend
import cats.effect.IO
enum AssetMode:
  case Dev
  case Prod
case class Config(
    host: String,
    port: Int,
    assetMode: AssetMode
)
object Config:
  def load: IO[Config] = IO:
    val host = sys.env.getOrElse("HOST", "0.0.0.0")
    val port = sys.env.getOrElse("PORT", "8080").toInt
    val assetMode = sys.env.getOrElse("MODE", "PROD").toUpperCase match
      case "DEV" => AssetMode.Dev
      case _     => AssetMode.Prod
    Config(host, port, assetMode)
