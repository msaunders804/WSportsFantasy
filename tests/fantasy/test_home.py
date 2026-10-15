from types import SimpleNamespace

from undertow.fantasy import home


def league(status="setup", name="Alpha"):
    return SimpleNamespace(status=status, name=name)


def test_no_league_means_the_waiting_view():
    result = home.pick([])
    assert result.state == home.HomeState.NO_LEAGUE
    assert result.league is None and result.team is None and result.role is None


def test_a_league_in_setup_gets_the_pre_draft_view():
    row = (league("setup"), SimpleNamespace(name="Ice Queens"), "member")
    result = home.pick([row])
    assert result.state == home.HomeState.LEAGUE_SETUP
    assert result.league.name == "Alpha" and result.team.name == "Ice Queens"
    assert result.role == "member"


def test_every_started_status_gets_the_active_view():
    for status in ("drafting", "in_season", "playoffs", "complete"):
        result = home.pick([(league(status), None, "member")])
        assert result.state == home.HomeState.LEAGUE_ACTIVE, status


def test_the_newest_league_wins():
    rows = [(league("setup", "New"), None, "member"), (league("complete", "Old"), None, "member")]
    result = home.pick(rows)
    assert result.league.name == "New" and result.state == home.HomeState.LEAGUE_SETUP
