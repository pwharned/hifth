package com.github.pwharned.hifth.backend

import com.github.pwharned.hifth.shared.protocol.Delivery

trait MessageHandler[F[_], In, Out]:
  def handle(in: In): F[Delivery[Out]]
