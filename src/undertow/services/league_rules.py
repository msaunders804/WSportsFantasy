"""League and settings rules. Pure functions: no database, no HTTP (Plan section 4.1, 4.5).

The caller passes in anything that comes from the database, such as the size of the player pool.
"""

from dataclasses import dataclass, field, fields

from undertow.scoring.config import ScoringConfig

MIN_TEAMS = 4
MAX_TEAMS = 12
# PWHL teams that dress a different starting goalie each night (Plan 4.1). Goalies are scarce,
# so a league asking for more starting goalie slots than this cannot fill them.
STARTING_GOALIES_LEAGUE_WIDE = 12
# Warn when rosters would take more than this share of the active player pool.
POOL_WARNING_SHARE = 0.5


@dataclass(frozen=True)
class LeagueSettingsData:
    """The commissioner-editable settings. Defaults are the Plan defaults."""

    roster_f: int = 3
    roster_d: int = 2
    roster_util: int = 1
    roster_g: int = 1
    roster_bench: int = 4
    roster_ir: int = 2
    max_goalies: int = 2
    waiver_hours: int = 48
    trade_review_hours: int = 24
    trade_deadline_week: int = 11
    regular_season_weeks: int = 12
    playoff_teams: int = 6
    scoring_config: dict[str, str] = field(default_factory=lambda: ScoringConfig().to_dict())

    @property
    def starters(self) -> int:
        return self.roster_f + self.roster_d + self.roster_util + self.roster_g

    @property
    def roster_size(self) -> int:
        """Starters plus bench (11 by default). IR spots are separate and not counted here."""
        return self.starters + self.roster_bench

    @property
    def players_held(self) -> int:
        """Most players one team can own: the roster plus its IR spots (13 by default)."""
        return self.roster_size + self.roster_ir

    def as_columns(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# Settings a commissioner may change, with the (min, max) each accepts.
INT_LIMITS: dict[str, tuple[int, int]] = {
    "roster_f": (0, 10),
    "roster_d": (0, 10),
    "roster_util": (0, 5),
    "roster_g": (1, 3),
    "roster_bench": (0, 12),
    "roster_ir": (0, 4),
    "max_goalies": (1, 6),
    "waiver_hours": (0, 168),
    "trade_review_hours": (0, 168),
    "trade_deadline_week": (1, 18),
    "regular_season_weeks": (1, 18),
    "playoff_teams": (2, MAX_TEAMS),
}


@dataclass(frozen=True)
class Validation:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_size(size: int) -> list[str]:
    if size < MIN_TEAMS or size > MAX_TEAMS:
        return [f"A league needs between {MIN_TEAMS} and {MAX_TEAMS} teams."]
    if size % 2:
        return ["A league needs an even number of teams, so every team has an opponent each week."]
    return []


def validate_settings(
    size: int,
    settings: LeagueSettingsData,
    *,
    active_pool: int | None = None,
) -> Validation:
    """Check a league's size and settings. Errors block saving; warnings are shown only.

    `active_pool` is the number of rosterable players in the real league (from the database).
    When it is None the pool warning is skipped.
    """
    errors = validate_size(size)
    warnings: list[str] = []

    for name, (low, high) in INT_LIMITS.items():
        value = getattr(settings, name)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            label = name.replace("_", " ").capitalize()
            errors.append(f"{label} must be a whole number from {low} to {high}.")
    if errors:
        return Validation(errors, warnings)

    if settings.starters < 1:
        errors.append("A team needs at least one starting slot.")
    if settings.max_goalies < settings.roster_g:
        errors.append("The goalie limit cannot be lower than the number of goalie slots.")
    if settings.trade_deadline_week > settings.regular_season_weeks:
        errors.append("The trade deadline has to fall inside the regular season.")
    if settings.playoff_teams > size:
        errors.append("More teams cannot make the playoffs than are in the league.")
    try:
        ScoringConfig.from_dict(settings.scoring_config)
    except (ValueError, TypeError, ArithmeticError) as exc:
        errors.append(f"Scoring settings are not valid: {exc}")

    if size * settings.roster_g > STARTING_GOALIES_LEAGUE_WIDE:
        warnings.append(
            f"{size} teams with {settings.roster_g} goalie slot each needs "
            f"{size * settings.roster_g} starting goalies, and the PWHL only has about "
            f"{STARTING_GOALIES_LEAGUE_WIDE}. Some goalie slots will sit empty."
        )
    if active_pool and size * settings.players_held > POOL_WARNING_SHARE * active_pool:
        warnings.append(
            f"Rosters would use {size * settings.players_held} of about {active_pool} players, "
            "which leaves few good options on the waiver wire."
        )
    return Validation(errors, warnings)


def clean_name(value: str, *, max_length: int) -> str:
    """Trim, collapse inner whitespace and cap the length. Used for league and team names."""
    return " ".join(value.split())[:max_length]
