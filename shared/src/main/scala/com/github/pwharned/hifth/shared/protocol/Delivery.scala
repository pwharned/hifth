package com.github.pwharned.hifth.shared.protocol
sealed trait Delivery[+A]
object Delivery:
  case class Unicast[A](msg: A) extends Delivery[A]
  case class Broadcast[A](msg: A) extends Delivery[A]
