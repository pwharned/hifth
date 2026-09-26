package com.github.pwharned.hifth.frontend.player

enum LoadState:
  case Loading
  case Failed(msg: String)
  case Ready(segments: Vector[PlayerSegment])
// ── Session State ────────────────────────────────────────────────────────────
