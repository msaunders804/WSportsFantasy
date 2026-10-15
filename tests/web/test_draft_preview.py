import json
import re

import pytest
from fastapi.testclient import TestClient

from undertow.fantasy import draft
from undertow.web import draft_preview
from undertow.web.app import app

client = TestClient(app)


def step(picks, seed=1):
    return client.post("/preview/draft/step", json={"picks": picks, "seed": seed})


def test_draft_page_embeds_the_pool_and_draft_order():
    resp = client.get("/preview/draft")
    assert resp.status_code == 200
    assert "Start the practice draft" in resp.text
    assert "Unofficial and unaffiliated" in resp.text
    data = json.loads(
        re.search(r'id="draft-data" type="application/json">(.*?)</script>', resp.text, re.S).group(
            1
        )
    )
    assert len(data["teams"]) == 10 and data["rounds"] == 11
    assert data["teams"][data["me"]]["mine"] is True
    ranks = [p["rank"] for p in data["pool"]]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert {"id", "name", "pos", "team", "fpg", "gp", "pos_rank"} <= data["pool"][0].keys()


def test_clock_and_pace_are_clamped():
    html = client.get("/preview/draft?clock=100000&pace=-5").text
    data = json.loads(
        re.search(r'id="draft-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    )
    assert data["clock"] == 300 and data["pace"] == 0


def test_first_step_is_a_computer_pick_for_team_zero():
    body = step([]).json()
    assert (body["pick_no"], body["round"], body["on_clock"]) == (1, 1, 0)
    assert isinstance(body["cpu_pick"], int) and not body["complete"]
    assert body["needs"] == {"F": 4, "D": 2, "G": 1, "picks_left": 11}
    assert len(body["recommend"]) == 3 and all(r["reason"] for r in body["recommend"])


def test_when_it_is_my_turn_there_is_no_cpu_pick_and_a_recommendation():
    me = draft_preview.ME
    picks = []
    while True:
        body = step(picks).json()
        if body["on_clock"] == me:
            break
        picks.append(body["cpu_pick"])
    assert body["cpu_pick"] is None and len(picks) == me  # I pick 4th, after 3 computer picks
    assert body["recommend"][0]["tag"] == "Best pick"
    assert body["recommend"][0]["id"] not in picks


def test_server_driven_draft_completes_with_a_valid_team_for_me():
    me = draft_preview.ME
    picks: list[int] = []
    for _ in range(110):
        body = step(picks, seed=9).json()
        picks.append(body["recommend"][0]["id"] if body["on_clock"] == me else body["cpu_pick"])
    final = step(picks, seed=9).json()
    assert final["complete"] and final["cpu_pick"] is None
    roster = final["roster"]
    assert len(roster["F"]) == 3 and len(roster["UTIL"]) == 1
    assert len(roster["D"]) == 2 and len(roster["G"]) == 1 and len(roster["BN"]) == 4
    assert final["needs"] == {"F": 0, "D": 0, "G": 0, "picks_left": 0}
    assert len(set(picks)) == 110


@pytest.mark.parametrize(
    "payload",
    [{"picks": [999999999]}, {"picks": [1, 1]}],
)
def test_bad_picks_are_rejected(payload):
    assert client.post("/preview/draft/step", json=payload).status_code == 400


def test_too_many_picks_is_rejected():
    assert (
        client.post("/preview/draft/step", json={"picks": list(range(1, 200))}).status_code == 422
    )


def test_same_seed_gives_same_computer_pick():
    assert step([], seed=5).json()["cpu_pick"] == step([], seed=5).json()["cpu_pick"]


def test_draft_room_is_linked_from_the_league_tab():
    assert 'href="/preview/draft"' in client.get("/preview/league").text


def test_draft_uses_the_same_service_config():
    assert draft_preview.CFG == draft.DraftConfig()
    assert draft_preview.CFG.total_picks == 110
