package com.github.pwharned.hifth.frontend
import com.raquo.laminar.api.L.*
// Custom CSS style properties not included in Laminar's built-in DSL.
// Import this object wherever these properties are needed.
object Styles:
  val gridTemplateColumns: StyleProp[String] = styleProp(
    "grid-template-columns"
  )
  val gridColumn: StyleProp[String] = styleProp("grid-column")
  val fontVariantNumeric: StyleProp[String] = styleProp("font-variant-numeric")
