import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    Game,
    GoalieGameStats,
    League,
    Player,
    ProTeam,
    Season,
    SkaterGameStats,
)
from undertow.fantasy import admin_view, auth, draft_room, leagues
from undertow.fantasy import season_stats as ss
from undertow.web import accounts
from undertow.web import app as web_app

pytestmark = pytest.mark.db

PASSWORD = "a long enough password"
BASE = 940_000
HOME, AWAY = 9_201, 9_202


@pytest.fixture(autouse=True)
def fresh():
    ss.clear_cache()
    for limiter in (accounts.login_ip_limiter, accounts.login_email_limiter):
        limiter._events.clear()
    yield
    ss.clear_cache()


@pytest.fixture
def client(fantasy_db):
    return TestClient(web_app.create_app(), follow_redirects=False)


@pytest.fixture
def season_data(fantasy_db):
    """One finished regular season (id 10) with a forward and a goalie."""
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id IN (:a, :b)"), {"a": HOME, "b": AWAY})
    with Session(fantasy_db) as s:
        s.add_all(
            [ProTeam(id=HOME, name="Home", code="HOM"), ProTeam(id=AWAY, name="Away", code="AWY")]
        )
        s.add(Season(id=10, name="2025-26 Regular Season", is_playoff=False, is_current=False))
        s.add_all(
            [
                Player(
                    id=BASE + 1,
                    first_name="Ada",
                    last_name="<b>Skater</b>",
                    position="F",
                    status="active",
                ),
                Player(
                    id=BASE + 2, first_name="Gia", last_name="Goalie", position="G", status="active"
                ),
            ]
        )
        s.flush()
        s.add(
            Game(
                id=9_900_001,
                season_id=10,
                home_team_id=HOME,
                away_team_id=AWAY,
                start_utc=datetime(2026, 1, 10, 20, tzinfo=UTC),
                status="final",
                result_type="REG",
            )
        )
        s.flush()
        s.add(
            SkaterGameStats(
                game_id=9_900_001,
                player_id=BASE + 1,
                team_id=HOME,
                toi_seconds=1200,
                goals=2,
                assists=1,
                shots=5,
            )
        )
        s.add(
            GoalieGameStats(
                game_id=9_900_001,
                player_id=BASE + 2,
                team_id=AWAY,
                toi_seconds=3600,
                shots_against=30,
                goals_against=2,
                saves=28,
                decision="L",
                shutout=False,
            )
        )
        s.commit()
    yield BASE
    with fantasy_db.begin() as conn:
        conn.execute(text("TRUNCATE skater_game_stats, goalie_game_stats, game CASCADE"))
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id IN (:a, :b)"), {"a": HOME, "b": AWAY})


def login(client, email):
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/login").text).group(1)
    resp = client.post("/login", data={"csrf": token, "email": email, "password": PASSWORD})
    assert resp.status_code == 303


def make_user(engine, email, admin=False):
    with Session(engine) as s:
        u = auth.register_user(
            s, email=email, display_name=email.split("@")[0], password=PASSWORD
        ).user
        u.is_admin = admin
        s.commit()
        return u.id


# ---- stats pages ----


def test_stats_page_is_public_and_shows_scored_players(client, season_data):
    page = client.get("/stats")  # no login
    assert page.status_code == 200
    assert "2025-26 Regular Season" in page.text and "How we score it" in page.text
    assert "public, max-age=300" in page.headers["cache-control"]
    # 2 goals 4 + 1 assist 1 + 5 shots 0.5 = 5.5; the goalie lost: 28 saves 5.6 - 4 = 1.6
    assert "5.5" in page.text and "1.6" in page.text
    assert '<a class="nav-link" href="/stats">Stats</a>' in page.text


def test_player_names_are_escaped(client, season_data):
    page = client.get("/stats")
    assert "<b>Skater</b>" not in page.text and "&lt;b&gt;Skater&lt;/b&gt;" in page.text


def test_stats_page_with_no_history_says_so_instead_of_failing(client, fantasy_db):
    page = client.get("/stats")
    assert page.status_code == 200 and "not loaded yet" in page.text


def test_unknown_or_bad_season_is_a_404(client, season_data):
    assert client.get("/stats?season=999").status_code == 404
    assert client.get("/stats?season=abc").status_code == 404
    assert client.get("/stats?season=10").status_code == 200


def test_player_page_shows_breakdown_and_game_log(client, season_data):
    page = client.get(f"/stats/player/{BASE + 1}")
    assert page.status_code == 200
    assert "Goals" in page.text and "Assists" in page.text and "Shots on goal" in page.text
    assert "vs AWY" in page.text and "Jan 10" in page.text
    assert "#1" in page.text  # rank among players
    goalie = client.get(f"/stats/player/{BASE + 2}")
    assert goalie.status_code == 200 and "Saves" in goalie.text and "@ HOM" in goalie.text


def test_player_page_404s_for_unknown_players_and_seasons(client, season_data):
    assert client.get(f"/stats/player/{BASE + 77}").status_code == 404
    assert client.get(f"/stats/player/{BASE + 1}?season=999").status_code == 404


# ---- staff overview ----


def test_admin_is_hidden_from_everyone_but_staff(client, fantasy_db):
    assert client.get("/admin").status_code == 303  # logged out: sent to log in
    make_user(fantasy_db, "fan@example.com")
    login(client, "fan@example.com")
    assert client.get("/admin").status_code == 404  # not even a hint that it exists


def test_staff_see_every_league_and_the_link_on_their_account_page(client, fantasy_db):
    make_user(fantasy_db, "staff@example.com", admin=True)
    with Session(fantasy_db) as s:
        boss = auth.register_user(
            s, email="boss@example.com", display_name="Boss", password=PASSWORD
        ).user
        leagues.register_league(s, boss, name="Friends", season_id=11, size=4, team_name="T0")
        staff = s.scalar(select(auth.User).where(auth.User.email == "staff@example.com"))
        leagues.create_league(s, staff, name="House One", season_id=11, size=10)
    login(client, "staff@example.com")
    page = client.get("/admin")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "Friends" in page.text and "House One" in page.text
    assert "boss@example.com" in page.text and "Undertow staff" in page.text
    assert "1 / 4" in page.text and "0 / 10" in page.text
    assert 'href="/admin"' in client.get("/account").text


# ---- the overview's data ----


def league_with_teams(engine, n=4, name="Friends"):
    with Session(engine) as s:
        boss = auth.register_user(
            s, email=f"{name}0@example.com", display_name="Boss", password=PASSWORD
        ).user
        league, _ = leagues.register_league(
            s, boss, name=name, season_id=11, size=n, team_name="T0"
        )
        users = [boss]
        for i in range(1, n):
            u = auth.register_user(
                s, email=f"{name}{i}@example.com", display_name=f"U{i}", password=PASSWORD
            ).user
            leagues.join_league(s, u, league.invite_code, f"T{i}")
            users.append(u)
        return league.id, boss.id


def test_flags_call_out_full_leagues_missing_times_and_a_stalled_clock(fantasy_db):
    league_id, boss_id = league_with_teams(fantasy_db)
    now = datetime(2026, 11, 30, 18, tzinfo=UTC)
    with Session(fantasy_db) as s:
        (row,) = admin_view.league_rows(s, now=now)
        assert row.teams == 4 and row.pending_invites == 0
        assert "Full, draft not started" in row.flags and "No draft time set" in row.flags
        league = s.get(League, league_id)
        league.draft_at = now - timedelta(hours=1)
        s.commit()
        assert "Draft time has passed" in admin_view.league_rows(s, now=now)[0].flags
        boss = s.get(auth.User, boss_id)
        started = draft_room.start_draft(s, boss, league_id, now=now, seed=1)
        ok = admin_view.league_rows(s, now=now + timedelta(seconds=30))[0]
        assert ok.draft_status == "live" and ok.picks_made == 0 and ok.picks_total == 44
        assert ok.flags == []
        stalled = admin_view.league_rows(s, now=now + timedelta(seconds=90 + 60))[0]
        assert any("worker" in f for f in stalled.flags)
        draft_room.pause(s, boss, started.id, now=now + timedelta(seconds=10))
        assert "Draft is paused" in admin_view.league_rows(s, now=now)[0].flags


def test_summary_counts(fantasy_db):
    league_with_teams(fantasy_db, name="One")
    with Session(fantasy_db) as s:
        pending = auth.register_user(
            s, email="late@example.com", display_name="Late", password=PASSWORD
        ).user
        pending.password_hash = None
        s.commit()
        totals = admin_view.summary(s)
    assert totals.users == 5 and totals.users_without_password == 1
    assert totals.leagues_by_status == {"setup": 1} and totals.leagues_by_kind == {"private": 1}
    assert totals.open_seats == 0
