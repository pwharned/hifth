from __future__ import annotations

import html

from .models import TextSpan


def _escape_anki_text(value: str) -> str:
    # Anki parses cloze markers before rendering HTML entities. Encoding these
    # delimiters keeps literal subtitle text from creating or truncating clozes.
    return (
        html.escape(value)
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace(":", "&#58;")
    )


def build_cloze(
    text: str,
    span: TextSpan,
    cloze_number: int = 1,
    hint: str | None = None,
    escape_html: bool = False,
) -> str:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not isinstance(span, TextSpan):
        raise ValueError("span must be a TextSpan")
    if type(cloze_number) is not int or cloze_number < 1:
        raise ValueError("cloze_number must be a positive integer")
    if hint is not None and (not isinstance(hint, str) or not hint.strip()):
        raise ValueError("hint must be a nonempty string when provided")
    if type(escape_html) is not bool:
        raise ValueError("escape_html must be a boolean")

    target = span.extract(text)
    before = text[: span.start_char]
    after = text[span.end_char :]
    if escape_html:
        before = _escape_anki_text(before)
        target = _escape_anki_text(target)
        after = _escape_anki_text(after)
        if hint is not None:
            hint = _escape_anki_text(hint)

    hint_part = "" if hint is None else f"::{hint}"
    return f"{before}{{{{c{cloze_number}::{target}{hint_part}}}}}{after}"
