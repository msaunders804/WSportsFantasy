import random
from collections import Counter
from itertools import combinations

import pytest

from undertow.fantasy import schedule as sched

SIZES = [4, 6, 8, 10, 12]
WEEKS = [1, 3, 9, 11, 12, 17, 24]


def check(teams, weeks, plan):
    assert len(plan) == weeks
    meet = Counter()
    last_opponent: dict[int, int] = {}
    for week in plan:
        seen = [t for pair in week for t in pair]
        assert sorted(seen) == sorted(teams), "every team plays exactly once each week"
        for home, away in week:
            assert home != away
            meet[frozenset((home, away))] += 1
            for me, other in ((home, away), (away, home)):
                assert last_opponent.get(me) != other, "no repeat opponent in consecutive weeks"
                last_opponent[me] = other
    counts = [meet[frozenset(p)] for p in combinations(teams, 2)]
    return meet, counts


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("weeks", WEEKS)
def test_schedule_is_fair_for_many_seeds(size, weeks):
    teams = list(range(100, 100 + size))
    for seed in range(40):
        plan = sched.generate_matchups(teams, weeks, seed)
        _, counts = check(teams, weeks, plan)
        assert max(counts) - min(counts) <= 1, f"opponent counts uneven for seed {seed}"


def test_ten_teams_twelve_weeks_play_everyone_once_and_three_opponents_twice():
    teams = list(range(1, 11))
    for seed in range(60):
        plan = sched.generate_matchups(teams, 12, seed)
        meet, counts = check(teams, 12, plan)
        assert Counter(counts) == {1: 45 - 15, 2: 15}  # 15 pairs meet twice: 3 per team
        per_team = Counter()
        for pair, n in meet.items():
            if n == 2:
                per_team.update(pair)
        assert set(per_team.values()) == {3}  # every team repeats exactly three opponents


def test_same_seed_same_schedule_and_other_seeds_differ():
    teams = list(range(1, 11))
    a = sched.generate_matchups(teams, 12, 42)
    assert a == sched.generate_matchups(list(reversed(teams)), 12, 42)  # input order is irrelevant
    assert a != sched.generate_matchups(teams, 12, 43)
    assert len({tuple(map(tuple, sched.generate_matchups(teams, 12, s))) for s in range(20)}) == 20


def test_home_and_away_alternate_for_repeat_meetings():
    teams = list(range(1, 5))  # 4 teams: every pair meets 4 times in 12 weeks
    for seed in range(30):
        plan = sched.generate_matchups(teams, 12, seed)
        home = Counter()
        for week in plan:
            for h, a in week:
                home[(frozenset((h, a)), h)] += 1
        for pair in combinations(teams, 2):
            key = frozenset(pair)
            assert abs(home[(key, pair[0])] - home[(key, pair[1])]) <= 1


def test_randomised_leagues_never_break_the_rules():
    rng = random.Random(7)
    for _ in range(200):
        size = rng.choice(SIZES)
        weeks = rng.randint(1, 30)
        teams = rng.sample(range(1, 1000), size)
        plan = sched.generate_matchups(teams, weeks, rng.randrange(10**9))
        _, counts = check(teams, weeks, plan)
        assert max(counts) - min(counts) <= 1


@pytest.mark.parametrize("bad", [[], [1, 2], [1, 2, 3], [1, 2, 3, 4, 5], [1, 1, 2, 3]])
def test_bad_team_lists_are_rejected(bad):
    with pytest.raises(ValueError):
        sched.generate_matchups(bad, 5, 1)


def test_zero_weeks_is_rejected():
    with pytest.raises(ValueError):
        sched.generate_matchups([1, 2, 3, 4], 0, 1)
