"""Aggregate the cached box scores into per-player season lines for the target players.

Offline. Matches roster targets (ncaa_targets.csv) to box-score rows by school and name
(accent-stripped last name plus first-name prefix/initial); unmatched targets are listed.
"""

import csv
import glob
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "data" / "ncaa_cache"
SLUG = {  # school (draft list) -> ncaa.com seo slug; mirrors pull.py ALIASES
    "Penn State": "penn-st",
    "Minnesota-Duluth": "minn-duluth",
    "Ohio State": "ohio-st",
    "UConn": "uconn",
    "St. Cloud State": "st-cloud-st",
    "St. Lawrence": "st-lawrence",
    "Boston University": "boston-u",
    "Minnesota State": "minn-st-mankato",
}


def slug(s: str) -> str:
    return SLUG.get(s) or s.lower().replace(" ", "-")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


def num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


lines: dict[tuple[str, str], dict] = defaultdict(lambda: defaultdict(float))
names: dict[tuple[str, str], tuple[str, str]] = {}
games = 0
for f in glob.glob(str(CACHE / "game__*__boxscore.json")):
    d = json.load(open(f))
    teams = {str(t["teamId"]): t["seoname"] for t in d["teams"]}
    games += 1
    for tb in d["teamBoxscore"]:
        seo = teams.get(str(tb["teamId"]))
        for p in tb["playerStats"]:
            key = (seo, norm(p["firstName"]) + "|" + norm(p["lastName"]))
            names[key] = (p["firstName"], p["lastName"])
            L = lines[key]
            played = (
                p.get("participated")
                or p.get("position") != "G"
                and (num(p["shots"]) or num(p["goals"]) or num(p["assists"]) or num(p["minor"]))
            )
            L["gp"] += 1 if p.get("participated") else 0
            for k in ("goals", "assists", "shots", "blk", "goalsAllowed", "saves", "goalieMinutes"):
                L[k] += num(p.get(k))
            L["pos"] = p["position"]
print(f"box scores aggregated: {games}")

targets = list(csv.DictReader(open(HERE / "data/ncaa_targets.csv", encoding="utf-8")))
found, missing = [], []
for t in targets:
    seo = slug(t["school"])
    first, _, last = t["name"].partition(" ")
    ln, fn = norm(last), norm(first)
    hits = [
        k
        for k in lines
        if k[0] == seo
        and k[1].split("|")[1] == ln
        and (k[1].split("|")[0][:3] == fn[:3] or k[1].split("|")[0][:1] == fn[:1])
    ]
    (found if len(hits) == 1 else missing).append((t, hits))
print(
    f"targets: {len(targets)}; found in box scores: {len(found)}; missing/ambiguous: {len(missing)}"
)
print("\nTop of the found list (GP / G / A / shots, goalies: saves, GA):")
rows = []
for t, hits in found:
    L = lines[hits[0]]
    rows.append(
        (
            t["name"],
            t["school"],
            L["pos"],
            int(L["gp"]),
            int(L["goals"]),
            int(L["assists"]),
            int(L["shots"]),
            int(L["saves"]),
            int(L["goalsAllowed"]),
        )
    )
for r in sorted(rows, key=lambda r: -(r[4] + r[5]))[:12]:
    print("  ", r)
gp = sorted(r[3] for r in rows)
print("GP distribution (min/median/max):", gp[0], gp[len(gp) // 2], gp[-1])
print("Goalies:", [(r[0], r[3], r[7], r[8]) for r in rows if r[2] == "G"])
print(
    "Missing/ambiguous:",
    [(t["name"], t["school"], [names[h] for h in hits]) for t, hits in missing],
)
out = HERE / "data/ncaa_player_lines_2025_26.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(
        ["name", "school", "pos", "gp", "goals", "assists", "shots", "saves", "goals_allowed"]
    )
    w.writerows(rows)
print("wrote", out)
