import threading

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from undertow.data.models import AuditLog, FantasyTeam, League, LeagueMember, LeagueSettings, User
from undertow.fantasy import auth, leagues
from undertow.fantasy.league_rules import LeagueSettingsData

pytestmark = pytest.mark.db


@pytest.fixture
def db(fantasy_db):
    with Session(fantasy_db) as s:
        yield s


def make_user(db, email, admin=False):
    user = auth.register_user(
        db, email=email, display_name=email.split("@")[0], password="a long enough password"
    ).user
    if admin:
        user.is_admin = True
        db.commit()
    return user


@pytest.fixture
def admin(db):
    return make_user(db, "admin@example.com", admin=True)


def new_league(db, admin, size=4, name="Alpha"):
    league, _ = leagues.create_league(db, admin, name=name, season_id=11, size=size)
    return league


def test_only_staff_can_create_leagues(db):
    fan = make_user(db, "fan@example.com")
    with pytest.raises(leagues.PermissionDenied):
        leagues.create_league(db, fan, name="Mine", season_id=11, size=10)
    assert db.scalar(select(func.count()).select_from(League)) == 0


def test_create_league_stores_settings_invite_code_and_audit(db, admin):
    league = new_league(db, admin, size=10)
    assert league.status == "setup"
    assert len(league.invite_code) >= 16
    row = db.get(LeagueSettings, league.id)
    assert row.roster_f == 3 and row.max_goalies == 2
    assert row.scoring_config["goal"] == "2"
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "league.create"))
    assert entry.actor_id == admin.id and entry.league_id == league.id


@pytest.mark.parametrize("size", [3, 5, 14])
def test_create_league_rejects_bad_size(db, admin, size):
    with pytest.raises(leagues.LeagueError):
        leagues.create_league(db, admin, name="X", season_id=11, size=size)


def test_create_league_rejects_blank_and_duplicate_names(db, admin):
    with pytest.raises(leagues.LeagueError):
        leagues.create_league(db, admin, name="   ", season_id=11, size=10)
    new_league(db, admin, name="Alpha")
    with pytest.raises(leagues.LeagueError, match="already exists"):
        new_league(db, admin, name="Alpha")


def test_create_league_rejects_invalid_settings(db, admin):
    with pytest.raises(leagues.LeagueError):
        leagues.create_league(
            db, admin, name="X", season_id=11, size=10, settings=LeagueSettingsData(roster_g=0)
        )


def test_join_creates_member_and_team(db, admin):
    league = new_league(db, admin)
    fan = make_user(db, "fan@example.com")
    team = leagues.join_league(db, fan, league.invite_code, "  Ice   Queens ")
    assert team.name == "Ice Queens" and team.league_id == league.id
    assert leagues.membership(db, fan.id, league.id).role == "member"


def test_join_rejects_bad_code_duplicate_member_and_taken_name(db, admin):
    league = new_league(db, admin)
    a, b = make_user(db, "a@example.com"), make_user(db, "b@example.com")
    with pytest.raises(leagues.LeagueError, match="not valid"):
        leagues.join_league(db, a, "nope", "Team")
    leagues.join_league(db, a, league.invite_code, "Ice Queens")
    with pytest.raises(leagues.LeagueError, match="already in"):
        leagues.join_league(db, a, league.invite_code, "Other")
    with pytest.raises(leagues.LeagueError, match="already has a team"):
        leagues.join_league(db, b, league.invite_code, "ice queens")  # case-insensitive
    with pytest.raises(leagues.LeagueError, match="name"):
        leagues.join_league(db, b, league.invite_code, "   ")
    assert leagues.membership(db, b.id, league.id) is None  # failed join left nothing behind


def test_join_refuses_full_and_started_leagues(db, admin):
    league = new_league(db, admin, size=4)
    for i in range(4):
        leagues.join_league(db, make_user(db, f"u{i}@example.com"), league.invite_code, f"T{i}")
    late = make_user(db, "late@example.com")
    with pytest.raises(leagues.LeagueError, match="full"):
        leagues.join_league(db, late, league.invite_code, "Late")
    league.status = "drafting"
    db.commit()
    with pytest.raises(leagues.LeagueError, match="already started"):
        leagues.join_league(db, late, league.invite_code, "Late")


def test_simultaneous_joins_never_overfill_a_league(fantasy_db):
    with Session(fantasy_db) as setup:
        admin = make_user(setup, "admin@example.com", admin=True)
        league = new_league(setup, admin, size=4)
        code = league.invite_code
        ids = [make_user(setup, f"u{i}@example.com").id for i in range(10)]
    results: list[str] = []

    def attempt(i: int, user_id: int) -> None:
        with Session(fantasy_db) as s:
            user = s.get(User, user_id)
            try:
                leagues.join_league(s, user, code, f"Team {i}")
                results.append("ok")
            except leagues.LeagueError:
                results.append("refused")

    threads = [threading.Thread(target=attempt, args=(i, uid)) for i, uid in enumerate(ids)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with Session(fantasy_db) as s:
        assert s.scalar(select(func.count()).select_from(LeagueMember)) == 4
        assert s.scalar(select(func.count()).select_from(FantasyTeam)) == 4
    assert results.count("ok") == 4 and results.count("refused") == 6


def test_roles_one_commissioner_and_one_co_commissioner(db, admin):
    league = new_league(db, admin)
    users = [make_user(db, f"u{i}@example.com") for i in range(3)]
    for i, u in enumerate(users):
        leagues.join_league(db, u, league.invite_code, f"T{i}")
    leagues.set_role(db, admin, league.id, users[0].id, "commissioner")
    leagues.set_role(db, admin, league.id, users[1].id, "co_commissioner")
    with pytest.raises(leagues.LeagueError, match="already has one"):
        leagues.set_role(db, admin, league.id, users[2].id, "commissioner")
    leagues.set_role(db, admin, league.id, users[0].id, "member")
    leagues.set_role(db, admin, league.id, users[2].id, "commissioner")
    assert leagues.can_manage(db, users[2], league.id)
    assert leagues.can_manage(db, users[1], league.id)
    assert not leagues.can_manage(db, users[0], league.id)
    actions = db.scalars(select(AuditLog.action).where(AuditLog.action == "league.set_role")).all()
    assert len(actions) == 4  # the refused change left no audit entry


def test_set_role_needs_staff_and_a_member(db, admin):
    league = new_league(db, admin)
    fan, stranger = make_user(db, "fan@example.com"), make_user(db, "x@example.com")
    leagues.join_league(db, fan, league.invite_code, "T")
    with pytest.raises(leagues.PermissionDenied):
        leagues.set_role(db, fan, league.id, fan.id, "commissioner")
    with pytest.raises(leagues.LeagueError, match="not in this league"):
        leagues.set_role(db, admin, league.id, stranger.id, "commissioner")
    with pytest.raises(leagues.LeagueError, match="Unknown role"):
        leagues.set_role(db, admin, league.id, fan.id, "owner")


def test_membership_checks(db, admin):
    league = new_league(db, admin)
    fan, stranger = make_user(db, "fan@example.com"), make_user(db, "x@example.com")
    leagues.join_league(db, fan, league.invite_code, "T")
    assert leagues.require_member(db, fan, league.id).user_id == fan.id
    assert leagues.require_member(db, admin, league.id) is None  # staff may look
    with pytest.raises(leagues.PermissionDenied):
        leagues.require_member(db, stranger, league.id)
    with pytest.raises(leagues.PermissionDenied):
        leagues.require_manager(db, fan, league.id)


def test_update_settings(db, admin):
    league = new_league(db, admin, size=10)
    fan = make_user(db, "fan@example.com")
    leagues.join_league(db, fan, league.invite_code, "T")
    leagues.set_role(db, admin, league.id, fan.id, "commissioner")
    leagues.update_settings(db, fan, league.id, {"roster_bench": 5, "waiver_hours": 24})
    row = db.get(LeagueSettings, league.id)
    assert (row.roster_bench, row.waiver_hours) == (5, 24)
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "league.update_settings"))
    assert entry.before == {"roster_bench": 4, "waiver_hours": 48}
    assert entry.after == {"roster_bench": 5, "waiver_hours": 24}


def test_update_settings_refusals(db, admin):
    league = new_league(db, admin, size=10)
    member, stranger = make_user(db, "m@example.com"), make_user(db, "s@example.com")
    leagues.join_league(db, member, league.invite_code, "T")
    with pytest.raises(leagues.PermissionDenied):
        leagues.update_settings(db, member, league.id, {"roster_bench": 5})
    with pytest.raises(leagues.PermissionDenied):
        leagues.update_settings(db, stranger, league.id, {"roster_bench": 5})
    with pytest.raises(leagues.LeagueError, match="Unknown"):
        leagues.update_settings(db, admin, league.id, {"scoring_config": 1})
    with pytest.raises(leagues.LeagueError):
        leagues.update_settings(db, admin, league.id, {"roster_g": 0})
    with pytest.raises(leagues.LeagueError):
        leagues.update_settings(db, admin, league.id, {"trade_deadline_week": 14})
    assert db.get(LeagueSettings, league.id).roster_bench == 4  # nothing changed
    league.status = "drafting"
    db.commit()
    with pytest.raises(leagues.LeagueError, match="locked"):
        leagues.update_settings(db, admin, league.id, {"roster_bench": 5})


def test_database_enforces_even_league_size(fantasy_db):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), fantasy_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO league (name, season_id, size, invite_code) "
                "VALUES ('Odd', 11, 9, 'code')"
            )
        )


def test_leagues_for_user(db, admin):
    league = new_league(db, admin)
    fan = make_user(db, "fan@example.com")
    assert leagues.leagues_for_user(db, fan) == []
    leagues.join_league(db, fan, league.invite_code, "Ice Queens")
    [(found, team, role)] = leagues.leagues_for_user(db, fan)
    assert (found.id, team.name, role) == (league.id, "Ice Queens", "member")


def test_default_playoff_size_never_exceeds_a_small_league(db, admin):
    small = new_league(db, admin, size=4, name="Small")
    big = new_league(db, admin, size=10, name="Big")
    assert db.get(LeagueSettings, small.id).playoff_teams == 4
    assert db.get(LeagueSettings, big.id).playoff_teams == 6


def test_new_leagues_get_two_ir_spots(db, admin):
    league = new_league(db, admin, size=10)
    assert db.get(LeagueSettings, league.id).roster_ir == 2
