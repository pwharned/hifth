from __future__ import annotations

import unicodedata


def normalize_greek(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value).casefold()
    return "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    ).replace("ς", "σ")


def normalize_latin(value: str) -> str:
    return unicodedata.normalize("NFD", value).casefold().replace("v", "u").replace("j", "i")


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, 1):
        current = [left_index]
        for right_index, right_character in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            ))
        previous = current
    return previous[-1]


def shared_prefix(left: str, right: str) -> int:
    return next(
        (index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]),
        min(len(left), len(right)),
    )


def safe_lemma_suggestions(target: str, suggestions: list[str], language: str = "greek") -> list[str]:
    normalize = normalize_greek if language == "greek" else normalize_latin
    wanted = normalize(target)
    ranked = []
    for suggestion in suggestions:
        normalized = normalize(suggestion)
        distance = edit_distance(wanted, normalized)
        prefix = shared_prefix(wanted, normalized)
        limit = 1 if len(wanted) < 6 else 2
        if distance <= limit and prefix >= max(4, min(len(wanted), len(normalized)) - 3):
            ranked.append((distance, -prefix, abs(len(wanted) - len(normalized)), suggestion))
    return [item[-1] for item in sorted(set(ranked))[:5]]
