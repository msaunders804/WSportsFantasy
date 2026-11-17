import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import FantasyWeek, Game, ProTeam, Season
from undertow.fantasy import season_calendar as cal

FIXTURE = Path(__file__).parent.parent / "fixtures" / "hockeytech" / "schedule_2026-27.json"


@pytest.fixture(scope="module")
def schedule():
    rows = json.loads(FIXTURE.read_text())["SiteKit"]["Schedule"]
    return [
        (date.fromisoformat(g["date_played"]), int(g["home_team"]), int(g["visiting_team"]))
        for g in rows
    ]


@pytest.fixture(scope="module")
def weeks(schedule):
    return cal.build_weeks(d for d, _, _ in schedule)


# ---- the real 2026-27 schedule ----


def test_the_real_schedule_gives_the_weeks_the_plan_describes(weeks):
    assert len(weeks) == 18
    first = weeks[0]
    assert (first.start, first.end) == (date(2026, 12, 5), date(2026, 12, 13))
    assert first.games == 16  # opening weekend (6) merged with the first full week (10)
    assert (weeks[11].start, weeks[11].end) == (date(2027, 2, 22), date(2027, 2, 28))  # week 12
    assert (weeks[12].start, weeks[12].end) == (date(2027, 3, 8), date(2027, 3, 14))  # week 13
    assert weeks[-1].end == date(2027, 4, 18)
    assert sum(w.games for w in weeks) == 180
    assert all(w.start.weekday() == 0 for w in weeks[1:]) and all(
        w.end.weekday() == 6 for w in weeks
    )


def test_the_all_star_week_is_skipped_and_leaves_no_gap_in_numbering(weeks):
    assert [w.week_no for w in weeks] == list(range(1, 19))
    assert cal.week_for_date(weeks, date(2027, 3, 3)) is None  # the empty break week
    assert cal.week_for_date(weeks, date(2027, 2, 28)).week_no == 12
    assert cal.week_for_date(weeks, date(2027, 3, 8)).week_no == 13
    assert cal.week_for_date(weeks, date(2026, 12, 1)) is None  # before the season


def test_every_game_date_lands_in_exactly_one_week(schedule, weeks):
    for day, _, _ in schedule:
        owners = [w for w in weeks if w.contains(day)]
        assert len(owners) == 1


def test_playoff_rounds_are_three_two_week_rounds_after_week_12(weeks):
    assert cal.playoff_rounds(weeks, 12) == [(13, 14), (15, 16), (17, 18)]
    assert cal.playoff_rounds(weeks, 14) == [(15, 16), (17, 18)]
    assert cal.playoff_rounds(weeks, 18) == []


def test_every_team_plays_at_least_two_games_in_every_playoff_round(schedule, weeks):
    assert cal.thin_rounds(weeks, schedule, 12) == []


def test_thin_rounds_reports_a_team_with_too_few_games(schedule, weeks):
    # Remove one team's games from round 1 (weeks 13 and 14) to simulate a postponement.
    lo, hi = weeks[12].start, weeks[13].end
    team = 9
    trimmed = [g for g in schedule if not (lo <= g[0] <= hi and team in g[1:])]
    thin = cal.thin_rounds(weeks, trimmed, 12)
    assert (1, team, 0) in thin and all(round_no == 1 for round_no, _, _ in thin)


# ---- the rules on small made-up seasons ----


def test_no_games_means_no_weeks():
    assert cal.build_weeks([]) == []


def test_a_season_opening_on_a_monday_has_a_normal_first_week():
    weeks = cal.build_weeks([date(2026, 12, 7), date(2026, 12, 12), date(2026, 12, 14)])
    assert [(w.start, w.end, w.games) for w in weeks] == [
        (date(2026, 12, 7), date(2026, 12, 13), 2),
        (date(2026, 12, 14), date(2026, 12, 20), 1),
    ]


def test_a_sunday_opener_is_merged_into_the_next_full_week():
    weeks = cal.build_weeks([date(2026, 12, 6), date(2026, 12, 9)])
    assert len(weeks) == 1 and (weeks[0].start, weeks[0].end) == (
        date(2026, 12, 6),
        date(2026, 12, 13),
    )


def test_several_empty_weeks_in_a_row_are_all_skipped():
    weeks = cal.build_weeks([date(2026, 12, 7), date(2026, 12, 28)])
    assert [w.week_no for w in weeks] == [1, 2] and weeks[1].start == date(2026, 12, 28)


def test_eastern_date_uses_new_york_time_not_utc():
    late = datetime(2026, 12, 6, 2, 30, tzinfo=UTC)  # 9:30 pm Saturday in New York
    assert cal.eastern_date(late) == date(2026, 12, 5)
    assert cal.eastern_date(datetime(2026, 12, 5, 15, tzinfo=UTC)) == date(2026, 12, 5)


# ---- storing the calendar ----


@pytest.mark.db
class TestSync:
    @pytest.fixture
    def db(self, fantasy_db):
        with Session(fantasy_db) as s:
            s.add_all(
                [ProTeam(id=9301, name="A", code="AAA"), ProTeam(id=9302, name="B", code="BBB")]
            )
            s.add(Season(id=12, name="2027-28 Regular Season", is_playoff=False, is_current=False))
            s.commit()
            yield s
        with fantasy_db.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("TRUNCATE game CASCADE"))
            conn.execute(text("DELETE FROM pro_team WHERE id IN (9301, 9302)"))

    def add_game(self, db, gid, day):
        db.add(
            Game(
                id=gid,
                season_id=12,
                home_team_id=9301,
                away_team_id=9302,
                start_utc=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=20),
                status="scheduled",
            )
        )

    def test_sync_stores_the_weeks_and_is_repeatable(self, db):
        for i, d in enumerate([date(2027, 1, 2), date(2027, 1, 12), date(2027, 1, 13)]):
            self.add_game(db, 100 + i, d)
        db.commit()
        first = cal.sync_calendar(db, 12, today=date(2026, 12, 1))
        assert [(w.week_no, w.start, w.end, w.games) for w in first] == [
            (1, date(2027, 1, 2), date(2027, 1, 10), 1),
            (2, date(2027, 1, 11), date(2027, 1, 17), 2),
        ]
        again = cal.sync_calendar(db, 12, today=date(2026, 12, 1))
        assert again == first
        assert len(db.scalars(select(FantasyWeek)).all()) == 2

    def test_future_weeks_follow_a_schedule_change_but_started_weeks_never_move(self, db):
        for i, d in enumerate([date(2027, 1, 2), date(2027, 1, 12)]):
            self.add_game(db, 100 + i, d)
        db.commit()
        cal.sync_calendar(db, 12, today=date(2026, 12, 1))
        # Week 2's game moves a week later, and a new game appears on the first weekend.
        db.get(Game, 101).start_utc = datetime(2027, 1, 19, 20, tzinfo=UTC)
        self.add_game(db, 102, date(2027, 1, 3))
        db.commit()
        # Week 1 has started (today is Jan 5): its dates are kept. Week 2 has not: it updates.
        after = cal.sync_calendar(db, 12, today=date(2027, 1, 5))
        by_no = {w.week_no: w for w in after}
        assert (by_no[1].start, by_no[1].end) == (date(2027, 1, 2), date(2027, 1, 10))
        assert by_no[1].games == 2
        assert (by_no[2].start, by_no[2].end) == (date(2027, 1, 18), date(2027, 1, 24))

    def test_a_season_with_no_games_stores_nothing(self, db):
        assert cal.sync_calendar(db, 12, today=date(2026, 12, 1)) == []


def test_eastern_dates_from_the_feed_timestamps_match_the_feed_dates():
    """The database path derives the date from the stored UTC start time, so it must agree with
    the date the feed itself prints for every game in the real schedule."""
    rows = json.loads(FIXTURE.read_text())["SiteKit"]["Schedule"]
    for g in rows:
        start = datetime.fromisoformat(g["GameDateISO8601"]).astimezone(UTC)
        assert cal.eastern_date(start) == date.fromisoformat(g["date_played"]), g["game_id"]
