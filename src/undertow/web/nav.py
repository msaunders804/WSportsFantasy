"""The tabs a signed-in member sees inside a league."""

from undertow.data.models import League


def league_tabs(league: League) -> list[tuple[str, str, str]]:
    """(key, label, link). The Draft tab is only there while a draft can still happen."""
    tabs = [
        ("matchup", "Matchup", f"/leagues/{league.id}/matchup"),
        ("team", "Team", f"/leagues/{league.id}/team"),
        ("league", "League", f"/leagues/{league.id}/standings"),
    ]
    if league.status in ("setup", "drafting"):
        tabs.append(("draft", "Draft", f"/leagues/{league.id}/draft"))
    tabs.append(("stats", "Stats", "/stats"))
    return tabs
