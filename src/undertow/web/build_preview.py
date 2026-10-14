"""Build `preview_data.json` for the /preview mockups from real 2025-26 box scores.

Run after loading a season into a local database (see `undertow.ingest.replay`; note the DB
tests truncate those tables, so reload first):

    uv run python -m undertow.ingest.replay --season 8 --offline
    uv run python -m undertow.web.build_preview --season 8

Real: player names, teams, positions, jersey numbers and per-game stat lines, scored with our
scoring engine. Invented: the ten fantasy teams and their managers, the draft, the records and
standings, and the transactions. Nothing here claims an injury or a live game.

The "now" of the demo is one moment in an early-season week; everything before it counts as
season-to-date, the week itself is partly played, and nothing after it is shown.
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
MIN_GP = 3
# Roster per fantasy team: lineup F F F D D UTIL(F) G, bench F F D D (only 12 goalies have played
# enough games to draft, so teams carry one).
NEEDS = {"F": 6, "D": 4, "G": 1}
LINEUP_SLOTS = ["F", "F", "F", "D", "D", "UTIL", "G"]
POS_PLURAL = {"F": "forwards", "D": "defenders", "G": "goalies"}

# slug, team name, manager, wins so far (of the weeks played), average points for per week
TEAMS = [
    ("tidebreakers", "Tidebreakers", "You", 5, 36.5),
    ("rink-rats", "Rink Rats", "Jordan", 6, 39.4),
    ("blue-line-club", "Blue Line Club", "Sam", 5, 34.9),
    ("neutral-zone", "Neutral Zone", "Alex", 4, 33.8),
    ("power-play-pals", "Power Play Pals", "Riley", 4, 33.0),
    ("five-hole", "Five Hole", "Casey", 3, 32.2),
    ("top-shelf", "Top Shelf", "Morgan", 3, 31.0),
    ("icing-illusions", "Icing Illusions", "Taylor", 2, 30.2),
    ("zamboni-dreams", "Zamboni Dreams", "Quinn", 2, 28.1),
    ("backcheckers", "Backcheckers", "Drew", 1, 26.9),
]
MY_SLUG = "tidebreakers"
DRAFT_ORDER = [
    "blue-line-club",
    "rink-rats",
    "neutral-zone",
    "tidebreakers",
    "power-play-pals",
    "five-hole",
    "top-shelf",
    "icing-illusions",
    "zamboni-dreams",
    "backcheckers",
]
MATCHUPS = [
    ("tidebreakers", "rink-rats"),
    ("blue-line-club", "neutral-zone"),
    ("power-play-pals", "five-hole"),
    ("top-shelf", "icing-illusions"),
    ("zamboni-dreams", "backcheckers"),
]


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt_time(dt: datetime) -> str:
    local = dt.astimezone(ET)
    return local.strftime("%a ") + local.strftime("%I:%M %p").lstrip("0")


def fmt_day(dt: datetime) -> str:
    local = dt.astimezone(ET)
    return f"{local:%b} {local.day}"


def monday_of(dt: datetime) -> datetime:
    local = dt.astimezone(ET)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


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
    played = defaultdict(list)  # player_id -> chronological list of per-game dicts
    for row in session.scalars(select(SkaterGameStats)).all():
        game = games.get(row.game_id)
        if game:
            pts = score_skater(skater_stat_line(row), CONFIG).total
            played[row.player_id].append(
                {
                    "game": game,
                    "pts": pts,
                    "text": skater_text(row),
                    "g": row.goals,
                    "a": row.assists,
                    "sog": row.shots,
                    "blk": row.blocks,
                }
            )
    for row in session.scalars(select(GoalieGameStats)).all():
        game = games.get(row.game_id)
        if game and row.toi_seconds > 0:
            pts = score_goalie(goalie_stat_line(row, game.result_type), CONFIG).total
            played[row.player_id].append(
                {
                    "game": game,
                    "pts": pts,
                    "text": goalie_text(row, game.result_type),
                    "decision": row.decision,
                    "sv": row.saves,
                    "ga": row.goals_against,
                    "so": bool(row.shutout),
                }
            )
    for entries in played.values():
        entries.sort(key=lambda e: e["game"].start_utc)
    return games, players, teams, played


def trend_for(p: Player, history: list[dict], fpg: float) -> tuple[float, str]:
    """How hot a player is: recent points per game minus season average, plus a reason."""
    last = history[-3:]
    n = len(last)
    avg = float(sum(e["pts"] for e in last)) / n
    score = avg - fpg
    reason = ""
    if p.position == "G":
        if last[-1]["so"]:
            score += 3
            reason = "Just off a shutout"
        elif n >= 2 and last[-1]["ga"] <= 1 and last[-2]["ga"] <= 1:
            reason = "Allowed one or fewer goals in 2 straight"
        elif n == 3 and all(e["decision"] == "W" for e in last):
            reason = "Won their last 3 starts"
    else:
        goals = sum(e["g"] for e in last)
        points = goals + sum(e["a"] for e in last)
        if goals >= 2:
            reason = f"{goals} goals in last {n} games"
        elif points >= 3:
            reason = f"{points} points in last {n} games"
    if not reason and score >= 1:
        reason = f"Averaging {avg:.1f} FP over last {n} games"
    return score, reason


def build_pool(players, history_of):
    pool = []
    for pid, p in players.items():
        hist = history_of(pid)
        if not p.position or len(hist) < MIN_GP:
            continue
        total = float(sum(e["pts"] for e in hist))
        fpg = total / len(hist)
        trend, reason = trend_for(p, hist, fpg)
        pool.append(
            {"p": p, "fpg": fpg, "total": total, "gp": len(hist), "trend": trend, "why": reason}
        )
    pool.sort(key=lambda r: -r["fpg"])
    return pool


def draft(pool):
    """Snake-draft ten sample teams from the real pool, best points per game first."""
    rosters = {slug: defaultdict(list) for slug in DRAFT_ORDER}
    taken: set[int] = set()
    for rnd in range(sum(NEEDS.values())):
        order = DRAFT_ORDER if rnd % 2 == 0 else DRAFT_ORDER[::-1]
        for slug in order:
            need = {k: NEEDS[k] - len(rosters[slug][k]) for k in NEEDS}
            for r in pool:
                p = r["p"]
                if p.id in taken or need[p.position] <= 0:
                    continue
                rosters[slug][p.position].append(p)
                taken.add(p.id)
                break
    return rosters, taken


def pick_week(games: dict[int, Game], played) -> tuple[datetime, datetime, list[datetime]]:
    """An early-season Monday-to-Sunday week (ET): weeks 7 to 11, most games.

    Prefers a week that opens with at least one goalie fresh off a shutout, so the Trending
    sort has that case to show.
    """
    by_week: dict[datetime, int] = defaultdict(int)
    for g in games.values():
        by_week[monday_of(g.start_utc)] += 1
    ordered = sorted(by_week)

    def fresh_shutouts(monday: datetime) -> int:
        count = 0
        for entries in played.values():
            before = [e for e in entries if e["game"].start_utc < monday]
            if len(before) >= MIN_GP and "so" in before[-1] and before[-1]["so"]:
                count += 1
        return count

    start = max(ordered[6:11], key=lambda m: (fresh_shutouts(m) > 0, by_week[m], -m.timestamp()))
    return start, start + timedelta(days=7), ordered


def build(season_id: int) -> dict:
    with Session(get_engine()) as session:
        games, players, teams, played = load(session, season_id)
    start, end, mondays = pick_week(games, played)
    week_no = mondays.index(start) + 1

    def code(team_id) -> str:
        return teams[team_id].code if team_id in teams else ""

    def history(pid: int) -> list[dict]:  # season-to-date: games before this week
        return [e for e in played.get(pid, []) if e["game"].start_utc < start]

    games_week: dict[int, list[Game]] = defaultdict(list)
    for g in sorted(games.values(), key=lambda g: g.start_utc):
        if start <= g.start_utc.astimezone(ET) < end:
            games_week[g.home_team_id].append(g)
            games_week[g.away_team_id].append(g)

    pool = build_pool(players, history)
    rosters, taken = draft(pool)
    owner_of = {p.id: slug for slug, r in rosters.items() for pos in r.values() for p in pos}
    team_name = {slug: name for slug, name, *_ in TEAMS}

    def entry(p: Player, now: datetime) -> dict:
        """One player's week: points so far, the latest line, and what is still to come."""
        mine = [e for e in played.get(p.id, []) if e["game"] in games_week.get(p.pro_team_id, [])]
        done = [e for e in mine if e["game"].start_utc <= now]
        upcoming = [g for g in games_week.get(p.pro_team_id, []) if g.start_utc > now]
        pts = sum(e["pts"] for e in done) if done else None
        if upcoming:
            status, line = "yet", f"Next {fmt_time(upcoming[0].start_utc)}"
            if done:
                line = f"{done[-1]['text']} · then {fmt_time(upcoming[0].start_utc)}"
        elif done:
            status = "final"
            line = done[-1]["text"] + (f" · {len(done)} gm" if len(done) > 1 else "")
        else:
            status, line = "yet", "No game this week"
        return {
            "id": p.id,
            "name": f"{p.first_name} {p.last_name}",
            "pos": p.position,
            "team": code(p.pro_team_id),
            "status": status,
            "line": line,
            "pts": round(float(pts), 1) if pts is not None else None,
            "games": len(games_week.get(p.pro_team_id, [])),
            "next": fmt_time(upcoming[0].start_utc) if upcoming else "",
        }

    def lineup_and_bench(slug: str, now: datetime):
        r = rosters[slug]
        f, d, g = list(r["F"]), list(r["D"]), list(r["G"])
        starters = [f[0], f[1], f[2], d[0], d[1], f[3], g[0]]
        bench = [f[4], f[5], d[2], d[3]]
        return (
            [[slot, entry(p, now)] for slot, p in zip(LINEUP_SLOTS, starters, strict=True)],
            [entry(p, now) for p in bench],
        )

    # Choose the moment in the week that gives the most even mix of finished players and players
    # with games still to come, across every lineup in the league.
    best = None
    for day in range(2, 7):
        for hour in (12, 20):
            now = (start + timedelta(days=day, hours=hour)).astimezone(ET)
            done = sum(
                1
                for slug in DRAFT_ORDER
                for _, p in lineup_and_bench(slug, now)[0]
                if p["status"] == "final"
            )
            score = abs(done - 5 * len(DRAFT_ORDER))  # about 5 of 7 starters finished
            if best is None or score < best[0]:
                best = (score, now)
    now = best[1]

    weeks_played = week_no - 1
    teams_out = {}
    for slug, name, manager, wins, avg_pf in TEAMS:
        lineup, bench = lineup_and_bench(slug, now)
        # Highlight the single best bench performer if they beat at least one starter.
        starter_pts = [p["pts"] or 0 for _, p in lineup if p["pos"] != "G"]
        best_bench = max((p for p in bench if p["pos"] != "G"), key=lambda p: p["pts"] or 0)
        if best_bench["pts"] and any(best_bench["pts"] > s for s in starter_pts):
            best_bench["flag"] = "Outscored a starter"
        teams_out[slug] = {
            "name": name,
            "owner": manager,
            "record": f"{wins}-{weeks_played - wins}",
            "wins": wins,
            "pf": round(avg_pf * weeks_played, 1),
            "score": round(sum(p["pts"] or 0 for _, p in lineup), 1),
            "bench_score": round(sum(p["pts"] or 0 for p in bench), 1),
            "lineup": lineup,
            "bench": bench,
        }

    standings = sorted(teams_out.items(), key=lambda kv: (-kv[1]["wins"], -kv[1]["pf"]))
    standings_out = [[t["name"], t["owner"], t["record"], t["pf"]] for _, t in standings]

    # Players tab: best at each position, the hottest, and the best still available.
    def row(r):
        p = r["p"]
        return [
            f"{p.first_name} {p.last_name}",
            p.position,
            code(p.pro_team_id),
            round(r["fpg"], 1),
            round(r["total"], 1),
            r["gp"],
            p.id not in taken,
            round(r["trend"], 2),
            r["why"],
            p.id,
        ]

    shown: dict[int, dict] = {}
    for pos, n_all, n_free in (("F", 5, 3), ("D", 4, 2), ("G", 3, 1)):
        at_pos = [r for r in pool if r["p"].position == pos]
        for r in at_pos[:n_all] + [r for r in at_pos if r["p"].id not in taken][:n_free]:
            shown[r["p"].id] = r
    for r in sorted(pool, key=lambda r: -r["trend"])[:8]:
        shown[r["p"].id] = r
    players_tab = [row(r) for r in sorted(shown.values(), key=lambda r: -r["fpg"])]
    free = [r for r in players_tab if r[6]]

    # One page per player on a roster or in the Players tab.
    rank_in = {}
    for pos in POS_PLURAL:
        for i, r in enumerate([r for r in pool if r["p"].position == pos], start=1):
            rank_in[r["p"].id] = (i, sum(1 for x in pool if x["p"].position == pos))
    by_id = {r["p"].id: r for r in pool}
    page_ids = set(owner_of) | set(shown)
    player_pages = {}
    for pid in sorted(page_ids):
        p = players[pid]
        r = by_id[pid]
        hist = history(pid)
        if p.position == "G":
            stats = [
                ["GP", len(hist)],
                ["W", sum(1 for e in hist if e["decision"] == "W")],
                ["SV", sum(e["sv"] for e in hist)],
                ["GA", sum(e["ga"] for e in hist)],
                ["SO", sum(1 for e in hist if e["so"])],
            ]
        else:
            stats = [
                ["GP", len(hist)],
                ["G", sum(e["g"] for e in hist)],
                ["A", sum(e["a"] for e in hist)],
                ["PTS", sum(e["g"] + e["a"] for e in hist)],
                ["SOG", sum(e["sog"] for e in hist)],
                ["BLK", sum(e["blk"] for e in hist)],
            ]
        stats += [["FP", round(r["total"], 1)], ["FP/G", round(r["fpg"], 1)]]
        weekly = []
        for i, monday in enumerate(mondays[:weeks_played], start=1):
            wk = [e for e in hist if monday_of(e["game"].start_utc) == monday]
            last_day = monday + timedelta(days=6)
            if not wk:
                line = "No games"
            elif p.position == "G":
                line = f"{sum(e['sv'] for e in wk)} SV · {sum(e['ga'] for e in wk)} GA"
            else:
                line = (
                    f"{sum(e['g'] for e in wk)}G {sum(e['a'] for e in wk)}A · "
                    f"{sum(e['sog'] for e in wk)} SOG"
                )
            weekly.append(
                {
                    "week": i,
                    "range": f"{monday:%b} {monday.day} to {last_day:%b} {last_day.day}",
                    "gp": len(wk),
                    "fp": round(float(sum(e["pts"] for e in wk)), 1),
                    "line": line,
                }
            )
        log = []
        for e in reversed(hist[-5:]):
            g = e["game"]
            home = p.pro_team_id == g.home_team_id
            opp = code(g.away_team_id if home else g.home_team_id)
            log.append(
                {
                    "date": fmt_day(g.start_utc),
                    "opp": f"{'vs' if home else 'at'} {opp}",
                    "line": e["text"],
                    "pts": round(float(e["pts"]), 1),
                }
            )
        place, of = rank_in[pid]
        slug = owner_of.get(pid)
        player_pages[str(pid)] = {
            "id": pid,
            "name": f"{p.first_name} {p.last_name}",
            "pos": p.position,
            "team": code(p.pro_team_id),
            "team_name": teams[p.pro_team_id].name if p.pro_team_id in teams else "",
            "jersey": p.jersey,
            "stats": stats,
            "rank": f"{ordinal(place)} of {of} {POS_PLURAL[p.position]} in FP/G",
            "trend": r["why"],
            "owner": team_name.get(slug) if slug else None,
            "mine": slug == MY_SLUG,
            "weekly": weekly,
            "log": log,
        }

    my_forwards = [p for p in rosters[MY_SLUG]["F"]]
    featured = max(my_forwards, key=lambda p: by_id[p.id]["fpg"]).id
    last_day = end - timedelta(days=1)
    return {
        "source": f"2025-26 PWHL regular season (HockeyTech season {season_id})",
        "league": {
            "name": "League One",
            "week": week_no,
            "weeks": 18,
            "range": f"{start:%b} {start.day} to {last_day:%b} {last_day.day}",
        },
        "my_team": MY_SLUG,
        "featured_player": featured,
        "teams": teams_out,
        "matchups": [{"id": i, "a": a, "b": b} for i, (a, b) in enumerate(MATCHUPS, start=1)],
        "players": players_tab,
        "player_pages": player_pages,
        "standings": standings_out,
        "activity": [
            ["Today 9:12 AM", f"Jordan added {free[0][0]} ({free[0][1]})."],
            ["Today 8:40 AM", f"Sam claimed {free[1][0]} ({free[1][1]}) off waivers."],
            ["Yesterday", "Taylor and Casey proposed a trade. Commissioner review open."],
            ["Yesterday", f"You set {teams_out[MY_SLUG]['lineup'][0][1]['name']} in your lineup."],
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=8)
    args = parser.parse_args()
    data = build(args.season)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    print("week:", data["league"]["week"], data["league"]["range"])
    print("featured player page:", data["player_pages"][str(data["featured_player"])]["name"])
    for slug in (MY_SLUG, "rink-rats"):
        t = data["teams"][slug]
        print(f"{t['name']}: {t['score']} (bench {t['bench_score']})")
        for slot, p in t["lineup"]:
            name, team, status = p["name"], p["team"], p["status"]
            print(f"  {slot:5} {name:24} {team:4} {status:5} {p['pts']} | {p['line']}")
        for p in t["bench"]:
            print(f"  BN    {p['name']:24} {p['team']:4} {p['pts']} {p.get('flag', '')}")


if __name__ == "__main__":
    main()
