"""Source-independent data shapes produced by the feed parsers.

Everything downstream of the adapter (DB upserts, scoring input) uses these, never raw
HockeyTech JSON.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class Position(StrEnum):
    F = "F"
    D = "D"
    G = "G"


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"


class ResultType(StrEnum):
    REG = "REG"
    OT = "OT"
    SO = "SO"


@dataclass(frozen=True, slots=True)
class SeasonDTO:
    id: int
    name: str
    start: date | None
    end: date | None
    is_playoff: bool
    is_career: bool


@dataclass(frozen=True, slots=True)
class TeamDTO:
    id: int
    name: str
    code: str
    city: str
    nickname: str
    logo_url: str


@dataclass(frozen=True, slots=True)
class PlayerDTO:
    id: int
    person_id: int | None
    first_name: str
    last_name: str
    position: Position | None
    raw_position: str
    team_id: int | None
    jersey: int | None
    feed_status: str
    shoots: str
    birthdate: date | None


@dataclass(frozen=True, slots=True)
class GameDTO:
    id: int
    season_id: int
    home_team_id: int
    away_team_id: int
    start_utc: datetime
    status: GameStatus
    status_text: str
    result_type: ResultType | None
    home_goals: int
    away_goals: int
    venue_name: str
    venue_location: str
    notes: str
    last_modified: str


@dataclass(frozen=True, slots=True)
class SkaterLineDTO:
    player_id: int
    team_id: int
    position: Position | None
    goals: int = 0
    assists: int = 0
    shots: int = 0
    hits: int = 0
    blocks: int = 0
    penalty_minutes: int = 0
    plus_minus: int = 0
    toi_seconds: int = 0
    faceoff_wins: int = 0
    faceoff_attempts: int = 0
    pp_goals: int = 0
    pp_assists: int = 0
    sh_goals: int = 0
    sh_assists: int = 0
    gwg: int = 0
    # Goals scored into an empty net. These are not charged to the opposing goalie.
    en_goals: int = 0


@dataclass(frozen=True, slots=True)
class GoalieLineDTO:
    player_id: int
    team_id: int
    toi_seconds: int
    shots_against: int
    goals_against: int
    saves: int
    decision: str | None
    shutout: bool
    started: bool


@dataclass(frozen=True, slots=True)
class BoxScoreDTO:
    game_id: int
    season_id: int
    status: GameStatus
    status_text: str
    result_type: ResultType | None
    skaters: list[SkaterLineDTO] = field(default_factory=list)
    goalies: list[GoalieLineDTO] = field(default_factory=list)
    # Minimal bio for players seen in the box score, so FK rows can be created for players
    # the roster feed has not (yet) listed.
    players: list[PlayerDTO] = field(default_factory=list)
