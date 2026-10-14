"""Pure scoring functions: (stat line, config) -> exact points + breakdown."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from .config import ScoringConfig
from .models import Decision, GoalieStatLine, ResultType, SkaterStatLine


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total: Fraction
    breakdown: dict[str, Fraction] = field(default_factory=dict)

    def total_decimal(self, places: int = 2) -> Decimal:
        """Rounded for display only (ROUND_HALF_UP). Compare on `total`, never on this."""
        exact = Decimal(self.total.numerator) / Decimal(self.total.denominator)
        return exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _build(contributions: dict[str, Fraction]) -> ScoreResult:
    breakdown = {k: v for k, v in contributions.items() if v != 0}
    return ScoreResult(total=sum(breakdown.values(), Fraction(0)), breakdown=breakdown)


def score_skater(line: SkaterStatLine, config: ScoringConfig) -> ScoreResult:
    # PP/SH points are bonuses on top of the normal goal/assist points.
    return _build(
        {
            "goal": line.goals * config.goal,
            "assist": line.assists * config.assist,
            "pp_point": (line.pp_goals + line.pp_assists) * config.pp_point,
            "sh_point": (line.sh_goals + line.sh_assists) * config.sh_point,
            "shot": line.shots * config.shot,
            "hit": line.hits * config.hit,
            "block": line.blocks * config.block,
        }
    )


def score_goalie(line: GoalieStatLine, config: ScoringConfig) -> ScoreResult:
    if not line.played:
        return ScoreResult(total=Fraction(0), breakdown={})
    c: dict[str, Fraction] = {
        "save": line.saves * config.save,
        "goal_against": line.goals_against * config.goal_against,
    }
    if line.shutout:
        c["shutout"] = config.shutout
    if line.decision is Decision.W:
        if line.result_type is ResultType.REG:
            c["win"] = config.win
        else:
            c["ot_win"] = config.win * config.ot_win_multiplier
    elif line.decision in (Decision.OTL, Decision.SOL):
        c["ot_loss"] = config.ot_loss
    return _build(c)
