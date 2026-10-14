import json
from decimal import Decimal
from fractions import Fraction as F
from pathlib import Path

import pytest

from undertow.scoring import (
    Decision,
    GoalieStatLine,
    ResultType,
    ScoringConfig,
    SkaterStatLine,
    score_goalie,
    score_skater,
)

CFG = ScoringConfig()
FIXTURE = Path(__file__).parent.parent / "fixtures" / "hockeytech" / "gamesummary_227_so.json"


def g(**kw) -> GoalieStatLine:
    kw.setdefault("toi_seconds", 3600)
    return GoalieStatLine(**kw)


def test_skater_golden():
    line = SkaterStatLine(goals=1, assists=1, pp_assists=1, shots=5, hits=3, blocks=2)
    r = score_skater(line, CFG)
    # goal 2 + assist 1 + pp 0.5 + shots 0.5 + hits 0.3 + blocks 1 = 5.3
    assert r.total == F(53, 10)
    assert r.breakdown == {
        "goal": F(2),
        "assist": F(1),
        "pp_point": F(1, 2),
        "shot": F(1, 2),
        "hit": F(3, 10),
        "block": F(1),
    }


def test_skater_sh_and_empty():
    r = score_skater(SkaterStatLine(goals=1, sh_goals=1), CFG)
    assert r.breakdown == {"goal": F(2), "sh_point": F(1, 2)}
    empty = score_skater(SkaterStatLine(), CFG)
    assert empty.total == 0 and empty.breakdown == {}


def test_goalie_reg_win_shutout():
    r = score_goalie(g(shots_against=25, saves=25, decision=Decision.W, shutout=True), CFG)
    assert r.total == 12  # win 4 + shutout 3 + 25 saves * 0.2
    assert r.breakdown == {"save": F(5), "shutout": F(3), "win": F(4)}


def test_goalie_so_win_exact():
    r = score_goalie(g(decision=Decision.W, result_type=ResultType.SO), CFG)
    assert r.total == F(8, 3)
    assert r.breakdown == {"ot_win": F(8, 3)}


def test_goalie_ot_win():
    line = g(saves=20, goals_against=2, decision=Decision.W, result_type=ResultType.OT)
    assert score_goalie(line, CFG).total == F(8, 3)  # 8/3 + 4 - 4


def test_win_points_change_keeps_multiplier():
    cfg = ScoringConfig(win=5)
    r = score_goalie(g(decision=Decision.W, result_type=ResultType.OT), cfg)
    assert r.total == F(10, 3)


@pytest.mark.parametrize("d", [Decision.OTL, Decision.SOL])
def test_ot_and_so_loss(d):
    r = score_goalie(g(decision=d, result_type=ResultType.OT), CFG)
    assert r.total == 1 and r.breakdown == {"ot_loss": F(1)}


def test_goalie_loss_many_saves():
    r = score_goalie(g(shots_against=40, saves=36, goals_against=4, decision=Decision.L), CFG)
    assert r.total == F(-4, 5)  # 7.2 - 8
    assert "ot_loss" not in r.breakdown


def test_goalie_no_decision_played():
    assert score_goalie(g(saves=10, goals_against=1), CFG).total == 0  # 2 - 2


def test_goalie_did_not_play():
    line = GoalieStatLine(decision=Decision.W, shutout=True)
    assert not line.played
    r = score_goalie(line, CFG)
    assert r.total == 0 and r.breakdown == {}


def test_disabled_stats():
    cfg = ScoringConfig(hit=0, block="0")
    r = score_skater(SkaterStatLine(goals=1, hits=9, blocks=9, shots=10), cfg)
    assert r.breakdown == {"goal": F(2), "shot": F(1)}


def test_config_accepts_mixed_types_exactly():
    cfg = ScoringConfig(shot="0.1", hit=Decimal("0.1"), block=0.5, goal=2, assist="1/2")
    assert cfg.shot == cfg.hit == F(1, 10)
    assert cfg.block == F(1, 2) and cfg.assist == F(1, 2)
    with pytest.raises(TypeError):
        ScoringConfig(goal=True)


def test_hash_stable_and_sensitive():
    assert ScoringConfig().hash() == ScoringConfig().hash()
    assert ScoringConfig(shot="0.1").hash() == ScoringConfig().hash()
    assert ScoringConfig(win=5).hash() != ScoringConfig().hash()
    assert len(ScoringConfig().hash()) == 16


def test_dict_round_trip():
    cfg = ScoringConfig(win="4.5", save="0.25")
    data = json.loads(json.dumps(cfg.to_dict()))
    again = ScoringConfig.from_dict(data)
    assert again == cfg and again.hash() == cfg.hash()
    assert ScoringConfig.from_dict(ScoringConfig().to_dict()) == ScoringConfig()


def test_from_dict_absent_is_unscored_and_unknown_rejected():
    cfg = ScoringConfig.from_dict({"goal": 3})
    assert cfg.goal == 3 and cfg.hit == 0 and cfg.ot_win_multiplier == 0
    with pytest.raises(ValueError):
        ScoringConfig.from_dict({"goalz": 1})


def test_display_rounding_and_exact_tie_comparison():
    r = score_goalie(g(decision=Decision.W, result_type=ResultType.SO), CFG)
    assert r.total_decimal() == Decimal("2.67")
    assert r.total_decimal(3) == Decimal("2.667")
    half = score_skater(SkaterStatLine(shots=1), ScoringConfig(shot="0.005"))
    assert half.total_decimal() == Decimal("0.01")  # ROUND_HALF_UP
    a = score_skater(SkaterStatLine(goals=1), ScoringConfig(goal="2.001"))
    b = score_skater(SkaterStatLine(goals=1), ScoringConfig(goal="2.004"))
    assert a.total_decimal() == b.total_decimal() == Decimal("2.00")
    assert a.total != b.total and a.total < b.total


def _fixture() -> dict:
    text = FIXTURE.read_text()
    return json.loads(text[1:-1])  # strip JSONP parentheses


def _goalie_line(team: dict, goalie_id: int) -> GoalieStatLine:
    entry = next(x for x in team["goalieLog"] if x["info"]["id"] == goalie_id)
    s = entry["stats"]
    mins, secs = s["timeOnIce"].split(":")
    return GoalieStatLine(
        toi_seconds=int(mins) * 60 + int(secs),
        shots_against=s["shotsAgainst"],
        goals_against=s["goalsAgainst"],
        saves=s["saves"],
        decision=Decision(entry["result"]),
        result_type=ResultType.SO,  # fixture game went to a shootout
        shutout=s["goalsAgainst"] == 0,
    )


def test_fixture_so_game_goalies():
    d = _fixture()
    assert d["hasShootout"]
    mtl = _goalie_line(d["visitingTeam"], 28)
    tor = _goalie_line(d["homeTeam"], 211)
    assert (mtl.shots_against, mtl.goals_against, mtl.saves, mtl.toi_seconds) == (36, 1, 35, 3900)
    assert (tor.shots_against, tor.goals_against, tor.saves) == (31, 1, 30)
    assert mtl.decision is Decision.W and tor.decision is Decision.SOL
    # MTL: 8/3 + 35*0.2 - 2 = 23/3 ; TOR: 1 + 30*0.2 - 2 = 5
    assert score_goalie(mtl, CFG).total == F(23, 3)
    assert score_goalie(tor, CFG).total == 5
