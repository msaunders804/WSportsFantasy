"""Coverage audit: for each 2026-27 roster spot, how much PWHL history does the player have?

Fetches rosters through undertow.ingest (throttled, cached under ./data/), then joins on
person_id across seasons and, for 2025-26, adds games played from data/replay_cache box scores.
Run from repo root with HOCKEYTECH_KEY set:  uv run python analysis/pwhl_coverage_audit/audit.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from undertow.ingest.feed import FeedError, HockeyTechFeed

HERE = Path(__file__).parent
CACHE = HERE / "data"
OUT = HERE / "output"
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
REPLAY = HERE.parents[1] / "data" / "replay_cache"

CURRENT = [11, 10]  # regular season, preseason (camp) rosters
PAST = {1: "2024", 5: "2024-25", 8: "2025-26"}  # regular seasons
TEAM_IDS = {
    11: [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13],
    10: [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13],
    8: [1, 2, 3, 4, 5, 6, 8, 9],
    5: [1, 2, 3, 4, 5, 6],
    1: [1, 2, 3, 4, 5, 6],
}


def strip(body: str) -> str:
    body = body.strip()
    return body[1:-1] if body.startswith("(") else body


def roster(feed: HockeyTechFeed | None, team: int, season: int) -> list[dict]:
    f = CACHE / f"roster_{team}_{season}.json"
    if season == 8 and (REPLAY / f"roster_{team}_8.json").exists():
        f = REPLAY / f"roster_{team}_8.json"
    if not f.exists():
        if feed is None:
            return []
        try:
            f.write_text(feed.roster(team, season).body)
        except FeedError as e:
            print(f"  skip team {team} season {season}: {e}", file=sys.stderr)
            return []
    rows = json.loads(strip(f.read_text()))["SiteKit"]["Roster"]
    # The feed appends a nested list of staff (GM, coach) after the players; drop it.
    return [r for r in rows if isinstance(r, dict)]


def season8_gp() -> dict[str, int]:
    gp: Counter = Counter()
    for f in REPLAY.glob("game_*.json"):
        g = json.loads(strip(f.read_text()))
        for side in ("homeTeam", "visitingTeam"):
            t = g.get(side) or {}
            for p in (t.get("skaters") or []) + (t.get("goalies") or []):
                toi = p.get("stats", {}).get("toi") or p.get("stats", {}).get("timeOnIce")
                if toi:
                    gp[str(p["info"]["id"])] += 1
    return gp


def main() -> None:
    try:
        feed = HockeyTechFeed()
    except FeedError:
        print("HOCKEYTECH_KEY not set; using cached files only")
        feed = None

    history: dict[str, dict[str, bool]] = defaultdict(dict)  # person_id -> season label -> seen
    for season, label in PAST.items():
        for team in TEAM_IDS[season]:
            for p in roster(feed, team, season):
                if p.get("person_id"):
                    history[p["person_id"]][label] = True
    gp8 = season8_gp()
    pid_to_person = {}
    for team in TEAM_IDS[8]:
        for p in roster(feed, team, 8):
            pid_to_person[str(p.get("player_id") or p.get("id"))] = p.get("person_id")
    person_gp8 = defaultdict(int)
    for pid, n in gp8.items():
        if pid in pid_to_person:
            person_gp8[pid_to_person[pid]] += n

    rows = []
    for season in CURRENT:
        for team in TEAM_IDS[season]:
            for p in roster(feed, team, season):
                pid = p.get("person_id")
                seasons_seen = sorted(history.get(pid, {}))
                rows.append(
                    {
                        "roster_season": season,
                        "team_id": team,
                        "team": p.get("team_name") or team,
                        "person_id": pid,
                        "name": f"{p.get('first_name')} {p.get('last_name')}",
                        "pos": p.get("position"),
                        "status": p.get("status"),
                        "rookie": p.get("rookie"),
                        "birthdate": p.get("birthdate"),
                        "country": p.get("homecntry") or p.get("birthcntry"),
                        "pwhl_seasons": ",".join(seasons_seen),
                        "n_pwhl_seasons": len(seasons_seen),
                        "gp_2025_26": person_gp8.get(pid, 0),
                    }
                )
    (OUT / "audit_rows.json").write_text(json.dumps(rows, indent=1))

    for season in CURRENT:
        rs = [r for r in rows if r["roster_season"] == season]
        print(f"\n== Roster season {season}: {len(rs)} players ==")
        if not rs:
            continue
        by_team = defaultdict(list)
        for r in rs:
            by_team[r["team"]].append(r)
        print(f"{'team':28}{'n':>3}{'3+ssn':>6}{'1-2':>5}{'new':>5}{'GP25-26>=15':>12}  statuses")
        for team, ps in sorted(by_team.items()):
            tiers = Counter(min(r["n_pwhl_seasons"], 3) for r in ps)
            gp15 = sum(r["gp_2025_26"] >= 15 for r in ps)
            st = Counter(r["status"] for r in ps)
            mid = tiers[1] + tiers[2]
            print(f"{team:28}{len(ps):>3}{tiers[3]:>6}{mid:>5}{tiers[0]:>5}{gp15:>12}  {dict(st)}")
        tot = Counter(min(r["n_pwhl_seasons"], 3) for r in rs)
        print(f"TOTAL no PWHL history: {tot[0]} / {len(rs)}  ({tot[0] / len(rs):.0%})")
        pos = Counter((r["pos"], r["n_pwhl_seasons"] == 0) for r in rs)
        print("no-history by position:", {k[0]: v for k, v in pos.items() if k[1]})


if __name__ == "__main__":
    main()
