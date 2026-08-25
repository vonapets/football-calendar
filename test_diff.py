#!/usr/bin/env python3
"""Check that the daily diff catches both kinds of reschedule ESPN produces."""
import copy, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync import diff_snapshots

live = json.loads(Path("data/fixtures.json").read_text())["fixtures"]
epl = [f for f in live if f["comp"] == "epl" and not f["finished"]]
ucl = [f for f in live if f["comp"] == "uecl"]

fails = []

# ---- case 1: TV pick. Same id, kickoff edited in place. ----
old = copy.deepcopy(epl[:40])
new = copy.deepcopy(old)
new[3]["utc"] = "2026-08-30T16:30:00Z"; new[3]["ts"] = 1788150600
ch = diff_snapshots(old, new, "2026-08-20")
moved = [c for c in ch if c["kind"] == "moved" and c["id"] == new[3]["id"]]
print(f"1. kickoff moved in place        -> {len(moved)} change(s)")
if len(moved) != 1 or moved[0]["from_utc"] != old[3]["utc"]:
    fails.append("case 1: in-place move not detected correctly")
else:
    print(f"   {moved[0]['home']} v {moved[0]['away']}: {moved[0]['from_utc']} -> {moved[0]['to_utc']}")

# ---- case 2: postponement. Old id frozen at PST, brand-new id at a new date. ----
old = copy.deepcopy(epl[:40])
new = copy.deepcopy(old)
victim = new[7]
victim["status"], victim["status_long"], victim["disrupted"] = "PST", "Postponed", True
replacement = copy.deepcopy(victim)
replacement.update({"id": 999999001, "utc": "2027-01-13T19:45:00Z", "ts": 1799430300,
                    "status": "NS", "status_long": "Not started", "disrupted": False})
new.append(replacement)
ch = diff_snapshots(old, new, "2026-08-20")
relinked = [c for c in ch if c["id"] == 999999001]
status_logged = [c for c in ch if c["kind"] == "status" and c["id"] == victim["id"]]
print(f"2. postponed, replaced by new id -> {len(relinked)} relink(s), {len(status_logged)} status log(s)")
if len(relinked) != 1 or relinked[0]["from_utc"] != epl[7]["utc"]:
    fails.append("case 2: replacement fixture not linked back to the postponed one")
else:
    print(f"   {relinked[0]['home']} v {relinked[0]['away']}: {relinked[0]['from_utc']} -> {relinked[0]['to_utc']}")
    print(f"   note: {relinked[0].get('note')}")

# ---- case 3: two-legged tie must link to the correct leg, not the other one ----
legs = [f for f in ucl if f["round"].endswith("1st Leg")][:1]
if legs:
    leg1 = legs[0]
    other = next((f for f in ucl if f["home"] == leg1["away"] and f["away"] == leg1["home"]), None)
    old = copy.deepcopy([leg1] + ([other] if other else []))
    new = copy.deepcopy(old)
    new[0]["status"], new[0]["disrupted"] = "PST", True
    rep = copy.deepcopy(new[0])
    rep.update({"id": 999999002, "ts": new[0]["ts"] + 86400 * 3,
                "utc": "2026-08-01T19:00:00Z", "status": "NS", "disrupted": False})
    new.append(rep)
    ch = diff_snapshots(old, new, "2026-08-20")
    linked = [c for c in ch if c["id"] == 999999002]
    ok = len(linked) == 1 and linked[0]["home"] == leg1["home"]
    print(f"3. two-legged tie links to right leg -> {'OK' if ok else 'WRONG'}")
    if not ok: fails.append("case 3: linked to the wrong leg")

# ---- case 4: a quiet day must produce no noise ----
old = copy.deepcopy(epl[:60])
ch = diff_snapshots(old, copy.deepcopy(old), "2026-08-20")
print(f"4. nothing changed               -> {len(ch)} change(s)")
if ch: fails.append("case 4: reported changes when nothing moved")

print()
if fails:
    for f in fails: print("FAIL:", f)
    sys.exit(1)
print("all 4 cases pass")
