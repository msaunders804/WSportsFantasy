import pytest

from undertow.fantasy import draft as d
from undertow.web import preview_data

CFG = d.DraftConfig()
POOL = [
    d.DraftPlayer(p["id"], p["name"], p["pos"], p["team"], p["fpg"], p["gp"], p["total"])
    for p in preview_data.DRAFT["pool"]
]
VALS = d.valuations(POOL, CFG)


def run_cpu_draft(seed: int, me_auto: int | None = None) -> d.DraftState:
    picks: list[int] = []
    for _ in range(CFG.total_picks):
        state = d.replay(picks, POOL, CFG)
        if me_auto is not None and state.team == me_auto:
            pick = d.recommend(state, state.team, VALS, CFG, n=1)[0].player
        else:
            pick = d.cpu_pick(state, state.team, VALS, CFG, seed=seed)
        picks.append(pick.id)
    return d.replay(picks, POOL, CFG)


def test_pool_is_big_enough_for_the_draft():
    counts = {pos: sum(1 for p in POOL if p.pos == pos) for pos in "FDG"}
    assert counts["F"] >= CFG.need_f * CFG.teams
    assert counts["D"] >= CFG.need_d * CFG.teams
    assert counts["G"] >= CFG.need_g * CFG.teams
    assert len(POOL) >= CFG.total_picks


def test_snake_order():
    assert d.snake_slot(1, 10) == (1, 0)
    assert d.snake_slot(10, 10) == (1, 9)
    assert d.snake_slot(11, 10) == (2, 9)  # direction reverses
    assert d.snake_slot(20, 10) == (2, 0)
    assert d.snake_slot(21, 10) == (3, 0)
    assert d.snake_slot(101, 10) == (11, 0)  # odd rounds run forward again
    assert d.snake_slot(110, 10) == (11, 9)
    # Every team gets exactly one pick per round.
    for rnd in range(1, 12):
        teams = [d.snake_slot((rnd - 1) * 10 + i, 10)[1] for i in range(1, 11)]
        assert sorted(teams) == list(range(10))


def test_valuations_rank_everyone_once_and_best_is_positive():
    assert sorted(v.rank for v in VALS.values()) == list(range(1, len(POOL) + 1))
    best = max(VALS.values(), key=lambda v: v.vorp)
    assert best.rank == 1 and best.vorp > 0
    # Small samples are pulled toward the position average, not left to top the list.
    top10 = sorted(POOL, key=lambda p: VALS[p.id].rank)[:10]
    assert all(p.gp >= 8 for p in top10)


def test_needs_and_slots():
    p = lambda i, pos: d.DraftPlayer(i, f"P{i}", pos, "BOS", 1.0, 10, 10.0)  # noqa: E731
    roster = [p(1, "F"), p(2, "F"), p(3, "D"), p(4, "G")]
    assert d.needs(roster, CFG) == {"F": 2, "D": 1, "G": 0}
    full = [p(i, "F") for i in range(1, 6)] + [p(10, "D"), p(11, "D"), p(12, "D"), p(13, "G")]
    slots = d.assign_slots(full, CFG)
    assert [x.id for x in slots["F"]] == [1, 2, 3]
    assert [x.id for x in slots["UTIL"]] == [4]  # the fourth forward takes the flex spot
    assert [x.id for x in slots["BN"]] == [5, 12]  # fifth forward and third defender
    assert [x.id for x in slots["G"]] == [13]


def test_replay_validates_picks():
    first = POOL[0].id
    with pytest.raises(ValueError, match="already"):
        d.replay([first, first], POOL, CFG)
    with pytest.raises(ValueError, match="unknown"):
        d.replay([-5], POOL, CFG)
    with pytest.raises(ValueError, match="more picks"):
        d.replay([p.id for p in POOL[: CFG.total_picks]] + [POOL[-1].id], POOL, CFG)
    state = d.replay([], POOL, CFG)
    assert (state.pick_no, state.round, state.team) == (1, 1, 0)
    state = d.replay([POOL[0].id, POOL[1].id], POOL, CFG)
    assert (state.pick_no, state.team) == (3, 2)
    assert POOL[0] not in state.available


@pytest.mark.parametrize("seed", range(6))
def test_computer_drafts_produce_valid_balanced_rosters(seed):
    final = run_cpu_draft(seed)
    assert final.complete
    assert len(set(final.picks)) == CFG.total_picks
    for roster in final.rosters:
        pos = [p.pos for p in roster]
        assert len(roster) == CFG.rounds
        assert pos.count("F") >= CFG.need_f
        assert pos.count("D") >= CFG.need_d
        assert 1 <= pos.count("G") <= CFG.max_goalies


def test_computer_draft_is_deterministic_and_varies_by_seed():
    assert run_cpu_draft(1).picks == run_cpu_draft(1).picks
    assert run_cpu_draft(1).picks != run_cpu_draft(2).picks


def test_early_picks_are_good_players_and_goalies_go_early():
    final = run_cpu_draft(3)
    first_round = final.picks[:10]
    assert all(VALS[pid].rank <= 30 for pid in first_round)
    # Under this scoring the best goalies are worth the most, so some go in the first rounds.
    by_id = {p.id: p for p in POOL}
    early_goalies = [pid for pid in final.picks[:30] if by_id[pid].pos == "G"]
    assert len(early_goalies) >= 3


def test_user_following_recommendations_ends_with_a_valid_team():
    final = run_cpu_draft(4, me_auto=3)
    roster = final.rosters[3]
    pos = [p.pos for p in roster]
    assert pos.count("F") >= 4 and pos.count("D") >= 2 and 1 <= pos.count("G") <= 2


def test_recommendations_have_reasons_and_respect_limits():
    state = d.replay([], POOL, CFG)
    recs = d.recommend(state, 0, VALS, CFG, n=3)
    assert len(recs) == 3
    assert recs[0].tag == "Best pick"
    assert all(r.reason and r.player in state.available for r in recs)
    assert recs[0].player.id == min(POOL, key=lambda p: VALS[p.id].rank).id


def _state_with_roster(roster):
    taken = {p.id for p in roster}
    return d.DraftState(
        picks=[p.id for p in roster],
        rosters=[roster] + [[] for _ in range(CFG.teams - 1)],
        available=[p for p in POOL if p.id not in taken],
        pick_no=1,
        round=1,
        team=0,
    )


def test_never_recommends_a_third_goalie():
    goalies = [p for p in POOL if p.pos == "G"][:2]
    forwards = [p for p in POOL if p.pos == "F"][:4]
    state = _state_with_roster(goalies + forwards)
    recs = d.recommend(state, 0, VALS, CFG, n=5)
    assert recs and all(r.player.pos != "G" for r in recs)


def test_last_picks_must_fill_missing_starting_spots():
    # Ten players in, with 4 forwards and 2 defenders but no goalie: one pick left, it must be a G.
    fs = [p for p in POOL if p.pos == "F"][:6]
    ds = [p for p in POOL if p.pos == "D"][:4]
    state = _state_with_roster(fs + ds)
    recs = d.recommend(state, 0, VALS, CFG, n=3)
    assert recs and all(r.player.pos == "G" for r in recs)
    assert d.cpu_pick(state, 0, VALS, CFG).pos == "G"


def test_scarce_goalies_are_flagged_and_taken():
    goalies = [p for p in POOL if p.pos == "G"]
    # Everyone else has a goalie except team 0 and one other; only 2 goalies left in the pool.
    others = [[goalies[i]] for i in range(1, 9)]
    left = goalies[9:11]
    taken = {g.id for r in others for g in r}
    state = d.DraftState(
        picks=list(taken),
        rosters=[[]] + others + [[]],
        available=[p for p in POOL if p.id not in taken and (p.pos != "G" or p in left)],
        pick_no=1,
        round=1,
        team=0,
    )
    rec = d.recommend(state, 0, VALS, CFG, n=1)[0]
    assert rec.player.pos == "G"
    assert "running out" in rec.reason
