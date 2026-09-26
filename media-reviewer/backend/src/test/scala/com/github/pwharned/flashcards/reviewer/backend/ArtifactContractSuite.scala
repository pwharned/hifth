package com.github.pwharned.flashcards.reviewer.backend

import com.github.plokhotnyuk.jsoniter_scala.core.readFromArray
import com.github.pwharned.flashcards.shared.domain.MediaArtifact

class ArtifactContractSuite extends munit.FunSuite:
  test("Python media artifact fixture decodes and validates on the JVM"):
    val stream = Option(getClass.getResourceAsStream("/media-artifact-v1.json"))
      .getOrElse(fail("missing media-artifact-v1.json"))
    val bytes = try stream.readAllBytes() finally stream.close()
    val artifact = readFromArray[MediaArtifact](bytes)
    assertEquals(artifact.validate, Right(artifact))
    assertEquals(artifact.utterances.head.text, "Tôi là học sinh.")
