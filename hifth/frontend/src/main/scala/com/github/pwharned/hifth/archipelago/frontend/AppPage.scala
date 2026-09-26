package com.github.pwharned.hifth.frontend
import com.github.pwharned.hifth.shared.domain.SessionResult
enum AppPage:
  case Home
  case Player(quarterHizbId: Int)
  case SessionComplete(quarterHizbId: Int, result: SessionResult)
