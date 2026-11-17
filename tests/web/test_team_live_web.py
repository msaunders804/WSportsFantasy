import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    Draft,
    FantasyTeam,
    FantasyWeek,
    Game,
    LineupSlot,
    Player,
    ProTeam,
    RosterEntry,
    SkaterGameStats,
)
from undertow.data.models.lineup import LineupLock
from undertow.fantasy import auth, leagues, lineups
from undertow.web import accounts, team_live
from undertow.web import app as web_app

pytestmark = pytest.mark.db

PASSWORD = "a long enough password"
BASE = 980_000
HOME, AWAY = 9_601, 9_602


@pytest.fixture(autouse=True)
def fresh():
    team_live.move_limiter._events.clear()
    for limiter in (accounts.login_ip_limiter, accounts.login_email_limiter):
        limiter._events.clear()


@pytest.fixture
def world(fantasy_db):
    """A 4-team in-season league, a calendar for now, and teams 0 and 1 with a few players."""
    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday())
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY]})
    with Session(fantasy_db) as s:
        s.add_all([ProTeam(id=HOME, name="H", code="HOM"), ProTeam(id=AWAY, name="A", code="AWY")])
        for n in range(1, 4):
            s.add(
                FantasyWeek(
                    season_id=11,
                    week_no=n,
                    start_date=monday + timedelta(days=7 * (n - 2)),
                    end_date=monday + timedelta(days=7 * (n - 2) + 6),
                    games=4,
                )
            )
        users = [
            auth.register_user(
                s, email=f"u{i}@example.com", display_name=f"User{i}", password=PASSWORD
            ).user
            for i in range(4)
        ]
        league, _ = leagues.register_league(
            s, users[0], name="Friends", season_id=11, size=4, team_name="Team 0"
        )
        for i in range(1, 4):
            leagues.join_league(s, users[i], league.invite_code, f"Team {i}")
        league.status = "in_season"
        s.add(
            Draft(
                league_id=league.id,
                status="complete",
                pick_seconds=60,
                rounds=11,
                order=[],
                order_seed=1,
                current_pick=45,
                completed_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        s.flush()
        teams = [
            s.scalar(select(FantasyTeam.id).where(FantasyTeam.owner_id == u.id)) for u in users
        ]
        players = {}
        for t_index in (0, 1):
            for pos in ("F", "F", "F", "F", "D", "G"):
                pid = BASE + len(players) + 1
                p = Player(
                    id=pid,
                    first_name="Pat",
                    last_name=f"{pos}{pid}",
                    position=pos,
                    pro_team_id=HOME if t_index == 0 else AWAY,
                    status="active",
                )
                s.add(p)
                s.flush()
                s.add(RosterEntry(league_id=league.id, team_id=teams[t_index], player_id=pid))
                s.flush()
                lineups.place_new_player(s, teams[t_index], p)
                players[pid] = (t_index, pos)
        s.commit()
        ids = {
            "league": league.id,
            "teams": teams,
            "users": [u.id for u in users],
            "players": players,
        }
    clients = {}

    def client_for(i):
        if i not in clients:
            c = TestClient(web_app.create_app(), follow_redirects=False)
            token = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/login").text).group(1)
            assert (
                c.post(
                    "/login",
                    data={"csrf": token, "email": f"u{i}@example.com", "password": PASSWORD},
                ).status_code
                == 303
            )
            clients[i] = c
        return clients[i]

    yield type("W", (), {"ids": ids, "client": staticmethod(client_for), "engine": fantasy_db})
    with fantasy_db.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE draft, roster_entry, lineup_slot, lineup_lock, matchup, fantasy_week, "
                "game CASCADE"
            )
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY]})


def page_data(html):
    raw = re.search(r'<script id="team-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(raw.group(1))


def csrf(client):
    html = client.get("/leagues").text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def move(client, world, body, token=None):
    return client.post(
        f"/leagues/{world.ids['league']}/team/move",
        content=json.dumps(body),
        headers={"x-csrf-token": token or csrf(client)},
    )


def player_in(world, team_index, slot=None, pos=None):
    with Session(world.engine) as s:
        rows = lineups.team_lineup(s, world.ids["teams"][team_index])
        return next(
            p.id
            for r, p in rows
            if (slot is None or r.slot == slot) and (pos is None or p.position == pos)
        )


# ---- viewing ----


def test_a_member_sees_their_lineup_with_the_week_and_move_buttons(world):
    page = world.client(0).get(f"/leagues/{world.ids['league']}/team")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "Team 0" in page.text and "Week 2" in page.text and "points this week" in page.text
    assert page.text.count("js-move") >= 3 and "Managed by User0" in page.text
    assert 'aria-current="page"' in page.text and "/stats/player/" in page.text
    assert "Sample data for design preview" not in page.text  # real page, not the mock


def test_the_lineup_shows_every_slot_including_empty_ones(world):
    page = world.client(0).get(f"/leagues/{world.ids['league']}/team").text
    assert (
        page.count('<span class="slot-tag">F</span>') >= 3 and 'class="slot-tag">IR</span>' in page
    )
    assert "Empty" in page  # the open IR spots and the empty bench spots


def test_other_teams_can_be_viewed_but_a_plain_member_cannot_edit_them(world):
    # User 2 is a plain member: they may look at team 0 but get no Move buttons.
    page = world.client(2).get(f"/leagues/{world.ids['league']}/team/{world.ids['teams'][0]}")
    assert (
        page.status_code == 200 and "Team 0" in page.text and "looking at another team" in page.text
    )
    assert "js-move" not in page.text and page_data(page.text)["moves"] == {}
    # They still get them on their own team.
    assert "js-move" in world.client(1).get(f"/leagues/{world.ids['league']}/team").text


def test_the_commissioner_is_offered_moves_on_other_teams(world):
    page = world.client(0).get(f"/leagues/{world.ids['league']}/team/{world.ids['teams'][1]}")
    assert "js-move" in page.text and page_data(page.text)["moves"] != {}


def test_strangers_get_a_404_and_unknown_teams_too(world, fantasy_db):
    with Session(fantasy_db) as s:
        auth.register_user(s, email="out@example.com", display_name="Out", password=PASSWORD)
    c = TestClient(web_app.create_app(), follow_redirects=False)
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"csrf": token, "email": "out@example.com", "password": PASSWORD})
    assert c.get(f"/leagues/{world.ids['league']}/team").status_code == 404
    assert c.get(f"/leagues/{world.ids['league']}/team/{world.ids['teams'][0]}").status_code == 404
    assert world.client(0).get(f"/leagues/{world.ids['league']}/team/999999").status_code == 404
    assert (
        TestClient(web_app.create_app(), follow_redirects=False)
        .get(f"/leagues/{world.ids['league']}/team")
        .status_code
        == 303
    )


def test_weeks_can_be_browsed_and_bad_weeks_fall_back(world):
    base = f"/leagues/{world.ids['league']}/team"
    c = world.client(0)
    one = c.get(base + "?week=1")
    assert "Week 1" in one.text and "js-move" not in one.text  # past weeks are read-only
    assert 'href="?week=2"' in one.text and "‹" in one.text
    assert "Week 2" in c.get(base + "?week=banana").text
    assert "Week 2" in c.get(base + "?week=99").text


def test_a_page_with_no_calendar_still_loads(world, fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM fantasy_week"))
    page = world.client(0).get(f"/leagues/{world.ids['league']}/team")
    assert page.status_code == 200 and "calendar not loaded" in page.text


def test_points_for_the_week_appear_next_to_the_player(world):
    star = player_in(world, 0, "F")
    start = datetime.now(UTC) - timedelta(minutes=10)
    with Session(world.engine) as s:
        s.add(
            Game(
                id=9_900_101,
                season_id=11,
                home_team_id=HOME,
                away_team_id=AWAY,
                start_utc=start,
                status="live",
            )
        )
        s.flush()
        s.add(
            SkaterGameStats(
                game_id=9_900_101,
                player_id=star,
                team_id=HOME,
                toi_seconds=600,
                goals=2,
                assists=1,
                shots=5,
            )
        )
        s.add(
            LineupLock(team_id=world.ids["teams"][0], player_id=star, game_id=9_900_101, slot="F")
        )
        s.commit()
    page = world.client(0).get(f"/leagues/{world.ids['league']}/team").text
    assert (
        '<strong class="big">5.5</strong>' in page and "points this week" in page
    )  # 2 goals 4 + assist 1 + 5 shots 0.5
    assert "🔒" in page  # the player whose game has started is locked


# ---- moving ----


def test_moving_a_player_through_the_page_changes_the_lineup(world):
    c = world.client(0)
    flex, starter = player_in(world, 0, "UTIL"), player_in(world, 0, "F")
    data = page_data(c.get(f"/leagues/{world.ids['league']}/team").text)
    offered = data["moves"][str(flex)]
    assert any(o["slot"] == "F" and o["swap_with"] for o in offered)  # the F slots are full: a swap
    assert any(o["slot"] == "BN" and o["swap_with"] is None for o in offered)  # the bench is open
    swap = next(o for o in offered if o["slot"] == "F" and o["swap_with"] == starter)
    resp = move(
        c,
        world,
        {
            "team_id": world.ids["teams"][0],
            "player_id": flex,
            "to_slot": "F",
            "swap_with": swap["swap_with"],
        },
    )
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    with Session(world.engine) as s:
        assert s.scalar(select(LineupSlot.slot).where(LineupSlot.player_id == flex)) == "F"
        assert s.scalar(select(LineupSlot.slot).where(LineupSlot.player_id == starter)) == "UTIL"


def test_the_move_endpoint_checks_csrf_body_permission_and_rules(world):
    c = world.client(0)
    team = world.ids["teams"][0]
    f = player_in(world, 0, "F")
    url = f"/leagues/{world.ids['league']}/team/move"
    assert c.post(url, content=json.dumps({"team_id": team})).status_code == 403
    assert move(c, world, {}, token="wrong").status_code == 403
    assert move(c, world, {"team_id": "x", "player_id": f, "to_slot": "BN"}).status_code == 422
    assert move(c, world, {"team_id": team, "player_id": f}).status_code == 422
    assert c.post(url, content="nope", headers={"x-csrf-token": csrf(c)}).status_code == 422
    # A plain member cannot move another manager's players.
    other = world.client(2)
    denied = move(other, world, {"team_id": team, "player_id": f, "to_slot": "BN"})
    assert denied.status_code == 403 and "cannot change" in denied.json()["error"]
    # A rule violation comes back as a message, not a crash.
    goalie = player_in(world, 0, "G")
    bad = move(c, world, {"team_id": team, "player_id": goalie, "to_slot": "F"})
    assert bad.status_code == 409 and "cannot play" in bad.json()["error"]
    # A team in some other league (or no such team) is a 404.
    assert move(c, world, {"team_id": 999999, "player_id": f, "to_slot": "BN"}).status_code == 404


def test_a_locked_player_cannot_be_moved_through_the_page(world):
    f = player_in(world, 0, "F")
    with Session(world.engine) as s:
        s.add(
            Game(
                id=9_900_102,
                season_id=11,
                home_team_id=HOME,
                away_team_id=AWAY,
                start_utc=datetime.now(UTC) - timedelta(minutes=5),
                status="live",
            )
        )
        s.commit()
    resp = move(
        world.client(0), world, {"team_id": world.ids["teams"][0], "player_id": f, "to_slot": "BN"}
    )
    assert resp.status_code == 409 and "game has started" in resp.json()["error"]


def test_the_commissioner_can_move_players_on_another_team_and_it_is_audited(world):
    f = player_in(world, 1, "F")
    resp = move(
        world.client(0), world, {"team_id": world.ids["teams"][1], "player_id": f, "to_slot": "BN"}
    )
    assert resp.status_code == 200
    with Session(world.engine) as s:
        assert s.scalar(select(LineupSlot.slot).where(LineupSlot.player_id == f)) == "BN"
        audit = s.execute(
            text("SELECT count(*) FROM audit_log WHERE action = 'lineup.edit'")
        ).scalar()
        assert audit == 1
