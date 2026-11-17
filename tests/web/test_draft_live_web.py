import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from undertow.data.models import FantasyTeam, Player
from undertow.fantasy import auth, draft_room, draft_view, leagues
from undertow.web import accounts, draft_live
from undertow.web import app as web_app

pytestmark = pytest.mark.db

PASSWORD = "a long enough password"
FIRST_ID = 920_000
POSITIONS = ["F"] * 36 + ["D"] * 16 + ["G"] * 8


@pytest.fixture(autouse=True)
def fresh_state():
    draft_view.clear_pool_cache()
    draft_live.pick_limiter._events.clear()
    for limiter in (accounts.login_ip_limiter, accounts.login_email_limiter):
        limiter._events.clear()
    yield
    draft_view.clear_pool_cache()


@pytest.fixture
def world(fantasy_db):
    """A full 4-team private league (not started) plus a logged-in client per manager."""
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": FIRST_ID})
    with Session(fantasy_db) as s:
        s.add_all(
            Player(
                id=FIRST_ID + i, first_name=f"P{i}", last_name=pos, position=pos, status="active"
            )
            for i, pos in enumerate(POSITIONS)
        )
        s.commit()
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
        outsider = auth.register_user(
            s, email="outsider@example.com", display_name="Out", password=PASSWORD
        ).user
        ids = {"league": league.id, "users": [u.id for u in users], "outsider": outsider.id}
    clients = {}

    def client_for(email):
        if email not in clients:
            c = TestClient(web_app.create_app(), follow_redirects=False)
            html = c.get("/login").text
            token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
            resp = c.post("/login", data={"csrf": token, "email": email, "password": PASSWORD})
            assert resp.status_code == 303
            clients[email] = c
        return clients[email]

    yield type("W", (), {"ids": ids, "client": staticmethod(client_for), "engine": fantasy_db})
    with fantasy_db.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE draft_queue, draft_pick, draft, roster_entry, lineup_slot, lineup_lock, "
                "matchup "
                "CASCADE"
            )
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": FIRST_ID})


def boss(w):
    return w.client("u0@example.com")


def start(w):
    c = boss(w)
    html = c.get("/leagues").text
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
    return c.post(
        f"/leagues/{w.ids['league']}/draft/start", data={"csrf": token, "pick_seconds": "60"}
    )


def room_data(client, w):
    html = client.get(f"/leagues/{w.ids['league']}/draft").text
    raw = re.search(r'<script id="draft-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(raw.group(1))


def room_token(client, w):
    return room_data(client, w)["csrf"]


def api(client, w, method, path, body=None, token=None):
    url = f"/leagues/{w.ids['league']}/draft{path}"
    if method == "GET":
        return client.get(url)
    headers = {"x-csrf-token": token if token is not None else room_token(client, w)}
    return client.post(url, content=json.dumps(body or {}), headers=headers)


def state(client, w):
    return api(client, w, "GET", "/state").json()


def clients_by_turn(w):
    """(on-the-clock client, another client) for the current pick."""
    st = state(boss(w), w)
    with Session(w.engine) as s:
        owner = s.get(FantasyTeam, st["on_clock"]).owner_id
    idx = w.ids["users"].index(owner)
    on = w.client(f"u{idx}@example.com")
    other = w.client(f"u{(idx + 1) % 4}@example.com")
    return on, other


# ---- before the draft ----


def test_lobby_waits_for_a_full_league_and_hides_the_league_from_outsiders(fantasy_db):
    with Session(fantasy_db) as s:
        u = auth.register_user(
            s, email="solo@example.com", display_name="Solo", password=PASSWORD
        ).user
        league, _ = leagues.register_league(
            s, u, name="Half", season_id=11, size=4, team_name="Only"
        )
        lid = league.id
    c = TestClient(web_app.create_app(), follow_redirects=False)
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"csrf": token, "email": "solo@example.com", "password": PASSWORD})
    page = c.get(f"/leagues/{lid}/draft")
    assert page.status_code == 200 and "3 still to join" in page.text
    assert "Start the draft now" not in page.text
    assert c.get(f"/leagues/{lid + 99}/draft").status_code == 404


def test_only_a_manager_sees_start_and_only_they_can_use_it(world):
    page = boss(world).get(f"/leagues/{world.ids['league']}/draft")
    assert "Start the draft now" in page.text
    member = world.client("u1@example.com")
    assert "Start the draft now" not in member.get(f"/leagues/{world.ids['league']}/draft").text
    html = member.get("/leagues").text
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
    denied = member.post(
        f"/leagues/{world.ids['league']}/draft/start", data={"csrf": token, "pick_seconds": "60"}
    )
    assert denied.status_code == 403
    outsider = world.client("outsider@example.com")
    assert outsider.get(f"/leagues/{world.ids['league']}/draft").status_code == 404
    assert api(outsider, world, "GET", "/state").status_code == 404


def test_missing_history_is_called_out_to_the_commissioner_and_in_the_room(world):
    lobby = boss(world).get(f"/leagues/{world.ids['league']}/draft")
    assert "No past-season stats are loaded" in lobby.text
    start(world)
    data = room_data(boss(world), world)
    assert data["ranking_basis"] is None


def test_starting_opens_the_room_for_everyone(world):
    resp = start(world)
    assert resp.status_code == 303 and resp.headers["location"].endswith("/draft")
    for i in range(4):
        page = world.client(f"u{i}@example.com").get(f"/leagues/{world.ids['league']}/draft")
        assert page.status_code == 200 and 'id="draft-data"' in page.text
        data = json.loads(
            re.search(
                r'id="draft-data" type="application/json">(.*?)</script>', page.text, re.S
            ).group(1)
        )
        assert len(data["teams"]) == 4 and data["me"] in range(4) and data["rounds"] == 11
        assert any(p["id"] >= FIRST_ID for p in data["pool"])
    assert start(world).status_code == 422  # a second start is refused


# ---- state ----


def test_state_shows_the_clock_and_only_recommends_to_the_team_on_the_clock(world):
    start(world)
    on, other = clients_by_turn(world)
    s_on, s_other = state(on, world), state(other, world)
    assert s_on["status"] == "live" and s_on["pick_no"] == 1 and s_on["picks"] == []
    assert 55 <= s_on["seconds_left"] <= 60
    assert s_on["on_clock"] == s_on["my_team"] and len(s_on["recommend"]) >= 1
    assert s_other["on_clock"] != s_other["my_team"] and s_other["recommend"] == []
    assert s_on["needs"]["picks_left"] == 11 and s_on["can_manage"] == (on is boss(world))


def test_staff_who_are_not_in_the_league_can_watch_and_control(world):
    with Session(world.engine) as s:
        admin = auth.register_user(
            s, email="staff@example.com", display_name="Staff", password=PASSWORD
        ).user
        admin.is_admin = True
        s.commit()
    start(world)
    staff = world.client("staff@example.com")
    st = state(staff, world)
    assert st["my_team"] is None and st["can_manage"] is True and st["recommend"] == []
    assert api(staff, world, "POST", "/control", {"action": "pause"}).status_code == 200


# ---- picking ----


def test_a_pick_needs_the_csrf_header_and_a_valid_body(world):
    start(world)
    on, _ = clients_by_turn(world)
    url = f"/leagues/{world.ids['league']}/draft/pick"
    assert on.post(url, content=json.dumps({"player_id": FIRST_ID})).status_code == 403
    assert (
        api(on, world, "POST", "/pick", {"player_id": FIRST_ID}, token="wrong").status_code == 403
    )
    assert api(on, world, "POST", "/pick", {"player_id": "x"}).status_code == 422
    assert api(on, world, "POST", "/pick", {}).status_code == 422
    bad = on.post(url, content="not json", headers={"x-csrf-token": room_token(on, world)})
    assert bad.status_code == 422


def test_picks_move_the_draft_along_and_refuse_the_wrong_team(world):
    start(world)
    on, other = clients_by_turn(world)
    refused = api(other, world, "POST", "/pick", {"player_id": FIRST_ID})
    assert refused.status_code == 409
    assert "not your turn" in refused.json()["error"] and refused.json()["state"]["pick_no"] == 1
    ok = api(on, world, "POST", "/pick", {"player_id": FIRST_ID})
    assert ok.status_code == 200
    after = ok.json()["state"]
    assert (
        after["pick_no"] == 2 and after["picks"][0][2] == FIRST_ID and after["picks"][0][3] is False
    )
    assert after["on_clock"] != after["my_team"]
    assert after["roster"]["F"] == [FIRST_ID]
    taken = api(on, world, "POST", "/pick", {"player_id": FIRST_ID})
    assert taken.status_code == 409


def test_queue_is_private_saved_and_cleaned(world):
    start(world)
    on, other = clients_by_turn(world)
    saved = api(other, world, "POST", "/queue", {"player_ids": [FIRST_ID + 5, FIRST_ID + 6]})
    assert saved.json()["queue"] == [FIRST_ID + 5, FIRST_ID + 6]
    assert state(other, world)["queue"] == [FIRST_ID + 5, FIRST_ID + 6]
    assert state(on, world)["queue"] == []  # nobody else sees it
    api(on, world, "POST", "/pick", {"player_id": FIRST_ID + 5})
    assert state(other, world)["queue"] == [FIRST_ID + 6]  # a drafted player leaves it
    assert api(other, world, "POST", "/queue", {"player_ids": "no"}).status_code == 422
    assert api(other, world, "POST", "/queue", {"player_ids": [True]}).status_code == 422


# ---- commissioner controls ----


def test_only_managers_control_the_draft(world):
    start(world)
    member = world.client("u1@example.com")
    assert api(member, world, "POST", "/control", {"action": "pause"}).status_code == 403
    assert api(boss(world), world, "POST", "/control", {"action": "explode"}).status_code == 422


def test_pause_blocks_picks_resume_restores_and_undo_reverses(world):
    start(world)
    on, _ = clients_by_turn(world)
    paused = api(boss(world), world, "POST", "/control", {"action": "pause"}).json()["state"]
    assert paused["status"] == "paused" and paused["seconds_left"] > 0
    blocked = api(on, world, "POST", "/pick", {"player_id": FIRST_ID})
    assert blocked.status_code == 409 and "paused" in blocked.json()["error"]
    resumed = api(boss(world), world, "POST", "/control", {"action": "resume"}).json()["state"]
    assert resumed["status"] == "live"
    api(on, world, "POST", "/pick", {"player_id": FIRST_ID})
    undone = api(boss(world), world, "POST", "/control", {"action": "undo"}).json()["state"]
    assert undone["picks"] == [] and undone["pick_no"] == 1
    again = api(boss(world), world, "POST", "/control", {"action": "undo"})
    assert again.status_code == 409 and "no pick to undo" in again.json()["error"]


def test_the_page_and_state_are_not_cached_and_escape_names(world):
    with Session(world.engine) as s:
        team = s.scalar(select(FantasyTeam).where(FantasyTeam.name == "Team 1"))
        team.name = "<script>alert(1)</script>"
        s.commit()
    start(world)
    page = boss(world).get(f"/leagues/{world.ids['league']}/draft")
    assert "<script>alert(1)</script>" not in page.text  # tojson escapes the angle brackets
    assert api(boss(world), world, "GET", "/state").headers["cache-control"] == "no-store"
    assert "u0@example.com" not in page.text  # emails are never sent to the page


def test_a_completed_draft_still_loads(world):
    start(world)
    with Session(world.engine) as s:
        dr = draft_room.draft_for_league(s, world.ids["league"])
        dr.status = "complete"
        dr.current_pick = 45
        dr.deadline = None
        s.commit()
    st = state(boss(world), world)
    assert st["status"] == "complete" and st["on_clock"] is None and st["seconds_left"] is None
    assert boss(world).get(f"/leagues/{world.ids['league']}/draft").status_code == 200
