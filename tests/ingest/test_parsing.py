from datetime import UTC, datetime
from pathlib import Path

from undertow.ingest.dto import GameStatus, Position, ResultType
from undertow.ingest.parsing import (
    clock_to_seconds,
    map_position,
    parse_box_score,
    parse_result_type,
    parse_roster,
    parse_schedule,
    parse_seasons,
    parse_teams,
    strip_jsonp,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "hockeytech"


def load(name: str):
    return strip_jsonp((FIXTURES / name).read_text())


def test_strip_jsonp_handles_both_shapes():
    assert strip_jsonp('({"a": 1})') == {"a": 1}
    assert strip_jsonp(' {"a": 1} ') == {"a": 1}


def test_clock_to_seconds():
    assert clock_to_seconds("24:46") == 1486
    assert clock_to_seconds("65:00") == 3900
    assert clock_to_seconds(None) == 0
    assert clock_to_seconds("") == 0


def test_position_mapping_is_f_d_g_only():
    for raw in ("C", "LW", "RW", "F"):
        assert map_position(raw) is Position.F
    for raw in ("LD", "RD", "D"):
        assert map_position(raw) is Position.D
    assert map_position("G") is Position.G
    assert map_position("X") is None


def test_seasons():
    by_id = {s.id: s for s in parse_seasons(load("seasons.json"))}
    assert by_id[11].name == "2026-27 Regular Season"
    assert not by_id[11].is_playoff
    assert by_id[9].is_playoff
    assert by_id[8].id == 8


def test_teams_use_feed_ids_and_codes():
    teams = {t.code: t for t in parse_teams(load("teams_2026-27.json"))}
    assert len(teams) == 12
    assert teams["VGS"].id == 12  # Las Vegas is VGS in the feed, not LV
    assert teams["BOS"].id == 1


def test_roster_maps_positions_and_skips_non_players():
    players = parse_roster(load("roster_bos_2026-27-pre.json"))
    assert players
    assert {p.position for p in players} <= {Position.F, Position.D, Position.G}
    assert any(p.raw_position == "LW" and p.position is Position.F for p in players)
    assert all(p.team_id == 1 for p in players)


def test_schedule_status_and_result_types_2025_26():
    games = parse_schedule(load("schedule_2025-26.json"))
    assert len(games) == 120
    assert all(g.status is GameStatus.FINAL for g in games)
    counts = {r: sum(g.result_type is r for g in games) for r in ResultType}
    assert counts == {ResultType.REG: 90, ResultType.OT: 20, ResultType.SO: 10}


def test_schedule_start_time_uses_iso_with_offset_not_mislabelled_z():
    games = {g.id: g for g in parse_schedule(load("schedule_2025-26.json"))}
    # 7:00 pm EST on Nov 21, 2025 is 00:00 UTC Nov 22. date_time_played says 19:00Z (wrong).
    assert games[210].start_utc == datetime(2025, 11, 22, 0, 0, tzinfo=UTC)


def test_schedule_2026_27_all_scheduled():
    games = parse_schedule(load("schedule_2026-27.json"))
    assert len(games) == 180
    assert all(g.status is GameStatus.SCHEDULED and g.result_type is None for g in games)


def test_box_score_shootout_game():
    box = parse_box_score(load("gamesummary_227_so.json"))
    assert box.status is GameStatus.FINAL
    assert box.result_type is ResultType.SO
    goalies = {g.player_id: g for g in box.goalies}
    mtl, tor = goalies[28], goalies[211]
    assert (mtl.decision, mtl.shots_against, mtl.goals_against, mtl.saves) == ("W", 36, 1, 35)
    assert (tor.decision, tor.shots_against, tor.goals_against, tor.saves) == ("SOL", 31, 1, 30)
    assert mtl.toi_seconds == tor.toi_seconds == 3900
    # Backups did not play and have no decision.
    assert goalies[85].toi_seconds == 0 and goalies[85].decision is None
    # Shootout goals are excluded from skater goals: only regulation/OT goals count.
    assert sum(s.goals for s in box.skaters) == 2


def test_box_score_goalie_decision_is_one_per_goalie_even_with_many_stints():
    box = parse_box_score(load("gamesummary_210_reg.json"))
    assert box.result_type is ResultType.REG
    winners = [g for g in box.goalies if g.decision == "W"]
    assert [g.player_id for g in winners] == [211]
    assert len([g for g in box.goalies if g.player_id == 211]) == 1


def test_box_score_special_teams_and_gwg_from_goal_list():
    raw = load("gamesummary_210_reg.json")
    box = parse_box_score(raw)
    goals = [g for p in raw["periods"] for g in p["goals"]]
    expected_pp = sum(g["properties"]["isPowerPlay"] == "1" for g in goals)
    assert sum(s.pp_goals for s in box.skaters) == expected_pp
    expected_gwg = sum(g["properties"]["isGameWinningGoal"] == "1" for g in goals)
    assert sum(s.gwg for s in box.skaters) == expected_gwg == 1


def test_shutout_requires_sole_goalie_and_zero_goals_against():
    raw = load("gamesummary_210_reg.json")
    # Make the winning goalie's line a clean sheet and confirm the derivation.
    for g in raw["visitingTeam"]["goalies"]:
        if g["info"]["id"] == 211:
            g["stats"]["goalsAgainst"] = 0
    box = parse_box_score(raw)
    assert {g.player_id for g in box.goalies if g.shutout} == {211}
    assert not any(g.shutout for g in box.goalies if g.team_id != 6)


def test_result_type_matches_schedule_and_box_score_spellings():
    # The box score says "Final OT1" where the schedule says "Final OT" (found by season replay).
    cases = {
        "Final": ResultType.REG,
        "Final OT": ResultType.OT,
        "Final OT1": ResultType.OT,
        "Final 2OT": ResultType.OT,
        "Final SO": ResultType.SO,
        "3:00 pm EST": None,
        "Final PP": None,
    }
    for text, expected in cases.items():
        assert parse_result_type(text) is expected, text


def test_empty_net_goals_are_recorded_per_scorer():
    raw = load("gamesummary_210_reg.json")
    box = parse_box_score(raw)
    goals = [g for p in raw["periods"] for g in p["goals"]]
    assert sum(s.en_goals for s in box.skaters) == sum(
        g["properties"]["isEmptyNet"] == "1" for g in goals
    )


def test_empty_goalie_result_means_no_decision():
    from undertow.ingest.parsing import _combine_decisions

    assert _combine_decisions(["", ""]) is None
    assert _combine_decisions([]) is None
    assert _combine_decisions(["", "L"]) == "L"
