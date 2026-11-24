"""Resolve user-supplied complex / city names against values present in the database."""

from __future__ import annotations

import difflib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.exceptions import FilterValueNotResolvedError
from app.services.text import normalize_complex_name, normalize_text

FUZZY_CUTOFF = 0.8


@dataclass(frozen=True, slots=True)
class Resolution:
    query: str
    value: str  # display name stored in the DB
    key: str  # normalized key used for filtering
    exact: bool


def resolve_name(
    field: str,
    query: str,
    names: Iterable[str],
    normalize: Callable[[str], str] = normalize_complex_name,
) -> Resolution:
    """Exact normalized match, else a single close candidate, else FilterValueNotResolvedError.

    Ambiguous queries are never silently narrowed: if several names match, the user is asked.
    """
    by_key: dict[str, str] = {}
    for name in names:
        by_key.setdefault(normalize(name), name)
    key = normalize(query)
    if not key:
        raise FilterValueNotResolvedError(field, query, sorted(by_key.values()), ambiguous=False)
    if key in by_key:
        return Resolution(query=query, value=by_key[key], key=key, exact=True)

    candidates = {
        candidate
        for candidate in by_key
        if _token_prefix_match(key, candidate) or _token_prefix_match(candidate, key)
    }
    if not candidates:
        candidates = set(difflib.get_close_matches(key, list(by_key), n=5, cutoff=FUZZY_CUTOFF))

    if len(candidates) == 1:
        match = candidates.pop()
        return Resolution(query=query, value=by_key[match], key=match, exact=False)
    if candidates:
        shown = sorted(by_key[c] for c in candidates)
        raise FilterValueNotResolvedError(field, query, shown, ambiguous=True)
    # Nothing similar: say so plainly and list what exists.
    raise FilterValueNotResolvedError(field, query, sorted(by_key.values()), ambiguous=False)


def _token_prefix_match(short: str, long: str) -> bool:
    """'юкки' matches 'догма юкки'; 'парк' matches 'догма парк' and 'парк победы'."""
    short_tokens = short.split()
    long_tokens = long.split()
    if not short_tokens or len(short_tokens) > len(long_tokens):
        return False
    for start in range(len(long_tokens) - len(short_tokens) + 1):
        window = long_tokens[start : start + len(short_tokens)]
        if all(w == s for w, s in zip(window, short_tokens, strict=True)):
            return True
    return False


def resolve_complex(query: str, names: Iterable[str]) -> Resolution:
    return resolve_name("complex_name", query, names, normalize_complex_name)


def resolve_city(query: str, names: Iterable[str]) -> Resolution:
    return resolve_name("city", query, names, _normalize_city)


def _normalize_city(value: str) -> str:
    text = normalize_text(value)
    for prefix in ("город ", "г. ", "г "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip()
