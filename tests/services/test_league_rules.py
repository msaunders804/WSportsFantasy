import pytest

from undertow.services import league_rules as rules
from undertow.services.league_rules import LeagueSettingsData


def test_default_settings_match_plan():
    s = LeagueSettingsData()
    assert (s.roster_f, s.roster_d, s.roster_util, s.roster_g) == (3, 2, 1, 1)
    assert s.starters == 7
    assert s.roster_size == 11  # 7 starters + 4 bench; IR is separate
    assert s.roster_ir == 2
    assert s.players_held == 13
    assert s.max_goalies == 2
    assert s.scoring_config["ot_win_multiplier"] == "2/3"


@pytest.mark.parametrize("size", [4, 6, 10, 12])
def test_valid_sizes(size):
    assert rules.validate_size(size) == []


@pytest.mark.parametrize("size", [0, 2, 3, 5, 9, 11, 13, 14, -4])
def test_invalid_sizes(size):
    assert rules.validate_size(size)


def test_default_ten_team_league_is_valid():
    result = rules.validate_settings(10, LeagueSettingsData(), active_pool=276)
    assert result.ok
    # 10 teams x 13 players (roster plus IR) = 130 of 276, under half the pool.
    assert not any("waiver wire" in w for w in result.warnings)


def test_warns_when_rosters_use_over_half_the_pool():
    result = rules.validate_settings(12, LeagueSettingsData(roster_bench=10), active_pool=200)
    assert result.ok
    assert any("waiver wire" in w for w in result.warnings)


def test_warns_when_goalie_slots_exceed_league_wide_starters():
    # 12 teams x 2 goalie slots = 24 starting goalies needed, about 12 exist.
    result = rules.validate_settings(12, LeagueSettingsData(roster_g=2, max_goalies=3))
    assert result.ok
    assert any("goalie" in w for w in result.warnings)


def test_ten_teams_with_one_goalie_slot_is_under_the_limit():
    result = rules.validate_settings(10, LeagueSettingsData())
    assert not any("starting goalies" in w for w in result.warnings)


@pytest.mark.parametrize(
    "changes",
    [
        {"roster_g": 0},
        {"roster_f": -1},
        {"roster_f": 99},
        {"max_goalies": 1, "roster_g": 2},
        {"trade_deadline_week": 13},  # after the 12-week regular season
        {"playoff_teams": 11},  # more than the 10 teams
        {"waiver_hours": -1},
    ],
)
def test_bad_settings_are_errors(changes):
    result = rules.validate_settings(10, LeagueSettingsData(**changes))
    assert not result.ok


def test_rejects_bool_and_non_int_values():
    assert not rules.validate_settings(10, LeagueSettingsData(roster_f=True)).ok
    assert not rules.validate_settings(10, LeagueSettingsData(roster_f=2.5)).ok  # type: ignore[arg-type]


def test_rejects_unknown_scoring_key():
    result = rules.validate_settings(10, LeagueSettingsData(scoring_config={"bogus": "1"}))
    assert not result.ok


def test_one_goalie_slot_minimum_means_there_is_always_a_starter():
    s = LeagueSettingsData(roster_f=0, roster_d=0, roster_util=0)
    # roster_g stays at its minimum of 1, so there is still a starting slot.
    assert rules.validate_settings(10, s).ok


def test_clean_name_collapses_whitespace_and_caps_length():
    assert rules.clean_name("  Ice   Queens \n", max_length=40) == "Ice Queens"
    assert len(rules.clean_name("x" * 100, max_length=40)) == 40


def test_ir_spots_are_separate_from_the_roster():
    s = LeagueSettingsData(roster_ir=4)
    assert s.roster_size == 11
    assert s.players_held == 15


def test_pool_warning_counts_players_on_ir():
    # 10 teams x 11 = 110 would fit under half of 250, but x 13 with IR does not.
    result = rules.validate_settings(10, LeagueSettingsData(), active_pool=250)
    assert any("waiver wire" in w for w in result.warnings)
    assert not rules.validate_settings(
        10, LeagueSettingsData(roster_ir=0), active_pool=250
    ).warnings
