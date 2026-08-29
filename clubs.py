#!/usr/bin/env python3
"""
Club-name matching, shared by sync.py and build.py.

Both scripts have to decide "is this fixture one of our top clubs?" — sync.py to
keep only the top clubs' games from a foreign league, build.py to highlight them.
They must answer identically, so the normalisation lives here and nowhere else.
"""
from __future__ import annotations

import re
import unicodedata

# ornamental tokens that carry no identity when they sit at either end of a name
_TRIM = {
    "fc", "cf", "ac", "as", "sc", "cd", "ud", "rc", "rcd", "afc", "sk", "fk",
    "ss", "ssc", "us", "cp", "sad", "bc", "calcio", "club", "de", "the",
    "football", "futbol", "fussball",
}


def club_key(name: str) -> str:
    """Reduce a club name to its comparison key."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    parts = s.split()
    while parts and parts[0] in _TRIM:
        parts.pop(0)
    while parts and parts[-1] in _TRIM:
        parts.pop()
    return " ".join(parts) or s


def top_index(cfg: dict) -> dict:
    """comparison key -> club record, for every listed club and all its aliases.

    Later entries never clobber earlier ones, so a display name always beats an
    alias if the two ever collide.
    """
    index: dict = {}
    for c in ((cfg.get("top_teams") or {}).get("clubs") or []):
        for n in [c["name"], *(c.get("aka") or [])]:
            index.setdefault(club_key(n), c)
    return index


def has_top_club(fixture: dict, index: dict) -> bool:
    """True when either side of a normalised fixture is on the top-club list."""
    return club_key(fixture["home"]) in index or club_key(fixture["away"]) in index
