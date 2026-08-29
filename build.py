#!/usr/bin/env python3
"""
Inject data/fixtures.json into template.html and write calendar.html.

Run:  python3 build.py            # build from the real synced data
      python3 build.py --demo     # build from synthetic data, for layout checks only
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clubs import club_key, top_index

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "calendar.html"


def demo_payload(cfg: dict) -> dict:
    """Synthetic fixtures so the layout can be checked without burning API quota.
    This is NOT real data and is never used by the daily sync."""
    rng = random.Random(7)
    squads = {
        "epl":  ["Arsenal","Liverpool","Man City","Chelsea","Spurs","Newcastle","Aston Villa","Brighton",
                 "Man United","West Ham","Everton","Fulham","Crystal Palace","Brentford","Wolves","Bournemouth"],
        "liga": ["Real Madrid","Barcelona","Atletico Madrid","Athletic Club","Real Sociedad","Villarreal",
                 "Real Betis","Sevilla","Valencia","Girona","Osasuna","Celta Vigo","Mallorca","Rayo Vallecano"],
        "ucl":  ["Real Madrid","Man City","Bayern Munich","Arsenal","Inter","PSG","Liverpool","Barcelona",
                 "Atletico Madrid","Dortmund","Milan","Juventus","Benfica","PSV","Feyenoord","Celtic"],
        "uel":  ["Roma","Ajax","Rangers","Porto","Lyon","Real Sociedad","Fenerbahce","Tottenham"],
        "uecl": ["Chelsea","Fiorentina","Real Betis","Legia Warsaw","Rapid Vienna","Djurgarden"],
        "fac":  ["Arsenal","Liverpool","Man City","Chelsea","Leeds","Sunderland","Coventry","Plymouth"],
        "efl":  ["Newcastle","Liverpool","Chelsea","Arsenal","Southampton","Stoke","Preston","Wycombe"],
        "cdr":  ["Real Madrid","Barcelona","Atletico Madrid","Mallorca","Eibar","Cadiz","Levante","Elche"],
        "sce":  ["Real Madrid","Barcelona","Athletic Club","Atletico Madrid"],
    }
    weeks = {"epl": 1, "liga": 1, "ucl": 3, "uel": 3, "uecl": 3, "fac": 6, "efl": 5, "cdr": 5, "sce": 26}

    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    fixtures, fid = [], 900000
    for comp in cfg["competitions"]:
        key = comp["key"]
        # comps added to config.json without a demo squad still get plausible
        # filler, so --demo keeps working as a layout check for the whole rail
        teams = squads.get(key) or squads["ucl"]
        every = weeks.get(key, 3 if comp["tier"] != "league" else 1)
        d = start + timedelta(days=rng.randint(0, 6))
        rnd = 1
        while d < datetime(2027, 5, 24, tzinfo=timezone.utc):
            pool = teams[:]
            rng.shuffle(pool)
            for i in range(0, min(len(pool) - 1, 8 if key in ("epl", "liga") else 4), 2):
                fid += 1
                ko = d.replace(hour=rng.choice([12, 14, 16, 19, 20]), minute=rng.choice([0, 30]))
                played = ko < datetime(2026, 8, 19, tzinfo=timezone.utc)
                fixtures.append({
                    "id": fid, "comp": key,
                    "utc": ko.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": int(ko.timestamp()),
                    "status": "FT" if played else "NS",
                    "status_long": "Match Finished" if played else "Not Started",
                    "round": f"Regular Season - {rnd}" if comp["tier"] == "league" else f"Round {rnd}",
                    "home": pool[i], "away": pool[i + 1],
                    "venue": f"{pool[i]} Stadium",
                    "gh": rng.randint(0, 4) if played else None,
                    "ga": rng.randint(0, 3) if played else None,
                    "tbd": False, "disrupted": False, "finished": played,
                })
            d += timedelta(days=7 * every)
            rnd += 1

    fixtures.sort(key=lambda f: f["ts"])
    # a couple of synthetic reschedules so the changes panel has something to show
    changes = []
    for f in [f for f in fixtures if not f["finished"]][40:43]:
        old = datetime.fromtimestamp(f["ts"], tz=timezone.utc) - timedelta(days=2)
        changes.append({
            "detected": "2026-08-18", "kind": "moved", "id": f["id"], "comp": f["comp"],
            "home": f["home"], "away": f["away"],
            "from_utc": old.strftime("%Y-%m-%dT%H:%M:%SZ"), "to_utc": f["utc"], "round": f["round"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": cfg["season"], "season_label": cfg["season_label"],
        "competitions": cfg["competitions"], "fixtures": fixtures,
        "breaks": cfg.get("breaks", []), "changes": changes,
        "counts": {}, "failures": [], "demo": True,
    }



# ---------------------------------------------------------------------------
# Top-club highlighting
#
# The feed names clubs however it likes ("AS Monaco", "Bayern Munchen"), so every
# name is reduced to a comparison key before it is looked up: accents flattened,
# punctuation dropped, and the decorative bits clubs put on the front or back of
# their name ("FC", "AC", "CF", "Club") trimmed off. Matching is then EXACT on
# that key — no fuzzy contains, which is what would quietly turn "FC Inter Turku"
# into Inter Milan. Names the key misses are reported at build time instead.
# ---------------------------------------------------------------------------

def stage_of(round_name: str, table: dict):
    """(bonus, label) for a knockout round. Checked longest-name first so
    'Semi-finals' is never read as a final."""
    r = str(round_name or "").lower()
    for needle, label in (("semi", "Semi-final"), ("quarter", "Quarter-final"),
                          ("round of 16", "Round of 16"), ("final", "Final")):
        if needle in r:
            return table.get("semi" if needle == "semi" else
                             "quarter" if needle == "quarter" else
                             "round of 16" if needle == "round of 16" else "final", 0), label
    return 0, ""


def annotate_top(payload: dict, cfg: dict) -> dict:
    """Tag every fixture with how much of a big match it is.

    hot  2 = both clubs are on the list (a big match)
         1 = one club is on the list
         0 = neither
    heat     ranking score: club tiers + derby + knockout stage
    tag      'El Clasico', 'Manchester derby', 'Final' — when there is one
    ht/at    tier of the home / away club (1 elite, 2 big, 0 not listed)
    """
    top = cfg.get("top_teams") or {}
    clubs = top.get("clubs") or []
    stage = top.get("stage_bonus") or {}

    index = top_index(cfg)

    derby = {}
    for r in top.get("rivalries") or []:
        derby[frozenset((club_key(r["a"]), club_key(r["b"])))] = r["label"]

    points = {1: 3, 2: 2}
    seen = set()
    counts = {0: 0, 1: 0, 2: 0}

    for f in payload.get("fixtures", []):
        kh, ka = club_key(f["home"]), club_key(f["away"])
        seen.add(kh)
        seen.add(ka)
        h, a = index.get(kh), index.get(ka)

        f["ht"] = h["tier"] if h else 0
        f["at"] = a["tier"] if a else 0
        f["hot"] = (1 if h else 0) + (1 if a else 0)

        heat = points.get(f["ht"], 0) + points.get(f["at"], 0)
        tag = derby.get(frozenset((kh, ka)))
        if tag:
            heat += 3
        sb, slabel = stage_of(f.get("round"), stage)
        heat += sb
        if not tag and slabel:
            tag = slabel
        f["heat"] = heat
        f["tag"] = tag or ""
        counts[f["hot"]] += 1

    # Clubs configured but never seen in the feed. Before the Champions League
    # draw most European names are legitimately absent, so this is a heads-up,
    # not an error — but after the draw it is how a spelling mismatch surfaces.
    missing = sorted({c["name"] for c in clubs if club_key(c["name"]) not in seen
                      and not any(club_key(n) in seen for n in (c.get("aka") or []))})

    payload["top_clubs"] = [
        {"name": c["name"], "tier": c["tier"], "group": c["group"],
         "seen": club_key(c["name"]) in seen or any(club_key(n) in seen for n in (c.get("aka") or []))}
        for c in clubs
    ]
    payload["top_unmatched"] = missing
    return {"big": counts[2], "one": counts[1], "missing": missing}


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text())
    template = (ROOT / "template.html").read_text()

    if "--demo" in sys.argv:
        payload = demo_payload(cfg)
        print("Building from SYNTHETIC data (layout check only).")
    else:
        src = ROOT / "data" / "fixtures.json"
        if not src.exists():
            sys.exit("data/fixtures.json not found — run `python3 sync.py` first (or `python3 build.py --demo`).")
        payload = json.loads(src.read_text())
        # curated breaks live in config.json and are re-applied on every build
        detected = [b for b in payload.get("breaks", []) if b.get("source") == "detected"]
        payload["breaks"] = list(cfg.get("breaks") or []) + detected

    stats = annotate_top(payload, cfg)
    print(f"Top clubs: {stats['big']} big matches (both clubs listed), "
          f"{stats['one']} with one listed club.")
    if stats["missing"]:
        print(f"  not yet seen in the feed ({len(stats['missing'])}): "
              + ", ".join(stats["missing"]))

    # ensure_ascii=True on purpose: the built page carries no charset declaration
    # of its own (the Artifact wrapper supplies the <head>), so keeping every byte
    # inside ASCII is what stops "Atletico" arriving as "AtlÃ©tico".
    blob = json.dumps(payload, separators=(",", ":"))
    # </script> inside a JSON string literal would close the enclosing script tag
    blob = blob.replace("</", "<\\/")

    OUT.write_text(template.replace("__DATA__", blob))
    kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT}  ({kb:.0f} KB, {len(payload['fixtures'])} fixtures, {len(payload.get('breaks') or [])} breaks)")


if __name__ == "__main__":
    main()
