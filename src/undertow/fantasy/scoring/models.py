"""Stat lines fed into the scoring engine. Plain data, no I/O."""

from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    W = "W"
    L = "L"
    OTL = "OTL"
    SOL = "SOL"


class ResultType(StrEnum):
    REG = "REG"
    OT = "OT"
    SO = "SO"


@dataclass(frozen=True, slots=True)
class SkaterStatLine:
    goals: int = 0
    assists: int = 0
    shots: int = 0
    hits: int = 0
    blocks: int = 0
    penalty_minutes: int = 0
    plus_minus: int = 0
    toi_seconds: int = 0
    pp_goals: int = 0
    pp_assists: int = 0
    sh_goals: int = 0
    sh_assists: int = 0
    gwg: int = 0
    faceoff_wins: int = 0
    faceoff_attempts: int = 0


@dataclass(frozen=True, slots=True)
class GoalieStatLine:
    toi_seconds: int = 0
    shots_against: int = 0
    goals_against: int = 0
    saves: int = 0
    decision: Decision | None = None
    result_type: ResultType = ResultType.REG
    shutout: bool = False  # derived by the caller; the engine trusts it

    @property
    def played(self) -> bool:
        return self.toi_seconds > 0
