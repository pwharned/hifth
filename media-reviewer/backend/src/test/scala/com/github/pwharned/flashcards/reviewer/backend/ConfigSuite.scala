package com.github.pwharned.flashcards.reviewer.backend

import com.comcast.ip4s.{Host, Port}

import java.net.URI
import java.nio.file.Path

class ConfigSuite extends munit.FunSuite:
  test("defaults expose only the expected reviewer and Anki settings"):
    Config.parse(List("artifact.json")) match
      case Right(Some(config)) =>
        assertEquals(
          config.productElementNames.toList,
          List("artifactPath", "host", "port", "ankiUrl", "deckName", "ankiModelName")
        )
        assertEquals(config.artifactPath, Path.of("artifact.json"))
        assertEquals(config.host, Host.fromString("127.0.0.1").get)
        assertEquals(config.port, Port.fromInt(8766).get)
        assertEquals(config.ankiUrl, URI.create("http://127.0.0.1:8765"))
        assertEquals(config.deckName, "Default")
        assertEquals(config.ankiModelName, "Cloze")
      case result => fail(s"expected a parsed config, got $result")

  test("Anki URL must use a loopback host"):
    List("http://example.com:8765", "http://127.attacker.example:8765").foreach: url =>
      Config.parse(List("--anki-url", url, "artifact.json")) match
        case Left(error) => assertEquals(error, "--anki-url must use a loopback host")
        case result      => fail(s"expected --anki-url to reject $url, got $result")

  test("reviewer bind host must be loopback"):
    Config.parse(List("--host", "0.0.0.0", "artifact.json")) match
      case Left(error) => assertEquals(error, "--host must be 127.0.0.1, localhost, or ::1")
      case result      => fail(s"expected a rejected public bind host, got $result")

  test("removed options are unknown and absent from help"):
    List("--target-language", "--model", "--ollama-url").foreach: option =>
      List(List(option, "removed", "artifact.json"), List(s"$option=removed", "artifact.json"))
        .foreach: args =>
          assertEquals(Config.parse(args), Left(s"unknown option: $option"))
      assert(!Config.usage.contains(option), s"usage still contains $option")
