"""Load the cached NCAA data, draft lists and training table into the `research` schema.

Replaces the contents of the four research tables (they are fully derived from local files), in
one transaction. Prints the target host first; a non-localhost database needs --allow-remote.
Run from the repo root with DATABASE_URL pointing at the target:

    uv run python analysis/ncaa_pilot/load_research.py [--allow-remote] [--dry-run]
"""

import argparse
import csv
import glob
import json
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from undertow.config import get_settings
from undertow.data.models import Player
from undertow.data.session import session_factory
from undertow.ingest import ncaa

HERE = Path(__file__).parent
CACHE = HERE / "data" / "ncaa_cache"


def file_date(f: Path):
    from datetime import date

    y, m, d = f.name.split("__")[3:6]
    return date(int(y), int(m), int(d))


def build_games_and_players():
    boxes = {
        Path(f).name.split("__")[1]: Path(f)
        for f in glob.glob(str(CACHE / "game__*__boxscore.json"))
    }
    games, players, seen = [], [], set()
    for f in sorted(CACHE.glob("scoreboard__*.json")):
        day = file_date(f)
        for g in json.loads(f.read_text()).get("games", []):
            game = ncaa.parse_scoreboard_game(g["game"], day)
            if game is None or str(game["game_id"]) not in boxes or game["game_id"] in seen:
                continue
            seen.add(game["game_id"])
            games.append(game)
            box = json.loads(boxes[str(game["game_id"])].read_text())
            rows = ncaa.parse_player_rows(box, game["game_id"])
            # the table's unique key is (game, team, jersey number); drop repeats of a number
            keys = set()
            for r in rows:
                k = (r["team_slug"], r["number"])
                if r["number"] is not None and k in keys:
                    continue
                keys.add(k)
                players.append(r)
    return games, players


def build_picks():
    picks = []
    for year in (2024, 2025, 2026):
        for r in csv.DictReader(open(HERE / f"data/draft_{year}.csv", encoding="utf-8")):
            picks.append(
                {
                    "draft_year": year,
                    "pick": int(r["pick"]),
                    "round": int(r["round"]),
                    "drafted_by": r["drafted_by"][:60],
                    "player": r["player"][:100],
                    "position": r["position"][:6],
                    "school_or_club": r["school_or_club"][:80],
                    "league": r["league_or_ncaa"][:20],
                }
            )
    return picks


def _n(v):
    return int(v) if v not in ("", None) else None


def build_training(session):
    ids = set(session.scalars(select(Player.id)))
    out, skipped = [], []
    for r in csv.DictReader(open(HERE / "data/training_table.csv", encoding="utf-8")):
        pid = int(r["player_id"])
        if pid not in ids:
            skipped.append(r["player"])
            continue
        row = {
            "draft_year": int(r["class"]),
            "pwhl_player_id": pid,
            "player": r["player"][:100],
            "position": r["pos"][:6],
            "kind": r["kind"],
            "draft_school": r["school"][:80],
            "pwhl_season_id": int(r["pwhl_season"]),
            "pwhl_gp": int(r["pwhl_gp"]),
            "pwhl_goals": int(r["pwhl_goals"]),
            "pwhl_assists": int(r["pwhl_assists"]),
            "pwhl_shots": int(r["pwhl_shots"]),
            "pwhl_toi_seconds": int(r["pwhl_toi_s"] or 0),
        }
        for tag in ("final", "prior"):
            row[f"{tag}_season"] = r[f"ncaa_{tag}_season"]
            row[f"{tag}_school_slug"] = r[f"ncaa_{tag}_school"] or None
            for col, src in (
                ("gp", "gp"),
                ("goals", "goals"),
                ("assists", "assists"),
                ("shots", "shots"),
                ("saves", "saves"),
                ("goals_allowed", "goalsAllowed"),
                ("goalie_minutes", "goalieMinutes"),
            ):
                row[f"{tag}_{col}"] = _n(r[f"ncaa_{tag}_{src}"])
        out.append(row)
    return out, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="build the rows, write nothing")
    a = ap.parse_args()
    host = urlparse(get_settings().database_url).hostname or "?"
    print(f"Target database host: {host}")
    if host not in ("localhost", "127.0.0.1") and not a.allow_remote:
        raise SystemExit("remote database: re-run with --allow-remote if this is intended")
    games, players = build_games_and_players()
    picks = build_picks()
    sf = session_factory()
    with sf() as session:
        training, skipped = build_training(session)
        print(f"rows: games={len(games)} player_games={len(players)}")
        print(f"      picks={len(picks)} training={len(training)}")
        if skipped:
            print("training rows skipped (player id not in public.player):", skipped)
        if a.dry_run:
            print("dry run: nothing written")
            return
        counts = ncaa.replace_research_tables(session, games, players, picks, training)
        session.commit()
    print("loaded:", counts)


if __name__ == "__main__":
    main()
