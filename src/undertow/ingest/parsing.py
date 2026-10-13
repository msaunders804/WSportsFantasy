"""Pure parsers: raw HockeyTech JSON -> DTOs. No I/O, so they are tested against fixtures.

Quirks handled here (see docs/feed.md): statviewfeed responses are JSONP-wrapped in
parentheses; modulekit returns every number as a string; positions are granular
(C/LW/RW/LD/RD) and are mapped to F/D/G; the schedule's `date_time_played` is local time
mislabelled as Z, so `GameDateISO8601` is the authoritative start time.
"""

import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from undertow.ingest.dto import (
    BoxScoreDTO,
    GameDTO,
    GameStatus,
    GoalieLineDTO,
    PlayerDTO,
    Position,
    ResultType,
    SeasonDTO,
    SkaterLineDTO,
    TeamDTO,
)

log = logging.getLogger(__name__)

POSITION_MAP = {
    "C": Position.F,
    "LW": Position.F,
    "RW": Position.F,
    "F": Position.F,
    "LD": Position.D,
    "RD": Position.D,
    "D": Position.D,
    "G": Position.G,
}

STATUS_TEXT_TO_RESULT = {
    "Final": ResultType.REG,
    "Final OT": ResultType.OT,
    "Final SO": ResultType.SO,
}


def strip_jsonp(text: str) -> Any:
    """Parse a body that is either plain JSON or JSON wrapped in `( ... )`."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return json.loads(text)


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(value)


def to_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_date(value: Any) -> date | None:
    if not value or value == "0000-00-00":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def clock_to_seconds(value: Any) -> int:
    """'24:46' -> 1486. None/'' -> 0."""
    if not value:
        return 0
    minutes, _, seconds = str(value).partition(":")
    return int(minutes) * 60 + int(seconds or 0)


def map_position(raw: str | None) -> Position | None:
    return POSITION_MAP.get((raw or "").strip().upper())


def parse_seasons(payload: dict) -> list[SeasonDTO]:
    return [
        SeasonDTO(
            id=int(s["season_id"]),
            name=s["season_name"],
            start=to_date(s.get("start_date")),
            end=to_date(s.get("end_date")),
            is_playoff=s.get("playoff") == "1",
            is_career=s.get("career") == "1",
        )
        for s in payload["SiteKit"]["Seasons"]
    ]


def parse_teams(payload: dict) -> list[TeamDTO]:
    return [
        TeamDTO(
            id=int(t["id"]),
            name=t["name"],
            code=t["code"],
            city=t.get("city", ""),
            nickname=t.get("nickname", ""),
            logo_url=t.get("team_logo_url", ""),
        )
        for t in payload["SiteKit"]["Teamsbyseason"]
    ]


def parse_roster(payload: dict) -> list[PlayerDTO]:
    players = []
    for p in payload["SiteKit"]["Roster"]:
        if not isinstance(p, dict) or not p.get("player_id"):
            continue  # the feed sometimes appends a non-player entry
        raw_pos = p.get("position", "")
        position = map_position(raw_pos)
        if position is None:
            log.warning("unmapped position %r for player %s", raw_pos, p["player_id"])
        players.append(
            PlayerDTO(
                id=int(p["player_id"]),
                person_id=to_int_or_none(p.get("person_id")),
                first_name=p.get("first_name", ""),
                last_name=p.get("last_name", ""),
                position=position,
                raw_position=raw_pos,
                team_id=to_int_or_none(p.get("team_id")),
                jersey=to_int_or_none(p.get("tp_jersey_number")),
                feed_status=p.get("status", ""),
                shoots=p.get("shoots", ""),
                birthdate=to_date(p.get("birthdate")),
            )
        )
    return players


def parse_result_type(status_text: str) -> ResultType | None:
    """'Final' -> REG, 'Final OT' / 'Final OT1' / 'Final 2OT' -> OT, 'Final SO' -> SO.

    The schedule says 'Final OT' but box scores say 'Final OT1', so match loosely. Returns
    None for text that is not a recognised final status.
    """
    text = status_text.strip().upper()
    if not text.startswith("FINAL"):
        return None
    rest = text[len("FINAL") :].strip()
    if not rest:
        return ResultType.REG
    if "SO" in rest:
        return ResultType.SO
    if "OT" in rest:
        return ResultType.OT
    return None


def parse_game_status(
    status_text: str, started: bool, final: bool
) -> tuple[GameStatus, ResultType | None]:
    if final:
        result = parse_result_type(status_text)
        if result is None:
            log.warning("unrecognised final status text %r; treating as regulation", status_text)
            result = ResultType.REG
        return GameStatus.FINAL, result
    if started:
        return GameStatus.LIVE, None
    return GameStatus.SCHEDULED, None


def parse_schedule(payload: dict) -> list[GameDTO]:
    games = []
    for g in payload["SiteKit"]["Schedule"]:
        final = g.get("final") == "1"
        started = g.get("started") == "1"
        status, result = parse_game_status(g.get("game_status", ""), started, final)
        start = datetime.fromisoformat(g["GameDateISO8601"]).astimezone(UTC)
        games.append(
            GameDTO(
                id=int(g["game_id"]),
                season_id=int(g["season_id"]),
                home_team_id=int(g["home_team"]),
                away_team_id=int(g["visiting_team"]),
                start_utc=start,
                status=status,
                status_text=g.get("game_status", ""),
                result_type=result,
                home_goals=to_int(g.get("home_goal_count")),
                away_goals=to_int(g.get("visiting_goal_count")),
                venue_name=g.get("venue_name", ""),
                venue_location=g.get("venue_location", ""),
                notes=g.get("schedule_notes", ""),
                last_modified=g.get("last_modified", ""),
            )
        )
    return games


def _player_from_info(info: dict, team_id: int) -> PlayerDTO:
    raw_pos = info.get("position", "")
    return PlayerDTO(
        id=int(info["id"]),
        person_id=None,
        first_name=info.get("firstName", ""),
        last_name=info.get("lastName", ""),
        position=map_position(raw_pos),
        raw_position=raw_pos,
        team_id=team_id,
        jersey=to_int_or_none(info.get("jerseyNumber")),
        feed_status="",
        shoots="",
        birthdate=to_date(info.get("birthDate")),
    )


def _combine_decisions(results: list[str]) -> str | None:
    """One decision per (game, goalie): a goalie's stints may repeat the same result."""
    for preferred in ("W", "OTL", "SOL", "L"):
        if preferred in results:
            return preferred
    # A relief goalie who is neither winner nor loser has an empty result: no decision.
    return next((r for r in results if r), None)


def _special_teams_and_gwg(summary: dict) -> dict[int, dict[str, int]]:
    """Per-player pp/sh goals and assists, GWG and empty-net goals, derived from the goal list.

    Skater box lines carry no PP/SH/GWG fields. Shootout goals are not in `periods`, so they
    are excluded automatically.
    """
    extra: dict[int, dict[str, int]] = {}

    def bump(player_id: int, key: str) -> None:
        extra.setdefault(player_id, {}).setdefault(key, 0)
        extra[player_id][key] += 1

    for period in summary.get("periods", []):
        for goal in period.get("goals", []):
            props = goal.get("properties", {})
            scorer = goal.get("scoredBy") or {}
            assists = goal.get("assists") or []
            if props.get("isPowerPlay") == "1":
                if scorer.get("id"):
                    bump(int(scorer["id"]), "pp_goals")
                for a in assists:
                    bump(int(a["id"]), "pp_assists")
            if props.get("isShortHanded") == "1":
                if scorer.get("id"):
                    bump(int(scorer["id"]), "sh_goals")
                for a in assists:
                    bump(int(a["id"]), "sh_assists")
            if props.get("isEmptyNet") == "1" and scorer.get("id"):
                bump(int(scorer["id"]), "en_goals")
            if props.get("isGameWinningGoal") == "1" and scorer.get("id"):
                bump(int(scorer["id"]), "gwg")
    return extra


def parse_box_score(summary: dict) -> BoxScoreDTO:
    """Parse a `statviewfeed gameSummary` payload (already JSON-decoded)."""
    details = summary["details"]
    status_text = details.get("status", "")
    status, result = parse_game_status(
        status_text, details.get("started") == "1", details.get("final") == "1"
    )
    extra = _special_teams_and_gwg(summary)

    skaters: list[SkaterLineDTO] = []
    goalies: list[GoalieLineDTO] = []
    players: dict[int, PlayerDTO] = {}

    for side in ("homeTeam", "visitingTeam"):
        team = summary[side]
        team_id = int(team["info"]["id"])

        for s in team.get("skaters", []):
            st = s["stats"]
            pid = int(s["info"]["id"])
            players[pid] = _player_from_info(s["info"], team_id)
            skaters.append(
                SkaterLineDTO(
                    player_id=pid,
                    team_id=team_id,
                    position=map_position(s["info"].get("position")),
                    goals=to_int(st.get("goals")),
                    assists=to_int(st.get("assists")),
                    shots=to_int(st.get("shots")),
                    hits=to_int(st.get("hits")),
                    blocks=to_int(st.get("blockedShots")),
                    penalty_minutes=to_int(st.get("penaltyMinutes")),
                    plus_minus=to_int(st.get("plusMinus")),
                    toi_seconds=clock_to_seconds(st.get("toi")),
                    faceoff_wins=to_int(st.get("faceoffWins")),
                    faceoff_attempts=to_int(st.get("faceoffAttempts")),
                    **extra.get(pid, {}),
                )
            )

        results_by_goalie: dict[int, list[str]] = {}
        for entry in team.get("goalieLog", []):
            results_by_goalie.setdefault(int(entry["info"]["id"]), []).append(
                entry.get("result", "")
            )

        team_goalies = team.get("goalies", [])
        played = [g for g in team_goalies if clock_to_seconds(g["stats"].get("timeOnIce")) > 0]
        for g in team_goalies:
            st = g["stats"]
            pid = int(g["info"]["id"])
            players[pid] = _player_from_info(g["info"], team_id)
            toi = clock_to_seconds(st.get("timeOnIce"))
            ga = to_int(st.get("goalsAgainst"))
            goalies.append(
                GoalieLineDTO(
                    player_id=pid,
                    team_id=team_id,
                    toi_seconds=toi,
                    shots_against=to_int(st.get("shotsAgainst")),
                    goals_against=ga,
                    saves=to_int(st.get("saves")),
                    decision=_combine_decisions(results_by_goalie.get(pid, [])),
                    # Shutout: the only goalie to play for the team, with nothing allowed.
                    shutout=toi > 0 and ga == 0 and len(played) == 1,
                    started=g.get("starting") == 1,
                )
            )

    return BoxScoreDTO(
        game_id=int(details["id"]),
        season_id=int(details["seasonId"]),
        status=status,
        status_text=status_text,
        result_type=result,
        skaters=skaters,
        goalies=goalies,
        players=list(players.values()),
    )
