from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    Draft,
    FantasyTeam,
    FantasyWeek,
    Game,
    LineupLock,
    Matchup,
    Player,
    ProTeam,
    RosterEntry,
    SkaterGameStats,
)
from undertow.fantasy import auth, leagues, lineups, matchup_jobs, standings

pytestmark = pytest.mark.db

BASE = 990_000
HOME, AWAY = 9_701, 9_702
TUE = datetime(2026, 12, 8, 0, 0, tzinfo=UTC)  # inside week 1 (Dec 5 to 13)
DURING = datetime(2026, 12, 8, 12, 0, tzinfo=UTC)
AFTER_WEEK = datetime(2026, 12, 15, 12, 0, tzinfo=UTC)  # week 1 over, inside the 3-day wait
SETTLED = datetime(2026, 12, 17, 12, 0, tzinfo=UTC)  # more than 3 days after Dec 13


@pytest.fixture
def db(fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY]})
    with Session(fantasy_db) as s:
        s.add_all([ProTeam(id=HOME, name="H", code="HOM"), ProTeam(id=AWAY, name="A", code="AWY")])
        for n, start in ((1, date(2026, 12, 5)), (2, date(2026, 12, 14))):
            s.add(
                FantasyWeek(
                    season_id=11,
                    week_no=n,
                    start_date=start,
                    end_date=start + timedelta(days=8 if n == 1 else 6),
                    games=4,
                )
            )
        s.commit()
        yield s
    with fantasy_db.begin() as conn:
        conn.execute(
            text("TRUNCATE draft, roster_entry, lineup_slot, lineup_lock, matchup, game CASCADE")
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY]})


def league_of_four(db, status="in_season"):
    users = [
        auth.register_user(
            db, email=f"u{i}@example.com", display_name=f"u{i}", password="a long enough password"
        ).user
        for i in range(4)
    ]
    league, _ = leagues.register_league(
        db, users[0], name="L", season_id=11, size=4, team_name="Team 0"
    )
    for i in range(1, 4):
        leagues.join_league(db, users[i], league.invite_code, f"Team {i}")
    league.status = status
    db.add(
        Draft(
            league_id=league.id,
            status="complete",
            pick_seconds=60,
            rounds=11,
            order=[],
            order_seed=1,
            current_pick=45,
            completed_at=datetime(2026, 12, 1, tzinfo=UTC),
        )
    )
    db.commit()
    return league, [
        db.scalar(select(FantasyTeam.id).where(FantasyTeam.owner_id == u.id)) for u in users
    ]


def scorer(db, league, team_id, n, goals, game_id):
    """A forward who scored `goals` goals (2 points each) for the team in the game, locked in."""
    p = Player(
        id=BASE + n,
        first_name="P",
        last_name=f"{n}",
        position="F",
        pro_team_id=HOME,
        status="active",
    )
    db.add(p)
    db.flush()
    db.add(RosterEntry(league_id=league.id, team_id=team_id, player_id=p.id))
    db.flush()
    lineups.place_new_player(db, team_id, p)
    db.add(
        SkaterGameStats(
            game_id=game_id, player_id=p.id, team_id=HOME, toi_seconds=1200, goals=goals
        )
    )
    db.add(LineupLock(team_id=team_id, player_id=p.id, game_id=game_id, slot="F"))
    return p


def season(db, league, teams, goals=(1, 2, 3, 3), status="final"):
    """Week 1: Team 0 v Team 1 and Team 2 v Team 3, one game, each team's scorer as given."""
    db.add(
        Game(
            id=1,
            season_id=11,
            home_team_id=HOME,
            away_team_id=AWAY,
            start_utc=TUE,
            status=status,
            result_type="REG" if status == "final" else None,
        )
    )
    db.flush()
    players = [
        scorer(db, league, t, i + 1, g, 1)
        for i, (t, g) in enumerate(zip(teams, goals, strict=True))
    ]
    db.add(
        Matchup(
            league_id=league.id,
            week_no=1,
            last_week_no=1,
            home_team_id=teams[0],
            away_team_id=teams[1],
        )
    )
    db.add(
        Matchup(
            league_id=league.id,
            week_no=1,
            last_week_no=1,
            home_team_id=teams[2],
            away_team_id=teams[3],
        )
    )
    db.commit()
    return players


def matchups(db, league):
    return list(
        db.scalars(select(Matchup).where(Matchup.league_id == league.id).order_by(Matchup.id))
    )


# ---- status and finalization ----


def test_matchup_status_follows_the_week_and_finalizes_after_the_wait(db):
    league, teams = league_of_four(db)
    season(db, league, teams, status="scheduled")
    assert (
        matchup_jobs.refresh_matchups(db, TUE - timedelta(days=1)).status_changes == 0
    )  # not begun
    assert {m.status for m in matchups(db, league)} == {"scheduled"}
    assert matchup_jobs.refresh_matchups(db, DURING).status_changes == 2
    assert {m.status for m in matchups(db, league)} == {"live"}
    db.get(Game, 1).status = "final"
    db.commit()
    result = matchup_jobs.refresh_matchups(db, AFTER_WEEK)
    assert (result.status_changes, result.finalized) == (2, 0)
    assert {m.status for m in matchups(db, league)} == {"final"}
    assert all(m.home_points is None for m in matchups(db, league))
    result = matchup_jobs.refresh_matchups(db, SETTLED)
    assert result.finalized == 2
    first = matchups(db, league)[0]
    assert (first.status, first.home_points, first.away_points) == ("finalized", "2", "4")
    assert first.finalized_at is not None
    assert matchup_jobs.refresh_matchups(db, SETTLED).summary() == "status_changes=0 finalized=0"


def test_a_late_stat_correction_cannot_change_a_finalized_result(db):
    league, teams = league_of_four(db)
    players = season(db, league, teams)
    matchup_jobs.refresh_matchups(db, AFTER_WEEK)
    matchup_jobs.refresh_matchups(db, SETTLED)
    row = db.scalar(select(SkaterGameStats).where(SkaterGameStats.player_id == players[0].id))
    row.goals = 9  # the feed corrects a box score a month later
    db.commit()
    matchup_jobs.refresh_matchups(db, SETTLED + timedelta(days=30))
    assert matchups(db, league)[0].home_points == "2"  # stored, unchanged
    table, provisional = standings.standings(db, league.id, now=SETTLED)
    assert provisional is False
    assert next(r for r in table if r.team_id == teams[0]).points_for == 2


def test_only_active_leagues_and_begun_weeks_are_touched(db):
    league, teams = league_of_four(db, status="drafting")
    season(db, league, teams)
    db.add(
        Matchup(
            league_id=league.id,
            week_no=2,
            last_week_no=2,
            home_team_id=teams[0],
            away_team_id=teams[2],
        )
    )
    db.commit()
    assert matchup_jobs.refresh_matchups(db, SETTLED).summary() == "status_changes=0 finalized=0"
    league.status = "in_season"
    db.commit()
    matchup_jobs.refresh_matchups(db, SETTLED)
    by_week = {(m.week_no, m.status) for m in matchups(db, league)}
    assert (1, "finalized") in by_week and (2, "finalized") not in by_week


def test_a_week_with_unfinished_games_is_never_finalized(db):
    league, teams = league_of_four(db)
    season(db, league, teams, status="scheduled")  # the game never got played or updated
    assert matchup_jobs.refresh_matchups(db, SETTLED).finalized == 0
    assert {m.status for m in matchups(db, league)} <= {"live", "scheduled"}


def test_the_worker_wrapper_reports_a_summary(db, fantasy_db):
    league, teams = league_of_four(db)
    season(db, league, teams)
    assert (
        matchup_jobs.run_matchup_job(lambda: Session(fantasy_db), AFTER_WEEK)
        == "status_changes=2 finalized=0"
    )


# ---- standings ----


def test_provisional_standings_use_live_totals_and_mark_the_table(db):
    league, teams = league_of_four(db)
    season(db, league, teams)  # scores: T0 2, T1 4, T2 6, T3 6
    matchup_jobs.refresh_matchups(db, AFTER_WEEK)
    table, provisional = standings.standings(db, league.id, now=AFTER_WEEK)
    assert provisional is True
    by_team = {r.team_id: r for r in table}
    assert (by_team[teams[1]].wins, by_team[teams[0]].losses) == (1, 1)
    assert (by_team[teams[2]].ties, by_team[teams[3]].ties) == (1, 1)
    assert by_team[teams[1]].points_for == 4 and by_team[teams[1]].points_against == 2
    # Order: Team 1 (win = 2) first, then the tied pair (1 each), then Team 0 (a loss).
    assert [r.team_id for r in table][0] == teams[1] and table[-1].team_id == teams[0]
    assert [r.rank for r in table] == [1, 2, 3, 4]


def test_ties_in_the_record_break_on_points_for_then_name(db):
    league, teams = league_of_four(db)
    season(db, league, teams, goals=(1, 1, 3, 2))  # T2 beats T3; T0 and T1 tie
    matchup_jobs.refresh_matchups(db, SETTLED)
    matchup_jobs.refresh_matchups(db, SETTLED)
    table, provisional = standings.standings(db, league.id, now=SETTLED)
    assert provisional is False
    # T2 (win, 6 PF) first; T0 and T1 each tied with 2 PF, so the name decides; T3 lost.
    assert [r.team_id for r in table] == [teams[2], teams[0], teams[1], teams[3]]


def test_unfinished_matchups_and_teams_with_no_games_show_zeros(db):
    league, teams = league_of_four(db)
    season(db, league, teams, status="scheduled")
    matchup_jobs.refresh_matchups(db, DURING)  # live, not final
    table, provisional = standings.standings(db, league.id, now=DURING)
    assert provisional is False and all(r.played == 0 and r.points_for == 0 for r in table)
    assert sorted(r.name for r in table) == ["Team 0", "Team 1", "Team 2", "Team 3"]
    assert [r.rank for r in table] == [1, 2, 3, 4]


def test_results_list_names_the_winner_or_a_tie(db):
    league, teams = league_of_four(db)
    season(db, league, teams)
    matchup_jobs.refresh_matchups(db, AFTER_WEEK)
    first, second = standings.results(db, league.id, now=AFTER_WEEK)
    assert first.winner == teams[1] and second.winner is None
    assert first.provisional and (first.home_points, first.away_points) == (
        Fraction(2),
        Fraction(4),
    )
