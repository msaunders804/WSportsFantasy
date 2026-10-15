"""Which home page a signed-in member sees. Pure: pass in what the database says.

Members have at most one league this season, so the home page follows their newest league. With
no league yet (everyone, until leagues are assigned) it shows where things stand instead.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class HomeState(StrEnum):
    NO_LEAGUE = "no_league"  # on the list, waiting to be placed
    LEAGUE_SETUP = "league_setup"  # in a league, before the draft
    LEAGUE_ACTIVE = "league_active"  # drafting, in season, playoffs or complete


@dataclass(frozen=True)
class Home:
    state: HomeState
    league: Any = None
    team: Any = None
    role: str | None = None


def pick(rows: Sequence[tuple[Any, Any, str]]) -> Home:
    """Choose the home view from `leagues_for_user` rows: (league, team, role), newest first."""
    if not rows:
        return Home(HomeState.NO_LEAGUE)
    league, team, role = rows[0]
    state = HomeState.LEAGUE_SETUP if league.status == "setup" else HomeState.LEAGUE_ACTIVE
    return Home(state, league, team, role)
