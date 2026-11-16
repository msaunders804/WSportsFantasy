"""Pull NCAA D-I women's hockey data for the pilot, limited to schools of known PWHL players.

Stage 1 `scoreboards`: one request per day (no player data) -> game ids and team names.
Stage 2 `boxscores`: one request per game, only games involving a target school.
Every response is cached under data/ncaa_cache/, so reruns make no new requests. Throttled to one
request per second; identifies itself with a generic User-Agent. Raw data stays local (gitignored).

    python analysis/ncaa_pilot/pull.py scoreboards [--start 2025-09-25 --end 2026-03-31]
    python analysis/ncaa_pilot/pull.py boxscores
"""

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "data" / "ncaa_cache"
BASE = "https://ncaa-api.henrygd.me"
UA = "UndertowSports-research/0.1"
PAUSE = 1.0

# Draft-list school name -> ncaa.com team `seo` slug, for names that differ. Checked by the script
# against the scoreboards; anything unresolved is printed, never guessed.
ALIASES = {
    "Penn State": "penn-st",
    "Minnesota-Duluth": "minn-duluth",
    "Ohio State": "ohio-st",
    "UConn": "uconn",
    "St. Cloud State": "st-cloud-st",
    "St. Lawrence": "st-lawrence",
    "Boston University": "boston-u",
    "Minnesota State": "minn-st-mankato",
    "Wisconsin": "wisconsin",
    "Minnesota": "minnesota",
    "Boston College": "boston-college",
    "Mercyhurst": "mercyhurst",
}


def fetch(path: str) -> dict | None:
    f = CACHE / (path.strip("/").replace("/", "__") + ".json")
    if f.exists():
        return json.loads(f.read_text())
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    time.sleep(PAUSE)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} for {path}")
        return None
    except Exception as e:  # network hiccup: skip, rerun fills the gap from cache
        print(f"error for {path}: {e}")
        return None
    f.write_text(body)
    return json.loads(body)


def daterange(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


def scoreboards(start: date, end: date) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    n = 0
    for d in daterange(start, end):
        fetch(f"/scoreboard/icehockey-women/d1/{d:%Y/%m/%d}/all-conf")
        n += 1
        if n % 30 == 0:
            print(f"  {n} days done (through {d})")
    print("scoreboards done")


def file_date(f: Path) -> date:
    y, m, d = f.name.split("__")[3:6]
    return date(int(y), int(m), int(d))


def season_window(label: str) -> tuple[date, date]:
    """'2023-24' -> Aug 1 2023 .. Jul 31 2024."""
    y = int(label[:4])
    return date(y, 8, 1), date(y + 1, 7, 31)


def season_games(label: str | None = None) -> dict[str, dict]:
    games: dict[str, dict] = {}
    lo, hi = season_window(label) if label else (date.min, date.max)
    for f in CACHE.glob("scoreboard__*.json"):
        if not lo <= file_date(f) <= hi:
            continue
        d = json.loads(f.read_text())
        for g in d.get("games", []):
            g = g["game"]
            games[g["gameID"]] = {
                "away": g["away"]["names"]["seo"],
                "home": g["home"]["names"]["seo"],
                "away_short": g["away"]["names"]["short"],
                "home_short": g["home"]["names"]["short"],
                "final": g.get("finalMessage"),
                "file": f.name,
            }
    return games


def targets(label: str | None = None) -> list[str]:
    if label and label != "2025-26":
        return json.loads((HERE / "data" / "cohort_pulls.json").read_text())[label]
    rows = list(csv.DictReader(open(HERE / "data" / "ncaa_targets.csv", encoding="utf-8")))
    return sorted({r["school"] for r in rows})


def resolve(games: dict[str, dict], label: str | None = None) -> dict[str, str | None]:
    seo_names = {g["away"] for g in games.values()} | {g["home"] for g in games.values()}
    out = {}
    for s in targets(label):
        guess = ALIASES.get(s) or s.lower().replace(" ", "-").replace(".", "")
        out[s] = guess if guess in seo_names else None
    return out


def boxscores(label: str) -> None:
    games = season_games(label)
    if not games:
        raise SystemExit("no scoreboards cached for that season; run the scoreboards stage first")
    mapping = resolve(games, label)
    missing = [s for s, v in mapping.items() if v is None]
    print(f"[{label}] school -> ncaa.com slug:", {k: v for k, v in mapping.items() if v})
    if missing:
        # not fatal: a school may not have played D-I women's hockey that season (or needs an alias)
        print(f"[{label}] UNRESOLVED schools, skipped (check ALIASES): {missing}")
    slugs = set(mapping.values())
    want = [
        gid
        for gid, g in games.items()
        if (g["away"] in slugs or g["home"] in slugs)
        and (g["final"] or "").upper().startswith("FINAL")
    ]
    print(f"{len(games)} games on scoreboards; {len(want)} final games involve a target school")
    for i, gid in enumerate(sorted(want), 1):
        fetch(f"/game/{gid}/boxscore")
        if i % 50 == 0:
            print(f"  {i}/{len(want)} box scores")
    print("boxscores done")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["scoreboards", "boxscores", "plan"])
    ap.add_argument("--season", default="2025-26", help="for boxscores: e.g. 2023-24")
    ap.add_argument("--start", default="2025-09-25")
    ap.add_argument("--end", default="2026-03-31")
    a = ap.parse_args()
    if a.stage == "scoreboards":
        scoreboards(date.fromisoformat(a.start), date.fromisoformat(a.end))
    elif a.stage == "boxscores":
        boxscores(a.season)
    else:
        games = season_games()
        print(len(games), "games cached;", resolve(games) if games else "")


if __name__ == "__main__":
    main()
