# Media reviewer

The repository root is the SBT build for this application. Its Scala layout
matches Archipelago:

```text
media-reviewer/
  shared/    # JVM/Scala.js domain and WebSocket protocol
  frontend/  # Laminar UI and browser transport
  backend/   # HTTP/WebSocket server, translation/TTS, and AnkiConnect
  schema/    # Python-to-Scala media artifact contract
```

Run it from the repository root:

```bash
sbt "backend/run projects/movie.json"
```

Translation intentionally follows the existing Clausula implementation rather
than a supported Google API. It posts `MkEWBc` requests to Google Translate's
`batchexecute` endpoint, retries a 400 response with the returned XSRF token,
and uses the undocumented `translate_tts` endpoint for whole-sentence audio.
Persian (`fa`) follows Clausula's Microsoft Edge consumer-speech WebSocket path.
That adapter is pinned to the `edge-tts` 7.2.8 protocol profile, including
4096-byte escaped-text turns and 403 clock-skew recovery. These private endpoints
can change without notice.
