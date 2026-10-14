"""Build `preview_data.json` for the /preview mockups from real 2025-26 box scores.

Run after loading a season into a local database (see `undertow.ingest.replay`):

    uv run python -m undertow.web.build_preview --season 8

The output is real public stats (names, teams, positions, per-game lines) scored with our
scoring engine. What is invented: the two fantasy teams, their managers, the draft, the league
standings and the transactions. Nothing here claims an injury, a transaction or a live game.
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.db import get_engine
from undertow.models import Game, GoalieGameStats, Player, ProTeam, SkaterGameStats
from undertow.scoring import ScoringConfig, score_goalie, score_skater
from undertow.services.stat_lines import goalie_stat_line, skater_stat_line

OUT = Path(__file__).with_name("preview_data.json")
ET = ZoneInfo("America/New_York")
CONFIG = ScoringConfig()
MIN_GP = 10
# Roster needs per fantasy team: lineup F F F D D UTIL(F) G, bench F F D G.
NEEDS = {"F": 6, "D": 3, "G": 2}
LINEUP_SLOTS = ["F", "F", "F", "D", "D", "UTIL", "G"]

STANDINGS = [
    ("Rink Rats", "Jordan", "3-0", 118.2),
    ("Tidebreakers", "You", "2-1", 109.4),
    ("Blue Line Club", "Sam", "2-1", 104.8),
    ("Neutral Zone", "Alex", "2-1", 101.3),
    ("Power Play Pals", "Riley", "2-1", 99.0),
    ("Five Hole", "Casey", "1-2", 96.5),
    ("Top Shelf", "Morgan", "1-2", 93.1),
    ("Icing Illusions", "Taylor", "1-2", 90.7),
    ("Zamboni Dreams", "Quinn", "0-3", 84.2),
    ("Backcheckers", "Drew", "0-3", 80.6),
]
OTHER_MATCHUPS = [
    ("Blue Line Club", 29.0, "Neutral Zone", 33.5),
    ("Power Play Pals", 22.8, "Five Hole", 19.4),
    ("Top Shelf", 17.3, "Icing Illusions", 20.1),
    ("Zamboni Dreams", 12.0, "Backcheckers", 14.6),
]


def fmt_time(dt: datetime) -> str:
    local = dt.astimezone(ET)
    return local.strftime("%a ") + local.strftime("%I:%M %p").lstrip("0")


def skater_text(row: SkaterGameStats) -> str:
    bits = []
    if row.goals:
        bits.append(f"{row.goals}G")
    if row.assists:
        bits.append(f"{row.assists}A")
    bits.append(f"{row.shots} SOG")
    if row.blocks:
        bits.append(f"{row.blocks} BLK")
    return " ".join(bits)


def goalie_text(row: GoalieGameStats, result_type: str | None) -> str:
    decision = row.decision or "ND"
    if decision == "W" and result_type in ("OT", "SO"):
        decision = f"W ({result_type})"
    return f"{decision} · {row.saves} SV · {row.goals_against} GA"


def load(session: Session, season_id: int):
    games = {
        g.id: g
        for g in session.scalars(select(Game).where(Game.season_id == season_id)).all()
        if g.status == "final"
    }
    players = {p.id: p for p in session.scalars(select(Player)).all()}
    teams = {t.id: t for t in session.scalars(select(ProTeam)).all()}
    played = defaultdict(list)  # player_id -> [(game, points, line text)]
    for row in session.scalars(select(SkaterGameStats)).all():
        if row.game_id in games:
            pts = score_skater(skater_stat_line(row), CONFIG).total
            played[row.player_id].append((games[row.game_id], pts, skater_text(row)))
    for row in session.scalars(select(GoalieGameStats)).all():
        game = games.get(row.game_id)
        if game and row.toi_seconds > 0:
            pts = score_goalie(goalie_stat_line(row, game.result_type), CONFIG).total
            played[row.player_id].append((game, pts, goalie_text(row, game.result_type)))
    for entries in played.values():
        entries.sort(key=lambda e: e[0].start_utc)
    return games, players, teams, played


def pick_week(games: dict[int, Game]) -> tuple[datetime, datetime, int]:
    """An early-season Monday-to-Sunday week (ET), the one with the most games.

    Restricted to weeks 4 to 9 of the season so the "Week N of 18" label is believable.
    Returns (start, end, week number).
    """
    by_week: dict[datetime, int] = defaultdict(int)
    for g in games.values():
        local = g.start_utc.astimezone(ET)
        monday = (local - timedelta(days=local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        by_week[monday] += 1
    ordered = sorted(by_week)
    candidates = ordered[3:9]
    start = max(candidates, key=lambda m: (by_week[m], -m.timestamp()))
    return start, start + timedelta(days=7), ordered.index(start) + 1


def draft(players, teams, played):
    """Snake-draft two sample teams from the real pool, best points per game first."""
    pool = []
    for pid, entries in played.items():
        p = players.get(pid)
        if p and p.position and len(entries) >= MIN_GP:
            total = sum(e[1] for e in entries)
            pool.append((float(total) / len(entries), float(total), len(entries), p))
    pool.sort(key=lambda r: -r[0])
    rosters = {"me": defaultdict(list), "opp": defaultdict(list)}
    order = ["me", "opp", "opp", "me"] * 6
    taken: set[int] = set()
    for side in order:
        need = {k: NEEDS[k] - len(rosters[side][k]) for k in NEEDS}
        for *_, p in pool:
            if p.id in taken or need[p.position] <= 0:
                continue
            rosters[side][p.position].append(p)
            taken.add(p.id)
            break
    return pool, rosters, taken


def entry(p, teams, games_week, now, played):
    """One player's week: points so far, latest line, and what is still to come."""
    mine = [e for e in played.get(p.id, []) if e[0] in games_week.get(p.pro_team_id, [])]
    done = [e for e in mine if e[0].start_utc <= now]
    upcoming = [g for g in games_week.get(p.pro_team_id, []) if g.start_utc > now]
    pts = sum(e[1] for e in done) if done else None
    if upcoming:
        status, line = "yet", f"Next {fmt_time(upcoming[0].start_utc)}"
        if done:
            line = f"{done[-1][2]} · then {fmt_time(upcoming[0].start_utc)}"
    elif done:
        status, line = "final", done[-1][2] + (f" · {len(done)} gm" if len(done) > 1 else "")
    else:
        status, line = "yet", "No game this week"
    return {
        "name": f"{p.first_name} {p.last_name}",
        "pos": p.position,
        "team": teams[p.pro_team_id].code if p.pro_team_id in teams else "",
        "status": status,
        "line": line,
        "pts": round(float(pts), 1) if pts is not None else None,
        "games": len(games_week.get(p.pro_team_id, [])),
        "next": fmt_time(upcoming[0].start_utc) if upcoming else "",
    }


def build(season_id: int) -> dict:
    with Session(get_engine()) as session:
        games, players, teams, played = load(session, season_id)
    start, end, week_no = pick_week(games)
    games_week: dict[int, list[Game]] = defaultdict(list)
    for g in sorted(games.values(), key=lambda g: g.start_utc):
        if start <= g.start_utc.astimezone(ET) < end:
            games_week[g.home_team_id].append(g)
            games_week[g.away_team_id].append(g)
    pool, rosters, taken = draft(players, teams, played)

    def lineup_and_bench(side, now):
        r = rosters[side]
        f, d, g = list(r["F"]), list(r["D"]), list(r["G"])
        order = [f[0], f[1], f[2], d[0], d[1], f[3], g[0]]
        bench = [f[4], f[5], d[2], g[1]]
        make = lambda p: entry(p, teams, games_week, now, played)  # noqa: E731
        return (
            [[slot, make(p)] for slot, p in zip(LINEUP_SLOTS, order, strict=True)],
            [make(p) for p in bench],
        )

    # Pick the moment in the week ("now") that gives the most even mix of finished players
    # and players with games still to come, across both lineups.
    best = None
    for day in range(2, 7):
        for hour in (12, 20):
            now = (start + timedelta(days=day, hours=hour)).astimezone(ET)
            a, _ = lineup_and_bench("me", now)
            b, _ = lineup_and_bench("opp", now)
            done = sum(1 for _, p in a + b if p["status"] == "final")
            score = abs(done - 7)
            if best is None or score < best[0]:
                best = (score, now)
    now = best[1]
    me_lineup, bench = lineup_and_bench("me", now)
    opp_lineup, _ = lineup_and_bench("opp", now)

    def total(lineup):
        return round(sum(p["pts"] or 0 for _, p in lineup), 1)

    me_score, opp_score = total(me_lineup), total(opp_lineup)
    my_names = [p["name"] for _, p in me_lineup]

    def pool_row(r):
        fpg, tot, gp, p = r
        return [
            f"{p.first_name} {p.last_name}",
            p.position,
            teams[p.pro_team_id].code if p.pro_team_id in teams else "",
            round(fpg, 1),
            round(tot, 1),
            gp,
            p.id not in taken,
        ]

    # A balanced list for the Players tab: the best few at each position overall, plus the best
    # still available (the sample teams draft the top players), best points per game first.
    shown = []
    for pos, n_all, n_free in (("F", 4, 3), ("D", 3, 2), ("G", 2, 1)):
        at_pos = [r for r in pool if r[3].position == pos]
        free_pos = [r for r in at_pos if r[3].id not in taken]
        shown += at_pos[:n_all] + [r for r in free_pos if r not in at_pos[:n_all]][:n_free]
    players_tab = [pool_row(r) for r in sorted(shown, key=lambda r: -r[0])]
    free = [r for r in players_tab if r[6]]  # available players only
    activity = [
        ["Today 9:12 AM", f"Jordan added {free[0][0]} ({free[0][1]})."],
        ["Today 8:40 AM", f"Sam claimed {free[1][0]} ({free[1][1]}) off waivers."],
        ["Yesterday", "Taylor and Casey proposed a trade. Commissioner review open."],
        ["Yesterday", f"You set {my_names[0]} in your lineup."],
    ]
    last_day = end - timedelta(days=1)
    return {
        "source": f"2025-26 PWHL regular season (HockeyTech season {season_id})",
        "league": {
            "name": "League One",
            "week": week_no,
            "weeks": 18,
            "range": f"{start:%b} {start.day} to {last_day:%b} {last_day.day}",
        },
        "my_team": {"name": "Tidebreakers", "owner": "You", "record": "2-1", "score": me_score},
        "opp_team": {"name": "Rink Rats", "owner": "Jordan", "record": "1-2", "score": opp_score},
        "my_lineup": me_lineup,
        "opp_lineup": opp_lineup,
        "bench": bench,
        "players": players_tab,
        "standings": [list(r) for r in STANDINGS],
        "schedule": [["Tidebreakers", me_score, "Rink Rats", opp_score]]
        + [list(r) for r in OTHER_MATCHUPS],
        "activity": activity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=8)
    args = parser.parse_args()
    data = build(args.season)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print("week:", data["league"]["week"], data["league"]["range"])
    for label in ("my_lineup", "opp_lineup"):
        for slot, p in data[label]:
            name, team, status = p["name"], p["team"], p["status"]
            print(f"  {label:10} {slot:4} {name:24} {team:4} {status:5} {p['pts']} | {p['line']}")


if __name__ == "__main__":
    main()
