package com.github.pwharned.flashcards.shared.domain

final case class SelectionSpan(startUtf16: Int, endUtf16: Int):
  def validate(text: String): Either[String, SelectionSpan] =
    Either.cond(
      startUtf16 >= 0 && endUtf16 > startUtf16 && endUtf16 <= text.length &&
        !SelectionSpan.splitsSurrogatePair(text, startUtf16) &&
        !SelectionSpan.splitsSurrogatePair(text, endUtf16),
      this,
      s"selection [$startUtf16, $endUtf16) is not a valid boundary in text length ${text.length}"
    )

  def extract(text: String): Either[String, String] =
    validate(text).map(_ => text.substring(startUtf16, endUtf16))

object SelectionSpan:
  private def splitsSurrogatePair(text: String, index: Int): Boolean =
    index > 0 && index < text.length &&
      Character.isHighSurrogate(text.charAt(index - 1)) &&
      Character.isLowSurrogate(text.charAt(index))

final case class Enrichment(targetGloss: String, sentenceTranslation: String):
  def validate: Either[String, Enrichment] =
    Either.cond(
      targetGloss.trim.nonEmpty && sentenceTranslation.trim.nonEmpty,
      copy(
        targetGloss = targetGloss.trim,
        sentenceTranslation = sentenceTranslation.trim
      ),
      "target gloss and sentence translation must not be empty"
    )

final case class PreparedAudio(
    audioId: String,
    previewUrl: String,
    filename: String,
    contentType: String,
    sizeBytes: Long,
    sha256: String
):
  def validate: Either[String, PreparedAudio] =
    for
      _ <- Either.cond(audioId.trim.nonEmpty, (), "audio id must not be empty")
      _ <- Either.cond(previewUrl.startsWith("/prepared-audio/"), (), "invalid preview URL")
      _ <- Either.cond(filename.trim.endsWith(".mp3"), (), "prepared audio must be MP3")
      _ <- Either.cond(contentType == "audio/mpeg", (), "prepared audio must use audio/mpeg")
      _ <- Either.cond(sizeBytes > 0, (), "prepared audio must not be empty")
      _ <- Either.cond(sha256.matches("(?i)[0-9a-f]{64}"), (), "invalid prepared audio SHA-256")
    yield this

final case class CardDraft(
    language: String,
    sentence: String,
    selection: SelectionSpan,
    targetGloss: String,
    sentenceTranslation: String
):
  def validate: Either[String, CardDraft] =
    for
      _ <- Either.cond(language.nonEmpty, (), "language must not be empty")
      _ <- selection.validate(sentence)
      enrichment <- Enrichment(targetGloss, sentenceTranslation).validate
    yield copy(
      targetGloss = enrichment.targetGloss,
      sentenceTranslation = enrichment.sentenceTranslation
    )

  def target: Either[String, String] = selection.extract(sentence)

  def clozeText: Either[String, String] =
    CardDraft.clozeText(sentence, selection)

  def fields(soundFilename: String): Either[String, Map[String, String]] =
    for
      valid <- validate
      cloze <- valid.clozeText
      _ <- Either.cond(soundFilename.trim.nonEmpty, (), "sound filename must not be empty")
    yield Map(
      "Text" -> s"$cloze[sound:${soundFilename.trim}]",
      "Back Extra" -> "",
      "Translation" -> s"${CardDraft.escapeHtml(valid.targetGloss)} : ${CardDraft.escapeHtml(valid.sentenceTranslation)}"
    )

object CardDraft:
  def clozeText(sentence: String, selection: SelectionSpan): Either[String, String] =
    selection.validate(sentence).map { _ =>
      val before = escapeForAnki(sentence.substring(0, selection.startUtf16))
      val target = escapeForAnki(sentence.substring(selection.startUtf16, selection.endUtf16))
      val after = escapeForAnki(sentence.substring(selection.endUtf16))
      s"$before{{c1::$target}}$after"
    }

  private def escapeForAnki(value: String): String =
    escapeHtml(value)
      .replace("{", "&#123;")
      .replace("}", "&#125;")
      .replace(":", "&#58;")

  private def escapeHtml(value: String): String =
    value
      .replace("&", "&amp;")
      .replace("<", "&lt;")
      .replace(">", "&gt;")
      .replace("\"", "&quot;")
      .replace("'", "&#x27;")
