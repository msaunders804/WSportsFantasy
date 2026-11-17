from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    FantasyTeam,
    FantasyWeek,
    Matchup,
    Player,
)
from undertow.fantasy import admin_view, auth, draft_room, leagues, lineups, schedule
from undertow.fantasy import season_calendar as cal

pytestmark = pytest.mark.db

BASE = 960_000
POSITIONS = ["F"] * 36 + ["D"] * 16 + ["G"] * 8
T0 = datetime(2026, 11, 30, 18, 0, tzinfo=UTC)


@pytest.fixture
def db(fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
    with Session(fantasy_db) as s:
        s.add_all(
            Player(id=BASE + i, first_name=f"P{i}", last_name=pos, position=pos, status="active")
            for i, pos in enumerate(POSITIONS)
        )
        s.commit()
        yield s
    with fantasy_db.begin() as conn:
        conn.execute(
            text("TRUNCATE draft, roster_entry, lineup_slot, lineup_lock, matchup CASCADE")
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})


def store_calendar(db, weeks=18, first=date(2026, 12, 5)):
    """Eighteen consecutive weeks for season 11, the first one nine days long like the real one."""
    days = [first + timedelta(days=7 * i) for i in range(weeks)]
    spans = cal.build_weeks(days)
    for span in spans:
        db.add(
            FantasyWeek(
                season_id=11,
                week_no=span.week_no,
                start_date=span.start,
                end_date=span.end,
                games=span.games,
            )
        )
    db.commit()
    return spans


def full_league(db, size=4):
    boss = auth.register_user(
        db, email="u0@example.com", display_name="u0", password="a long enough password"
    ).user
    league, _ = leagues.register_league(
        db, boss, name="Friends", season_id=11, size=size, team_name="Team 0"
    )
    users = [boss]
    for i in range(1, size):
        u = auth.register_user(
            db, email=f"u{i}@example.com", display_name=f"u{i}", password="a long enough password"
        ).user
        leagues.join_league(db, u, league.invite_code, f"Team {i}")
        users.append(u)
    return league, users


def matchups(db, league_id):
    return list(
        db.scalars(select(Matchup).where(Matchup.league_id == league_id).order_by(Matchup.id))
    )


# ---- creating a schedule ----


def test_a_schedule_covers_every_regular_season_week_with_every_team_once(db):
    store_calendar(db)
    league, users = full_league(db)
    created = schedule.create_league_schedule(db, league.id, seed=99, actor=users[0])
    assert len(created) == 24  # 12 weeks x 2 matchups
    by_week = Counter(m.week_no for m in matchups(db, league.id))
    assert by_week == {w: 2 for w in range(1, 13)}
    team_ids = set(db.scalars(select(FantasyTeam.id).where(FantasyTeam.league_id == league.id)))
    for week in range(1, 13):
        teams = [
            t
            for m in schedule.matchups_for_week(db, league.id, week)
            for t in (m.home_team_id, m.away_team_id)
        ]
        assert sorted(teams) == sorted(team_ids)
    assert all(
        m.status == "scheduled" and m.last_week_no == m.week_no for m in matchups(db, league.id)
    )
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "schedule.generate"))
    assert entry.after == {"seed": 99, "weeks": 12, "teams": 4} and entry.actor_id == users[0].id


def test_the_stored_seed_reproduces_the_stored_schedule(db):
    store_calendar(db)
    league, _ = full_league(db)
    schedule.create_league_schedule(db, league.id, seed=1234)
    team_ids = list(db.scalars(select(FantasyTeam.id).where(FantasyTeam.league_id == league.id)))
    plan = schedule.generate_matchups(team_ids, 12, 1234)
    stored = [(m.week_no, m.home_team_id, m.away_team_id) for m in matchups(db, league.id)]
    expected = [(w, h, a) for w, week in enumerate(plan, 1) for h, a in week]
    assert stored == expected


def test_a_league_cannot_be_scheduled_twice_or_before_it_is_full(db):
    store_calendar(db)
    league, _ = full_league(db)
    schedule.create_league_schedule(db, league.id, seed=1)
    with pytest.raises(leagues.LeagueError, match="already has a schedule"):
        schedule.create_league_schedule(db, league.id, seed=2)
    boss = auth.register_user(
        db, email="solo@example.com", display_name="solo", password="a long enough password"
    ).user
    half, _ = leagues.register_league(db, boss, name="Half", season_id=11, size=4, team_name="Only")
    with pytest.raises(leagues.LeagueError, match="1 of 4 teams"):
        schedule.create_league_schedule(db, half.id)
    assert len(matchups(db, half.id)) == 0


def test_a_season_calendar_that_is_too_short_is_refused_with_a_clear_reason(db):
    store_calendar(db, weeks=5)
    league, _ = full_league(db)
    with pytest.raises(leagues.LeagueError, match="Load the season's schedule"):
        schedule.create_league_schedule(db, league.id)
    assert matchups(db, league.id) == []


def test_regenerating_is_for_managers_and_only_before_week_one(db):
    store_calendar(db)
    league, users = full_league(db)
    schedule.create_league_schedule(db, league.id, seed=1)
    with pytest.raises(leagues.PermissionDenied):
        schedule.regenerate_league_schedule(db, users[1], league.id, now=T0)
    before = [(m.week_no, m.home_team_id, m.away_team_id) for m in matchups(db, league.id)]
    schedule.regenerate_league_schedule(db, users[0], league.id, now=T0)
    after = [(m.week_no, m.home_team_id, m.away_team_id) for m in matchups(db, league.id)]
    assert len(after) == 24 and before != after  # a new draw (the chance of equal is negligible)
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "schedule.regenerate")
        )
        == 1
    )
    week_one = datetime(2026, 12, 5, 15, tzinfo=UTC)
    with pytest.raises(leagues.LeagueError, match="Week 1 has started"):
        schedule.regenerate_league_schedule(db, users[0], league.id, now=week_one)
    assert [(m.week_no, m.home_team_id, m.away_team_id) for m in matchups(db, league.id)] == after


# ---- a finished draft sets everything up ----


def play_full_draft(db, league, users):
    draft = draft_room.start_draft(db, users[0], league.id, now=T0, seed=7)
    for _ in range(draft_room.total_picks(draft)):
        db.refresh(draft)
        team = draft_room.team_on_clock(draft)
        counts = draft_room._roster_counts(db, league.id, team)
        need = "G" if counts["G"] < 1 else "D" if counts["D"] < 2 else "F"
        pid = db.scalar(
            select(Player.id)
            .where(
                Player.id >= BASE,
                Player.position == need,
                Player.id.not_in(select(draft_room.DraftSelection.player_id)),
            )
            .order_by(Player.id)
            .limit(1)
        )
        owner_id = db.get(FantasyTeam, team).owner_id
        draft_room.make_pick(db, next(u for u in users if u.id == owner_id), draft.id, pid, now=T0)
    return draft


def test_finishing_the_draft_gives_every_team_a_lineup_and_the_league_a_schedule(db):
    store_calendar(db)
    league, users = full_league(db)
    play_full_draft(db, league, users)
    db.refresh(league)
    assert league.status == "in_season"
    for team_id in db.scalars(select(FantasyTeam.id)):
        assert lineups.count_by_slot(db, team_id) == {"F": 3, "UTIL": 1, "D": 2, "G": 1, "BN": 4}
    assert len(matchups(db, league.id)) == 24
    assert db.scalar(select(AuditLog).where(AuditLog.action == "schedule.generate")) is not None
    assert admin_view.league_rows(db)[0].flags == []


def test_a_draft_still_finishes_when_the_season_calendar_is_missing_and_staff_are_told(db):
    league, users = full_league(db)  # no calendar stored, and no games to build one from
    play_full_draft(db, league, users)
    db.refresh(league)
    assert league.status == "in_season"  # the draft itself was not affected
    assert matchups(db, league.id) == []
    for team_id in db.scalars(select(FantasyTeam.id)):
        assert sum(lineups.count_by_slot(db, team_id).values()) == 11  # lineups still set
    flags = admin_view.league_rows(db)[0].flags
    assert any("No schedule yet" in f for f in flags)
    # Once the calendar exists, the same call that the command line makes works.
    store_calendar(db)
    schedule.create_league_schedule(db, league.id)
    assert len(matchups(db, league.id)) == 24
    assert not any("No schedule yet" in f for f in admin_view.league_rows(db)[0].flags)


def test_undoing_a_pick_removes_the_players_lineup_slot(db):
    league, users = full_league(db)
    draft = draft_room.start_draft(db, users[0], league.id, now=T0, seed=7)
    team = draft_room.team_on_clock(draft)
    owner = next(u for u in users if u.id == db.get(FantasyTeam, team).owner_id)
    draft_room.make_pick(db, owner, draft.id, BASE, now=T0)
    assert lineups.count_by_slot(db, team) == {"F": 1}
    draft_room.undo_last_pick(db, users[0], draft.id, now=T0)
    assert lineups.count_by_slot(db, team) == {}


def test_matchups_for_week_includes_two_week_playoff_rounds(db):
    league, users = full_league(db)
    ids = list(db.scalars(select(FantasyTeam.id).where(FantasyTeam.league_id == league.id)))
    db.add(
        Matchup(
            league_id=league.id,
            week_no=13,
            last_week_no=14,
            home_team_id=ids[0],
            away_team_id=ids[1],
        )
    )
    db.add(
        Matchup(
            league_id=league.id,
            week_no=12,
            last_week_no=12,
            home_team_id=ids[2],
            away_team_id=ids[3],
        )
    )
    db.commit()
    assert [m.week_no for m in schedule.matchups_for_week(db, league.id, 14)] == [13]
    assert [m.week_no for m in schedule.matchups_for_week(db, league.id, 13)] == [13]
    assert [m.week_no for m in schedule.matchups_for_week(db, league.id, 12)] == [12]
