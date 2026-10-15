"""Scoring configuration: exact per-stat point values, JSON round-trippable."""

import hashlib
import json
from dataclasses import dataclass, fields
from decimal import Decimal
from fractions import Fraction
from typing import Any


def _to_fraction(value: Any) -> Fraction:
    if isinstance(value, bool):
        raise TypeError("scoring value must be a number, not bool")
    if isinstance(value, float):
        return Fraction(str(value))  # 0.1 means one tenth, not the binary float
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int | Decimal):
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value.strip())
    raise TypeError(f"unsupported scoring value type: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Points per stat as exact Fractions. A value of 0 means the stat is not scored.

    `ot_win_multiplier` scales `win` for OT/SO wins (not a second fixed value).
    `ot_loss` applies to both OTL and SOL decisions.
    Inputs may be int, str ('0.1', '2/3'), Decimal, Fraction or float (via its repr).
    """

    goal: Fraction = Fraction(2)
    assist: Fraction = Fraction(1)
    pp_point: Fraction = Fraction(1, 2)
    sh_point: Fraction = Fraction(1, 2)
    shot: Fraction = Fraction(1, 10)
    hit: Fraction = Fraction(1, 10)
    block: Fraction = Fraction(1, 2)
    win: Fraction = Fraction(4)
    ot_win_multiplier: Fraction = Fraction(2, 3)
    goal_against: Fraction = Fraction(-2)
    save: Fraction = Fraction(1, 5)
    shutout: Fraction = Fraction(3)
    ot_loss: Fraction = Fraction(1)

    def __post_init__(self) -> None:
        for f in fields(self):
            object.__setattr__(self, f.name, _to_fraction(getattr(self, f.name)))

    def to_dict(self) -> dict[str, str]:
        """Canonical JSON-safe form: every key present, values as exact strings ('1/10', '2')."""
        return {f.name: str(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoringConfig":
        """Build from a dict. Unknown keys raise ValueError; absent keys mean 0 (not scored)."""
        known = [f.name for f in fields(cls)]
        unknown = set(data) - set(known)
        if unknown:
            raise ValueError(f"unknown scoring keys: {sorted(unknown)}")
        return cls(**{name: data.get(name, 0) for name in known})

    def hash(self) -> str:
        """Stable 16-hex-char hash of the canonical config (cache key for fantasy_points)."""
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
