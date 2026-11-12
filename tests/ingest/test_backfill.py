import json

import pytest
from sqlalchemy import select

from undertow.data.models import Player, ProTeam, Season
from undertow.ingest import backfill, store, sync
from undertow.ingest.parsing import parse_roster

pytestmark = pytest.mark.db


def seed_production_like(session_factory, feed):
    """Season 11 current, current teams, and one player rostered on a current team."""
    with session_factory() as s:
        sync.sync_seasons(s, feed, current_season_id=11)
        sync.sync_teams(s, feed, 11)
        sync.sync_roster(s, feed, 1, 10)
        s.commit()


def test_backfill_leaves_current_state_alone(session_factory, feed):
    seed_production_like(session_factory, feed)
    with session_factory() as s:
        before_teams = {t.id: (t.name, t.code) for t in s.scalars(select(ProTeam))}
        rostered = {p.id: (p.pro_team_id, p.status) for p in s.scalars(select(Player))}

    totals = backfill.backfill_history(session_factory, feed, [8])
    # Fixtures only carry two box scores; the rest of the 120 games have none to fetch.
    assert totals["games"] == 120

    with session_factory() as s:
        assert s.get(Season, 11).is_current and not s.get(Season, 8).is_current
        assert {t.id: (t.name, t.code) for t in s.scalars(select(ProTeam))} == before_teams
        for pid, state in rostered.items():
            assert (s.get(Player, pid).pro_team_id, s.get(Player, pid).status) == state
        # New players from old box scores: no team, inactive.
        new = [p for p in s.scalars(select(Player)) if p.id not in rostered]
        assert new and all(p.pro_team_id is None and p.status == "inactive" for p in new)


def test_backfill_refuses_current_and_worker_seasons(session_factory, feed):
    seed_production_like(session_factory, feed)
    with pytest.raises(SystemExit):
        backfill.backfill_history(session_factory, feed, [11])


def test_roster_sync_reactivates_history_player_but_keeps_injured(session_factory, feed):
    box_only = parse_roster(json.loads(feed.roster(1, 10).body))[0]
    with session_factory() as s:
        sync.sync_teams(s, feed, 11)  # the roster row references a team
        store.ensure_players(s, [box_only], history_only=True)
        s.commit()
        assert s.get(Player, box_only.id).status == "inactive"
        store.upsert_roster_players(s, [box_only])
        s.commit()
        assert s.get(Player, box_only.id).status == "active"
        s.get(Player, box_only.id).status = "injured"
        s.commit()
        store.upsert_roster_players(s, [box_only])
        s.commit()
        assert s.get(Player, box_only.id).status == "injured"
