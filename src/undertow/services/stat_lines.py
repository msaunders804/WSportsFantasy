"""Bridge from stored stat rows to the pure scoring engine's input types."""

from undertow.models import GoalieGameStats, SkaterGameStats
from undertow.scoring import Decision, GoalieStatLine, ResultType, SkaterStatLine

_SKATER_FIELDS = (
    "goals assists shots hits blocks penalty_minutes plus_minus toi_seconds pp_goals "
    "pp_assists sh_goals sh_assists gwg faceoff_wins faceoff_attempts"
).split()


def skater_stat_line(row: SkaterGameStats) -> SkaterStatLine:
    return SkaterStatLine(**{f: getattr(row, f) for f in _SKATER_FIELDS})


def goalie_stat_line(row: GoalieGameStats, result_type: str | None) -> GoalieStatLine:
    """`result_type` is the game's REG/OT/SO (from `game.result_type`); None counts as REG."""
    return GoalieStatLine(
        toi_seconds=row.toi_seconds,
        shots_against=row.shots_against,
        goals_against=row.goals_against,
        saves=row.saves,
        decision=Decision(row.decision) if row.decision else None,
        result_type=ResultType(result_type or "REG"),
        shutout=row.shutout,
    )
