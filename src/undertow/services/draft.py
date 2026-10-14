"""Draft logic: snake order, roster needs, player values, recommendations and computer picks.

Pure functions, no HTTP or database. This is the basic version used by the demo draft room:
values come from last season's points per game (shrunk toward the position average so a
three-game hot streak does not top the list), converted to value over a replacement-level
player at the same position, then weighted by what the roster still needs. It is meant to be
realistic and balanced, not optimal. See docs/decisions.md for the model and its limits.
"""

import random
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

POS_PLURAL = {"F": "forwards", "D": "defenders", "G": "goalies"}


@dataclass(frozen=True)
class DraftPlayer:
    id: int
    name: str
    pos: str  # F, D or G
    team: str
    fpg: float  # fantasy points per game last season
    gp: int
    total: float


@dataclass(frozen=True)
class DraftConfig:
    teams: int = 10
    # Starting lineup 3 F + 1 UTIL (any forward) + 2 D + 1 G, then the bench.
    need_f: int = 4
    need_d: int = 2
    need_g: int = 1
    bench: int = 4
    max_goalies: int = 2
    season_games: int = 30
    # How strongly a small sample is pulled toward the position average (in games).
    shrink_games: float = 4.0

    @property
    def rounds(self) -> int:
        return self.need_f + self.need_d + self.need_g + self.bench

    @property
    def total_picks(self) -> int:
        return self.rounds * self.teams

    def needed(self, pos: str) -> int:
        return {"F": self.need_f, "D": self.need_d, "G": self.need_g}[pos]


@dataclass(frozen=True)
class Valuation:
    proj: float  # projected season points
    vorp: float  # projected points above a replacement-level player at the position
    rank: int  # overall rank by value
    pos_rank: int


@dataclass(frozen=True)
class Recommendation:
    player: DraftPlayer
    tag: str
    reason: str


@dataclass
class DraftState:
    """What the picks so far imply."""

    picks: list[int]
    rosters: list[list[DraftPlayer]]  # by team index
    available: list[DraftPlayer]
    pick_no: int  # 1-based number of the next pick
    round: int
    team: int | None  # index of the team on the clock, None once the draft is over

    @property
    def complete(self) -> bool:
        return self.team is None


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def snake_slot(pick_no: int, teams: int) -> tuple[int, int]:
    """(round, team index) for a 1-based overall pick number in a snake draft."""
    rnd, i = divmod(pick_no - 1, teams)
    return rnd + 1, (i if rnd % 2 == 0 else teams - 1 - i)


def valuations(players: Sequence[DraftPlayer], cfg: DraftConfig) -> dict[int, Valuation]:
    """Projected points and value over replacement for every player in the pool."""
    by_pos: dict[str, list[DraftPlayer]] = {"F": [], "D": [], "G": []}
    for p in players:
        by_pos[p.pos].append(p)
    proj: dict[int, float] = {}
    for plist in by_pos.values():
        if not plist:
            continue
        games = sum(p.gp for p in plist)
        mean = sum(p.total for p in plist) / games if games else 0.0
        for p in plist:
            adj = (p.total + cfg.shrink_games * mean) / (p.gp + cfg.shrink_games)
            # Regress games played toward a full season: last year's absences may not repeat.
            expected = min(cfg.season_games, 0.7 * p.gp + 0.3 * cfg.season_games)
            proj[p.id] = adj * expected
    # Replacement level: the best player who would not start. For forwards and defenders that is
    # half a bench past the starters; each team carries one starting goalie and a spare is nearly
    # worthless, so for goalies it is simply the last team's starter.
    repl = {}
    for pos, plist in by_pos.items():
        ranked = sorted((proj[p.id] for p in plist), reverse=True)
        slack = 0 if pos == "G" else cfg.teams // 2
        idx = min(len(ranked) - 1, cfg.needed(pos) * cfg.teams + slack - 1)
        repl[pos] = ranked[idx] if ranked else 0.0
    vorp = {p.id: proj[p.id] - repl[p.pos] for p in players}
    order = sorted(players, key=lambda p: -vorp[p.id])
    pos_order = {pos: sorted(pl, key=lambda p: -proj[p.id]) for pos, pl in by_pos.items()}
    out = {}
    for rank, p in enumerate(order, start=1):
        pos_rank = pos_order[p.pos].index(p) + 1
        out[p.id] = Valuation(proj[p.id], vorp[p.id], rank, pos_rank)
    return out


def needs(roster: Iterable[DraftPlayer], cfg: DraftConfig) -> dict[str, int]:
    """Starting spots still empty, by position (the flex UTIL counts as a forward)."""
    c = Counter(p.pos for p in roster)
    return {
        "F": max(0, cfg.need_f - c["F"]),
        "D": max(0, cfg.need_d - c["D"]),
        "G": max(0, cfg.need_g - c["G"]),
    }


def assign_slots(roster: Sequence[DraftPlayer], cfg: DraftConfig) -> dict[str, list[DraftPlayer]]:
    """Put a roster into lineup slots in pick order: F F F D D UTIL G, then the bench."""
    slots: dict[str, list[DraftPlayer]] = {"F": [], "D": [], "UTIL": [], "G": [], "BN": []}
    for p in roster:
        if p.pos == "F" and len(slots["F"]) < 3:
            slots["F"].append(p)
        elif p.pos == "D" and len(slots["D"]) < cfg.need_d:
            slots["D"].append(p)
        elif p.pos == "G" and len(slots["G"]) < cfg.need_g:
            slots["G"].append(p)
        elif p.pos == "F" and not slots["UTIL"]:
            slots["UTIL"].append(p)
        else:
            slots["BN"].append(p)
    return slots


def replay(picks: Sequence[int], players: Sequence[DraftPlayer], cfg: DraftConfig) -> DraftState:
    """Rebuild the draft from the pick list (team index = draft slot).

    Raises ValueError for an unknown player, a repeat pick, or too many picks.
    """
    if len(picks) > cfg.total_picks:
        raise ValueError("more picks than the draft has")
    by_id = {p.id: p for p in players}
    rosters: list[list[DraftPlayer]] = [[] for _ in range(cfg.teams)]
    taken: set[int] = set()
    for n, pid in enumerate(picks, start=1):
        if pid not in by_id:
            raise ValueError(f"unknown player {pid}")
        if pid in taken:
            raise ValueError(f"player {pid} was already picked")
        taken.add(pid)
        rosters[snake_slot(n, cfg.teams)[1]].append(by_id[pid])
    nxt = len(picks) + 1
    if nxt > cfg.total_picks:
        rnd, team = cfg.rounds, None
    else:
        rnd, team = snake_slot(nxt, cfg.teams)
    return DraftState(
        picks=list(picks),
        rosters=rosters,
        available=[p for p in players if p.id not in taken],
        pick_no=nxt,
        round=rnd,
        team=team,
    )


@dataclass
class _Context:
    state: DraftState
    vals: dict[int, Valuation]
    cfg: DraftConfig
    roster: list[DraftPlayer]
    picks_left: int = 0
    need: dict[str, int] = field(default_factory=dict)
    goalies_scarce: bool = False

    def __post_init__(self) -> None:
        self.need = needs(self.roster, self.cfg)
        self.picks_left = self.cfg.rounds - len(self.roster)
        teams_needing_g = sum(1 for r in self.state.rosters if needs(r, self.cfg)["G"] > 0)
        goalies_left = sum(1 for p in self.state.available if p.pos == "G")
        # Goalies run out: once the ones left barely cover the teams without one, take one now.
        self.goalies_scarce = self.need["G"] > 0 and goalies_left <= teams_needing_g + 1


def _score(p: DraftPlayer, ctx: _Context) -> float | None:
    """How much a pick helps this roster, or None if the pick is not allowed."""
    cfg, need = ctx.cfg, ctx.need
    n_g = sum(1 for r in ctx.roster if r.pos == "G")
    if p.pos == "G" and n_g >= cfg.max_goalies:
        return None
    # With only enough picks left to fill the starting spots, every pick must fill one.
    if ctx.picks_left <= sum(need.values()) and need[p.pos] == 0:
        return None
    v = ctx.vals[p.id]
    base = max(v.vorp, 0.0) + 0.001 * v.proj
    if need[p.pos] > 0:
        value = base
    else:  # depth for the bench: worth less, and a spare goalie is worth very little
        value = base * (0.12 if p.pos == "G" else 0.45)
    if ctx.goalies_scarce and p.pos == "G":
        value += 1000.0
    return value


def _ranked(ctx: _Context) -> list[tuple[float, DraftPlayer]]:
    scored = [(s, p) for p in ctx.state.available if (s := _score(p, ctx)) is not None]
    scored.sort(key=lambda sp: -sp[0])
    return scored


def _reason(p: DraftPlayer, ctx: _Context, top_value: bool) -> str:
    v = ctx.vals[p.id]
    bits = []
    if ctx.goalies_scarce and p.pos == "G":
        bits.append("Goalies are running out, and you still need one.")
    elif ctx.need[p.pos] > 0:
        left = ctx.need[p.pos]
        spot = {"F": "forward spots (including UTIL)", "D": "defender spots", "G": "goalie spot"}[
            p.pos
        ]
        bits.append(f"You still have {left} starting {spot} to fill.")
    else:
        bits.append("Your starters at this position are set, so this adds bench depth.")
    bits.append(
        f"Averaged {p.fpg:.1f} points per game last season, "
        f"{ordinal(v.pos_rank)} best among {POS_PLURAL[p.pos]}."
    )
    if p.pos == "G" and not ctx.goalies_scarce and ctx.need["G"] > 0:
        bits.append("Only one goalie plays per team, so the best ones go early.")
    elif top_value and ctx.need[p.pos] > 0:
        bits.append("Best value on the board right now.")
    return " ".join(bits)


def recommend(
    state: DraftState,
    team: int,
    vals: dict[int, Valuation],
    cfg: DraftConfig,
    n: int = 3,
) -> list[Recommendation]:
    """The best few picks for a team right now, each with a plain-English reason."""
    ctx = _Context(state=state, vals=vals, cfg=cfg, roster=state.rosters[team])
    ranked = _ranked(ctx)
    out = []
    for i, (_, p) in enumerate(ranked[:n]):
        tag = "Best pick" if i == 0 else ("Also good" if ctx.need[p.pos] > 0 else "Depth")
        out.append(Recommendation(p, tag, _reason(p, ctx, top_value=(i == 0))))
    return out


def cpu_pick(
    state: DraftState, team: int, vals: dict[int, Valuation], cfg: DraftConfig, seed: int = 0
) -> DraftPlayer:
    """A computer team's pick: usually the best fit, sometimes one of the next few.

    Each team has a mild, fixed lean toward a position so the teams do not all draft alike.
    Deterministic for a given seed, team and pick number.
    """
    ctx = _Context(state=state, vals=vals, cfg=cfg, roster=state.rosters[team])
    lean_rng = random.Random(seed * 7919 + team)
    lean = {pos: 0.9 + 0.2 * lean_rng.random() for pos in POS_PLURAL}
    ranked = [(s * lean[p.pos], p) for s, p in _ranked(ctx)]
    ranked.sort(key=lambda sp: -sp[0])
    if not ranked:
        raise ValueError("no legal pick available")
    top = ranked[:4]
    # Forced and scarce picks are never randomised away.
    if ctx.goalies_scarce and top[0][1].pos == "G":
        return top[0][1]
    rng = random.Random(seed * 104729 + state.pick_no * 31 + team)
    weights = [0.6, 0.22, 0.12, 0.06][: len(top)]
    return rng.choices([p for _, p in top], weights=weights, k=1)[0]
