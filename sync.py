#!/usr/bin/env python3
"""
Pull fixtures for every configured competition from ESPN's public soccer feed,
normalise them, diff against yesterday's snapshot to catch reschedules, and
write the result to data/.

Run:  python3 sync.py

No API key is needed. This reads the same JSON endpoint espn.com's own site uses.
It is undocumented, so sync.py is defensive: it validates every response, refuses
to overwrite good data with a bad fetch, and keeps the previous snapshot on failure.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

FIXTURES_FILE = DATA / "fixtures.json"
CHANGES_FILE = DATA / "changes.json"
RAW_DIR = DATA / "raw"

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"

# ESPN status name -> (short code, long text). Anything unmapped falls through
# to the response's own description, so a new ESPN status never crashes the sync.
STATUS_MAP = {
    "STATUS_SCHEDULED":      ("NS",   "Not started"),
    "STATUS_IN_PROGRESS":    ("LIVE", "In progress"),
    "STATUS_FIRST_HALF":     ("1H",   "First half"),
    "STATUS_HALFTIME":       ("HT",   "Half time"),
    "STATUS_SECOND_HALF":    ("2H",   "Second half"),
    "STATUS_EXTRA_TIME":     ("ET",   "Extra time"),
    "STATUS_SHOOTOUT":       ("PEN",  "Penalty shootout"),
    "STATUS_FULL_TIME":      ("FT",   "Full time"),
    "STATUS_FINAL":          ("FT",   "Full time"),
    "STATUS_FINAL_AET":      ("AET",  "After extra time"),
    "STATUS_FINAL_PEN":      ("PEN",  "Won on penalties"),
    "STATUS_POSTPONED":      ("PST",  "Postponed"),
    "STATUS_CANCELED":       ("CANC", "Cancelled"),
    "STATUS_CANCELLED":      ("CANC", "Cancelled"),
    "STATUS_ABANDONED":      ("ABD",  "Abandoned"),
    "STATUS_SUSPENDED":      ("SUSP", "Suspended"),
    "STATUS_DELAYED":        ("SUSP", "Delayed"),
    "STATUS_FORFEIT":        ("WO",   "Forfeit"),
}
DISRUPTED = {"PST", "CANC", "SUSP", "ABD", "WO"}

# ESPN silently truncates to this many events if `limit` is omitted. If a season
# pull comes back at exactly this size the data is suspect, not complete.
TRUNCATION_TRIPWIRE = 100
PAGE_LIMIT = 1000


def fetch(slug: str, start: str, end: str, retries: int = 3) -> dict:
    """GET one competition's fixtures for a date window. Raises on give-up."""
    url = f"{BASE.format(slug=slug)}?dates={start}-{end}&limit={PAGE_LIMIT}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return json.load(resp)
        except Exception as exc:                      # noqa: BLE001 - retry then report
            last = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{slug}: {last}")


def title_round(slug: str, comp_tier: str) -> str:
    """Cups put the round in season.slug ('semifinals'); leagues put the season name."""
    if not slug or comp_tier == "league":
        return ""
    if slug[:4].isdigit():          # e.g. '2026-27-english-premier-league'
        return ""
    return slug.replace("-", " ").title().replace("Aet", "AET")


def normalise(event: dict, comp: dict, qual: bool = False) -> dict | None:
    try:
        comps = event.get("competitions") or []
        if not comps:
            return None
        c = comps[0]

        sides = {x.get("homeAway"): x for x in (c.get("competitors") or [])}
        home_t, away_t = sides.get("home"), sides.get("away")
        if not home_t or not away_t:
            return None
        home = (home_t.get("team") or {}).get("displayName")
        away = (away_t.get("team") or {}).get("displayName")
        if not home or not away:
            return None

        raw_date = event.get("date") or c.get("date")
        if not raw_date:
            return None
        # ESPN emits '2026-08-21T19:00Z' — no seconds, Z suffix.
        kickoff = datetime.strptime(raw_date.replace("Z", ""), "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)

        st = ((c.get("status") or {}).get("type") or {})
        name = st.get("name") or "STATUS_SCHEDULED"
        short, long_txt = STATUS_MAP.get(name, ("NS", st.get("description") or "Scheduled"))
        completed = bool(st.get("completed"))

        def score(side):
            try:
                return int(side.get("score"))
            except (TypeError, ValueError):
                return None

        rnd = title_round((event.get("season") or {}).get("slug", ""), comp["tier"])
        leg = next((n.get("text") for n in (c.get("notes") or [])
                    if n.get("text", "").endswith("Leg")), None)
        if leg:
            rnd = f"{rnd} {leg}".strip()

        return {
            "id": int(event["id"]),
            "comp": comp["key"],
            "utc": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ts": int(kickoff.timestamp()),
            "status": short,
            "status_long": long_txt,
            "round": rnd,
            "home": home,
            "away": away,
            "venue": (c.get("venue") or {}).get("fullName"),
            "gh": score(home_t) if completed else None,
            "ga": score(away_t) if completed else None,
            # ESPN flags a kickoff whose time is not yet confirmed. Showing that
            # honestly beats displaying a placeholder as if it were real.
            "tbd": c.get("timeValid") is False,
            "qual": qual,
            "disrupted": short in DISRUPTED,
            "finished": completed,
        }
    except Exception:                                 # noqa: BLE001 - skip the row, keep the sync
        return None


def diff_snapshots(old: list, new: list, today: str) -> list:
    """Describe what moved between yesterday's fixtures and today's.

    Two different reschedule shapes have to be handled:
      * a kick-off time edited in place (TV picks) — same id, new timestamp
      * a postponement — ESPN freezes the old id at PST and mints a NEW id at the
        new date, so the move has to be re-linked by competition and team pair.
    """
    old_by_id = {f["id"]: f for f in old}
    new_ids = {f["id"] for f in new}
    changes = []

    for f in new:
        prev = old_by_id.get(f["id"])
        if prev is None:
            continue
        if prev["utc"] != f["utc"]:
            changes.append({
                "detected": today, "kind": "moved", "id": f["id"], "comp": f["comp"],
                "home": f["home"], "away": f["away"],
                "from_utc": prev["utc"], "to_utc": f["utc"], "round": f["round"],
            })
        elif prev["status"] != f["status"] and f["status"] in DISRUPTED:
            changes.append({
                "detected": today, "kind": "status", "id": f["id"], "comp": f["comp"],
                "home": f["home"], "away": f["away"],
                "from_status": prev["status"], "to_status": f["status"],
                "status_long": f["status_long"],
                "from_utc": prev["utc"], "to_utc": f["utc"], "round": f["round"],
            })

    # Re-link ESPN's replacement fixtures: a brand-new id whose team pairing matches
    # an older fixture that has since been disrupted or has vanished from the feed.
    def pairing(f):
        return (f["comp"], f["home"], f["away"])

    stranded = {}
    for f in old:
        cur = next((n for n in new if n["id"] == f["id"]), None)
        if (cur is None and f["id"] not in new_ids) or (cur and cur["disrupted"]):
            stranded.setdefault(pairing(f), []).append(cur or f)

    for f in new:
        if f["id"] in old_by_id:
            continue
        candidates = stranded.get(pairing(f))
        if not candidates:
            continue
        # nearest prior date wins, so a two-legged tie links to the right leg
        prior = min(candidates, key=lambda p: abs(p["ts"] - f["ts"]))
        if prior["utc"] == f["utc"]:
            continue
        changes.append({
            "detected": today, "kind": "moved", "id": f["id"], "comp": f["comp"],
            "home": f["home"], "away": f["away"],
            "from_utc": prior["utc"], "to_utc": f["utc"], "round": f["round"],
            "note": f"replaces fixture {prior['id']} ({prior['status']})",
        })

    return changes


def detect_gaps(fixtures: list, cfg: dict) -> list:
    """Stretches with no domestic league football — catches unlisted shutdowns."""
    gd = cfg.get("gap_detection") or {}
    if not gd.get("enabled"):
        return []
    watch = set(gd.get("watch") or [])
    min_days = int(gd.get("min_days", 6))

    days = sorted({f["utc"][:10] for f in fixtures if f["comp"] in watch})
    if len(days) < 2:
        return []

    # a gap that just re-states a break we already list by hand adds nothing
    curated = [(b["start"], b["end"]) for b in (cfg.get("breaks") or [])]

    def already_listed(s: str, e: str) -> bool:
        return any(s <= c_end and e >= c_start for c_start, c_end in curated)

    gaps = []
    for a, b in zip(days, days[1:]):
        d0 = datetime.strptime(a, "%Y-%m-%d").date()
        d1 = datetime.strptime(b, "%Y-%m-%d").date()
        if (d1 - d0).days >= min_days:
            g_start = (d0 + timedelta(days=1)).isoformat()
            g_end = (d1 - timedelta(days=1)).isoformat()
            if already_listed(g_start, g_end):
                continue
            gaps.append({
                "start": g_start,
                "end": g_end,
                "days": (d1 - d0).days - 1,
                "source": "detected", "kind": "gap",
                "label": "No league football", "short": "NO LEAGUE",
            })
    return gaps


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text())
    start = cfg["window_start"].replace("-", "")
    end = cfg["window_end"].replace("-", "")

    previous_payload = {}
    if FIXTURES_FILE.exists():
        try:
            previous_payload = json.loads(FIXTURES_FILE.read_text())
        except json.JSONDecodeError:
            previous_payload = {}
    previous = previous_payload.get("fixtures", [])
    prev_by_comp = {}
    for f in previous:
        prev_by_comp.setdefault(f["comp"], []).append(f)

    RAW_DIR.mkdir(exist_ok=True)
    fixtures, per_comp, failures, warnings = [], {}, [], []

    for comp in cfg["competitions"]:
        rows, ok = [], True
        for slug in comp["slugs"]:
            try:
                body = fetch(slug, start, end)
            except Exception as exc:                  # noqa: BLE001
                ok = False
                failures.append({"comp": comp["key"], "slug": slug, "error": str(exc)})
                print(f"  !! {comp['name']:<20} {slug}: {exc}")
                continue

            events = body.get("events") or []
            if len(events) >= PAGE_LIMIT:
                warnings.append(f"{comp['key']}/{slug} returned {len(events)} events — at the page limit, may be truncated")
            elif len(events) == TRUNCATION_TRIPWIRE:
                warnings.append(f"{comp['key']}/{slug} returned exactly {TRUNCATION_TRIPWIRE} events — possible silent truncation")

            (RAW_DIR / f"{slug}.json").write_text(json.dumps(body, indent=1))
            is_qual = slug.endswith("_qual")
            rows.extend(n for n in (normalise(e, comp, is_qual) for e in events) if n)

        # Never let one bad fetch erase a competition that had data yesterday.
        held = prev_by_comp.get(comp["key"], [])
        if not rows and held and not ok:
            rows = held
            warnings.append(f"{comp['key']}: fetch failed, kept yesterday's {len(held)} fixtures")
            print(f"  ~~ {comp['name']:<20} fetch failed — kept {len(held)} from last run")
        elif not rows and held:
            rows = held
            warnings.append(f"{comp['key']}: feed returned nothing, kept yesterday's {len(held)} fixtures")
            print(f"  ~~ {comp['name']:<20} empty feed — kept {len(held)} from last run")

        # de-duplicate: qualifying and main-draw slugs can overlap
        seen_ids, deduped = set(), []
        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                deduped.append(r)

        fixtures.extend(deduped)
        per_comp[comp["key"]] = len(deduped)
        note = "" if deduped else "  (not drawn yet)"
        print(f"  ok {comp['name']:<20} {len(deduped):>4} fixtures{note}")

    if not fixtures:
        sys.exit("\nNo fixtures returned for any competition — ESPN's feed may have changed shape. "
                 "Previous data left untouched.")

    fixtures.sort(key=lambda f: (f["ts"], f["comp"], f["home"]))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_changes = diff_snapshots(previous, fixtures, today)

    history = []
    if CHANGES_FILE.exists():
        try:
            history = json.loads(CHANGES_FILE.read_text())
        except json.JSONDecodeError:
            history = []
    seen = {(c["kind"], c["id"], c.get("to_utc"), c.get("to_status")) for c in history}
    for c in new_changes:
        marker = (c["kind"], c["id"], c.get("to_utc"), c.get("to_status"))
        if marker not in seen:
            history.append(c)
            seen.add(marker)
    history.sort(key=lambda c: (c["detected"], c.get("to_utc") or ""), reverse=True)
    CHANGES_FILE.write_text(json.dumps(history, indent=1))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "ESPN",
        "season": cfg["season"],
        "season_label": cfg["season_label"],
        "competitions": cfg["competitions"],
        "fixtures": fixtures,
        "breaks": list(cfg.get("breaks") or []) + detect_gaps(fixtures, cfg),
        "changes": history,
        "counts": per_comp,
        "failures": failures,
        "warnings": warnings,
    }
    FIXTURES_FILE.write_text(json.dumps(payload, indent=1))

    print(f"\n{len(fixtures)} fixtures across {len(per_comp)} competitions -> {FIXTURES_FILE}")
    print(f"{len(new_changes)} new schedule change(s) today; {len(history)} logged in total")
    for w in warnings:
        print(f"  warning: {w}")
    if failures:
        print(f"  {len(failures)} feed(s) failed — see data/fixtures.json -> failures")


if __name__ == "__main__":
    main()
