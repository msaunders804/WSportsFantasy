import re
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    Draft,
    FantasyTeam,
    FantasyWeek,
    Game,
    League,
    LineupLock,
    Matchup,
    Player,
    ProTeam,
    RosterEntry,
    SkaterGameStats,
)
from undertow.fantasy import auth, leagues, lineups, matchup_jobs, matchup_view
from undertow.web import accounts
from undertow.web import app as web_app

pytestmark = pytest.mark.db

PASSWORD = "a long enough password"
BASE = 995_000
HOME, AWAY = 9_801, 9_802


@pytest.fixture(autouse=True)
def fresh():
    for limiter in (accounts.login_ip_limiter, accounts.login_email_limiter):
        limiter._events.clear()


@pytest.fixture
def world(fantasy_db):
    """Week 1 is over and finalized; week 3 is the current week with a game in progress.

    T0 v T1 and T2 v T3 each week. Week 1 scores: 2, 4, 6, 6. Week 3 so far: T0 has 4.3, T1 has 2.3.
    """
    now = datetime.now(UTC)
    monday = now.date() - timedelta(days=now.date().weekday())
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY]})
    with Session(fantasy_db) as s:
        s.add_all([ProTeam(id=HOME, name="H", code="HOM"), ProTeam(id=AWAY, name="A", code="AWY")])
        for n, start in (
            (1, monday - timedelta(days=14)),
            (2, monday - timedelta(days=7)),
            (3, monday),
        ):
            s.add(
                FantasyWeek(
                    season_id=11,
                    week_no=n,
                    start_date=start,
                    end_date=start + timedelta(days=6),
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
                completed_at=now - timedelta(days=60),
            )
        )
        s.flush()
        teams = [
            s.scalar(select(FantasyTeam.id).where(FantasyTeam.owner_id == u.id)) for u in users
        ]
        # Two forwards per team (one scores in each game) and a goalie for team 0.
        old_game = Game(
            id=9_900_201,
            season_id=11,
            home_team_id=HOME,
            away_team_id=AWAY,
            start_utc=datetime.combine(monday - timedelta(days=12), datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=20),
            status="final",
            result_type="REG",
        )
        live_game = Game(
            id=9_900_202,
            season_id=11,
            home_team_id=HOME,
            away_team_id=AWAY,
            start_utc=now - timedelta(minutes=30),
            status="live",
        )
        s.add_all([old_game, live_game])
        s.flush()
        ids = {}
        for t_index, (old_goals, live_goals) in enumerate(((1, 2), (2, 1), (3, 0), (3, 0))):
            p = Player(
                id=BASE + t_index + 1,
                first_name="Pat",
                last_name=f"F{t_index}",
                position="F",
                pro_team_id=HOME,
                status="active",
            )
            s.add(p)
            s.flush()
            s.add(RosterEntry(league_id=league.id, team_id=teams[t_index], player_id=p.id))
            s.flush()
            lineups.place_new_player(s, teams[t_index], p)
            s.add(
                SkaterGameStats(
                    game_id=old_game.id,
                    player_id=p.id,
                    team_id=HOME,
                    toi_seconds=1200,
                    goals=old_goals,
                )
            )
            s.add(LineupLock(team_id=teams[t_index], player_id=p.id, game_id=old_game.id, slot="F"))
            if live_goals:
                s.add(
                    SkaterGameStats(
                        game_id=live_game.id,
                        player_id=p.id,
                        team_id=HOME,
                        toi_seconds=600,
                        goals=live_goals,
                        shots=3,
                    )
                )
            s.add(
                LineupLock(team_id=teams[t_index], player_id=p.id, game_id=live_game.id, slot="F")
            )
            ids[t_index] = p.id
        for week in (1, 3):
            s.add(
                Matchup(
                    league_id=league.id,
                    week_no=week,
                    last_week_no=week,
                    home_team_id=teams[0],
                    away_team_id=teams[1],
                )
            )
            s.add(
                Matchup(
                    league_id=league.id,
                    week_no=week,
                    last_week_no=week,
                    home_team_id=teams[2],
                    away_team_id=teams[3],
                )
            )
        s.commit()
        matchup_jobs.refresh_matchups(s, now)
        info = {"league": league.id, "teams": teams, "players": ids}
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

    yield type("W", (), {"ids": info, "client": staticmethod(client_for), "engine": fantasy_db})
    with fantasy_db.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE draft, roster_entry, lineup_slot, lineup_lock, matchup, fantasy_week, "
                "game CASCADE"
            )
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY]})


def url(world, path=""):
    return f"/leagues/{world.ids['league']}/{path}"


# ---- the matchup tab ----


def test_the_current_week_shows_a_live_scoreboard_with_my_team_on_the_left(world):
    page = world.client(0).get(url(world, "matchup")).text
    assert "Week 3" in page and "Team 0" in page and "Team 1" in page
    assert (
        '<strong class="ms-total">4.3</strong>' in page
        and '<strong class="ms-total">2.3</strong>' in page
    )
    assert page.index("Team 0") < page.index("Team 1")
    assert "Team 0 leads by 2.0" in page and ">Live<" in page
    assert 'data-refresh="30"' in page and "Playing now" in page
    assert "2G 0A 3 SOG" in page  # the live player's stat line so far
    # The other team's manager sees themselves on the left instead.
    other = world.client(1).get(url(world, "matchup")).text
    assert other.index("Team 1") < other.index("Team 0") and "You" in other


def test_a_finished_week_shows_final_totals_and_no_live_refresh(world):
    page = world.client(0).get(url(world, "matchup?week=1")).text
    assert "Week 1" in page and ">Final<" in page
    assert (
        '<strong class="ms-total">2.0</strong>' in page
        and '<strong class="ms-total">4.0</strong>' in page
    )
    assert "Team 1 won by 2.0" in page and 'data-refresh="0"' in page
    assert "Done for the week" in page


def test_weeks_are_navigable_and_bad_weeks_fall_back_to_the_nearest(world):
    base = url(world, "matchup")
    one = world.client(0).get(base + "?week=1").text
    assert "Week 1" in one and "week=3" in one and "week=2" not in one  # only weeks with matchups
    assert "Week 3" in world.client(0).get(base + "?week=banana").text
    assert "Week 3" in world.client(0).get(base + "?week=99").text
    assert (
        "Week 1" in world.client(0).get(base + "?week=2").text
        or "Week 3" in world.client(0).get(base + "?week=2").text
    )


def test_other_matchups_are_listed_and_viewable(world):
    page = world.client(0).get(url(world, "matchup")).text
    assert "Rest of the league" in page and "Team 2" in page and "Team 3" in page
    with Session(world.engine) as s:
        other = s.scalar(
            select(Matchup).where(
                Matchup.week_no == 3, Matchup.home_team_id == world.ids["teams"][2]
            )
        )
        other_id = other.id
    seen = world.client(0).get(url(world, f"matchup/{other_id}"))
    assert seen.status_code == 200 and "Team 2" in seen.text and "Team 3" in seen.text


def test_access_rules(world, fantasy_db):
    assert (
        TestClient(web_app.create_app(), follow_redirects=False)
        .get(url(world, "matchup"))
        .status_code
        == 303
    )
    with Session(fantasy_db) as s:
        auth.register_user(s, email="out@example.com", display_name="Out", password=PASSWORD)
    c = TestClient(web_app.create_app(), follow_redirects=False)
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"csrf": token, "email": "out@example.com", "password": PASSWORD})
    assert c.get(url(world, "matchup")).status_code == 404
    assert c.get(url(world, "standings")).status_code == 404
    assert world.client(0).get(url(world, "matchup/999999")).status_code == 404


def test_a_league_without_a_schedule_explains_itself(world, fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM matchup"))
    page = world.client(0).get(url(world, "matchup"))
    assert page.status_code == 200 and "schedule is not ready" in page.text


def test_names_are_escaped_and_pages_are_not_cached(world, fantasy_db):
    with Session(fantasy_db) as s:
        s.get(FantasyTeam, world.ids["teams"][1]).name = "<script>alert(1)</script>"
        s.commit()
    resp = world.client(0).get(url(world, "matchup"))
    assert "<script>alert(1)</script>" not in resp.text and "&lt;script&gt;" in resp.text
    assert resp.headers["cache-control"] == "no-store"


# ---- the league tab ----


def test_standings_show_the_record_points_and_results(world):
    page = world.client(0).get(url(world, "standings")).text
    # Week 1 (final): Team 1 beat Team 0 (4 to 2); Team 2 and Team 3 tied at 6.
    assert (
        page.index("Team 1") < page.index("Team 2") < page.index("Team 3") < page.rindex("Team 0")
    )
    assert "Week 3" in page and "Week 1 results" in page
    assert '<tr class="mine">' in page  # my row is highlighted
    assert "Includes weeks that just ended" not in page  # finalized, so not provisional


def test_a_finished_but_not_yet_finalized_week_marks_standings_provisional(world, fantasy_db):
    with Session(fantasy_db) as s:
        for m in s.scalars(select(Matchup).where(Matchup.week_no == 1)):
            m.status, m.home_points, m.away_points, m.finalized_at = "final", None, None, None
        s.commit()
    page = world.client(0).get(url(world, "standings")).text
    assert "Includes weeks that just ended" in page and "Team 1" in page


def test_a_finalized_result_does_not_change_when_stats_are_corrected_later(world, fantasy_db):
    with Session(fantasy_db) as s:
        row = s.scalar(
            select(SkaterGameStats).where(
                SkaterGameStats.player_id == world.ids["players"][0],
                SkaterGameStats.game_id == 9_900_201,
            )
        )
        row.goals = 9
        s.commit()
    page = world.client(0).get(url(world, "matchup?week=1")).text
    assert (
        '<strong class="ms-total">2.0</strong>' in page
    )  # the stored total, not the corrected 18.0


def test_the_tabs_point_at_the_new_pages_and_draft_only_shows_before_the_season(world, fantasy_db):
    page = world.client(0).get(url(world, "standings")).text
    for href in (
        f"/leagues/{world.ids['league']}/matchup",
        f"/leagues/{world.ids['league']}/team",
        f"/leagues/{world.ids['league']}/standings",
        "/stats",
    ):
        assert f'href="{href}"' in page
    assert f'href="/leagues/{world.ids["league"]}/draft"' not in page  # in season: no Draft tab
    with Session(fantasy_db) as s:
        s.get(League, world.ids["league"]).status = "drafting"
        s.commit()
    drafting = world.client(0).get(url(world, "standings")).text
    assert f'href="/leagues/{world.ids["league"]}/draft"' in drafting


# ---- the view helpers ----


def test_goalie_and_skater_details_add_up_counted_games_only():
    from undertow.fantasy.matchup_scoring import GameScore, PlayerWeek

    def game(i, counted, stats, status="final"):
        return GameScore(
            i,
            datetime.now(UTC),
            date.today(),
            "AWY",
            True,
            status,
            "F",
            counted,
            stats,
            Fraction(0),
        )

    skater = PlayerWeek(
        1,
        "S",
        "F",
        "HOM",
        "F",
        [
            game(1, True, {"G": "1", "A": "2", "SOG": "4", "Hits": "1", "Blk": "0", "PPP": "0"}),
            game(2, True, {"G": "0", "A": "1", "SOG": "2", "Hits": "0", "Blk": "2", "PPP": "0"}),
            game(3, False, {"G": "9", "A": "9", "SOG": "9", "Hits": "9", "Blk": "9", "PPP": "0"}),
        ],
    )
    assert matchup_view._detail(skater) == "1G 3A 6 SOG 1 H 2 B"
    keeper = PlayerWeek(
        2,
        "G",
        "G",
        "HOM",
        "G",
        [
            game(1, True, {"Dec": "W", "SA": "30", "SV": "28", "GA": "2", "SO": "0"}),
            game(2, True, {"Dec": "L", "SA": "10", "SV": "10", "GA": "0", "SO": "1"}),
        ],
    )
    assert matchup_view._detail(keeper) == "38 SV · 2 GA · W · SO"
    assert matchup_view._detail(PlayerWeek(3, "N", "F", "HOM", "F", [])) == ""
