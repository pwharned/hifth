from __future__ import annotations

import argparse
import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from .media import probe_duration_ms, sha256_file
from .models import BaseToken, MediaSource, ProjectManifest, TextSpan, Utterance


_TIMING_RE = re.compile(
    r"^(?P<start>\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}\s*-->\s*"
    r"(?P<end>\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$"
)
_ASS_OVERRIDE_RE = re.compile(r"\{\\[^}]+}")
_VTT_TIMESTAMP_TAG_RE = re.compile(r"<(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}>")


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    id: str
    start_ms: float
    end_ms: float
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("subtitle cue id must be a nonempty string")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("subtitle cue end must be later than its start")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("subtitle cue text must be nonempty")


class _SubtitleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self.parts.append(" ")


def _clean_text(lines: list[str]) -> str:
    extractor = _SubtitleTextExtractor()
    subtitle_text = _VTT_TIMESTAMP_TAG_RE.sub("", " ".join(line.strip() for line in lines))
    extractor.feed(subtitle_text)
    extractor.close()
    text = "".join(extractor.parts)
    text = _ASS_OVERRIDE_RE.sub("", text)
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", text).strip())


def _timestamp_ms(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"invalid subtitle timestamp: {value!r}")
    try:
        result = (int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000
    except ValueError as exc:
        raise ValueError(f"invalid subtitle timestamp: {value!r}") from exc
    if result < 0:
        raise ValueError(f"invalid subtitle timestamp: {value!r}")
    return result


def _parse_timing_line(line: str) -> tuple[float, float]:
    if not _TIMING_RE.match(line.strip()):
        raise ValueError(f"invalid subtitle timing line: {line!r}")
    start_text, end_with_settings = re.split(r"\s*-->\s*", line.strip(), maxsplit=1)
    end_text = end_with_settings.split()[0]
    start_ms = _timestamp_ms(start_text)
    end_ms = _timestamp_ms(end_text)
    if end_ms <= start_ms:
        raise ValueError(f"subtitle cue end must be later than its start: {line!r}")
    return start_ms, end_ms


def parse_subtitles(payload: str) -> tuple[SubtitleCue, ...]:
    if not isinstance(payload, str):
        raise ValueError("subtitle payload must be a string")
    payload = payload.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = payload.split("\n")
    if lines and lines[0].strip().startswith("WEBVTT"):
        lines = lines[1:]
    lines = [line for line in lines if not line.strip().startswith("X-TIMESTAMP-MAP")]

    cues: list[SubtitleCue] = []
    block: list[str] = []

    def consume(current: list[str]) -> None:
        nonempty = [line for line in current if line.strip()]
        if not nonempty:
            return
        timing_index = next(
            (index for index, line in enumerate(nonempty) if "-->" in line),
            None,
        )
        if timing_index is None:
            if nonempty[0].startswith(("NOTE", "STYLE", "REGION", "X-TIMESTAMP-MAP")):
                return
            raise ValueError(f"subtitle block has no timing line: {nonempty[0]!r}")
        start_ms, end_ms = _parse_timing_line(nonempty[timing_index])
        text = _clean_text(nonempty[timing_index + 1 :])
        if not text:
            return
        cue_label = nonempty[timing_index - 1] if timing_index > 0 else str(len(cues) + 1)
        cues.append(SubtitleCue(cue_label, start_ms, end_ms, text))

    for line in lines:
        if line.strip():
            block.append(line)
        elif block:
            consume(block)
            block = []
    consume(block)
    if not cues:
        raise ValueError("subtitle payload contains no cues")
    return tuple(cues)


def tokenize_base_units(text: str) -> tuple[BaseToken, ...]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a nonempty string")
    tokens: list[BaseToken] = []
    index = 0

    def is_word_character(character: str) -> bool:
        return character == "_" or unicodedata.category(character)[0] in {"L", "M", "N"}

    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        start = index
        if is_word_character(text[index]):
            index += 1
            while index < len(text):
                character = text[index]
                if is_word_character(character):
                    index += 1
                    continue
                if (
                    character in {"'", "’", "-"}
                    and index + 1 < len(text)
                    and is_word_character(text[index + 1])
                ):
                    index += 1
                    continue
                break
        else:
            index += 1
        tokens.append(BaseToken(text[start:index], TextSpan(start, index)))
    return tuple(tokens)


def manifest_from_subtitles(
    media_path: str | Path,
    subtitle_path: str | Path,
    *,
    language: str,
    title: str | None = None,
    source_url: str | None = None,
    stored_media_path: str | None = None,
) -> ProjectManifest:
    media_file = Path(media_path)
    subtitles_file = Path(subtitle_path)
    if not media_file.is_file():
        raise FileNotFoundError(f"Media file not found: {media_file}")
    if not subtitles_file.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {subtitles_file}")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("language must be a nonempty string")

    checksum = sha256_file(media_file)
    media_id = f"media-{checksum[:24]}"
    cues = parse_subtitles(subtitles_file.read_text(encoding="utf-8-sig"))
    duration_ms = probe_duration_ms(media_file)
    utterances = []
    for cue in cues:
        if cue.start_ms >= duration_ms:
            continue
        cue_end_ms = min(cue.end_ms, duration_ms)
        identity = f"{media_id}\0{cue.start_ms:.3f}\0{cue_end_ms:.3f}\0{cue.text}"
        utterance_id = f"utterance-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
        utterances.append(
            Utterance(
                id=utterance_id,
                media_id=media_id,
                text=cue.text,
                start_ms=cue.start_ms,
                end_ms=cue_end_ms,
                tokens=tokenize_base_units(cue.text),
                provenance=(f"subtitle:{subtitles_file.suffix.lower().lstrip('.')}",),
            )
        )
    if not utterances:
        raise ValueError("no subtitle cues overlap the media duration")

    project_title = title or media_file.stem
    project_identity = f"{media_id}\0{language}\0{project_title}"
    project_id = f"project-{hashlib.sha256(project_identity.encode('utf-8')).hexdigest()[:24]}"
    source = MediaSource(
        id=media_id,
        path=stored_media_path or str(media_file.resolve()),
        language=language,
        title=project_title,
        source_url=source_url,
        checksum_sha256=checksum,
        duration_ms=duration_ms,
    )
    return ProjectManifest(
        id=project_id,
        title=project_title,
        language=language,
        media=(source,),
        utterances=tuple(utterances),
        learning_units=(),
        cards=(),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a reviewable flashcard project from local media and SRT/VTT subtitles."
    )
    parser.add_argument("media", type=Path)
    parser.add_argument("subtitles", type=Path)
    parser.add_argument("--language", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title")
    parser.add_argument("--source-url")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stored_path = os.path.relpath(args.media.resolve(), args.out.resolve().parent)
    except ValueError:
        stored_path = str(args.media.resolve())
    manifest = manifest_from_subtitles(
        args.media,
        args.subtitles,
        language=args.language,
        title=args.title,
        source_url=args.source_url,
        stored_media_path=stored_path,
    )
    args.out.write_text(manifest.to_json(indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(manifest.utterances)} subtitle cue(s) -> {args.out}")


if __name__ == "__main__":
    main()
