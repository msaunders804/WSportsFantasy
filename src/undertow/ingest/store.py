"""Raw snapshot storage and idempotent upserts for the normalized tables."""

import hashlib
import json
from dataclasses import asdict
from functools import lru_cache
from importlib import resources

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from undertow.data.models import (
    Game,
    GoalieGameStats,
    PenaltyEvent,
    Player,
    ProTeam,
    RawSnapshot,
    Season,
    ShotEvent,
    SkaterGameStats,
)
from undertow.ingest.dto import (
    BoxScoreDTO,
    GameDTO,
    PlayByPlayDTO,
    PlayerDTO,
    SeasonDTO,
    TeamDTO,
)
from undertow.ingest.feed import RawResponse
from undertow.ingest.parsing import strip_jsonp


@lru_cache
def conference_map() -> dict[str, str]:
    text = resources.files("undertow.data").joinpath("conferences.json").read_text()
    return json.loads(text)


def save_snapshot(session: Session, raw: RawResponse) -> tuple[RawSnapshot | None, object]:
    """Store the response unless it is identical to the latest one for the same URL.

    Returns (new snapshot or None if unchanged, decoded payload).
    """
    payload = strip_jsonp(raw.body)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    latest = session.scalar(
        select(RawSnapshot.content_hash)
        .where(RawSnapshot.url == raw.source_url)
        .order_by(RawSnapshot.fetched_at.desc(), RawSnapshot.id.desc())
        .limit(1)
    )
    if latest == digest:
        return None, payload
    snap = RawSnapshot(
        url=raw.source_url, fetched_at=raw.fetched_at, content_hash=digest, payload=payload
    )
    session.add(snap)
    session.flush()
    return snap, payload


def upsert_seasons(
    session: Session, seasons: list[SeasonDTO], current_id: int | None = None
) -> None:
    for s in seasons:
        stmt = insert(Season).values(
            id=s.id,
            name=s.name,
            start_date=s.start,
            end_date=s.end,
            is_playoff=s.is_playoff,
            is_current=(s.id == current_id),
        )
        update = {
            "name": stmt.excluded.name,
            "start_date": stmt.excluded.start_date,
            "end_date": stmt.excluded.end_date,
            "is_playoff": stmt.excluded.is_playoff,
        }
        if current_id is not None:
            update["is_current"] = stmt.excluded.is_current
        session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=update))


def upsert_teams(session: Session, teams: list[TeamDTO]) -> None:
    conferences = conference_map()
    for t in teams:
        stmt = insert(ProTeam).values(
            id=t.id,
            name=t.name,
            code=t.code,
            city=t.city,
            nickname=t.nickname,
            logo_url=t.logo_url,
            conference=conferences.get(t.code),
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    c: getattr(stmt.excluded, c)
                    for c in ("name", "code", "city", "nickname", "logo_url", "conference")
                },
            )
        )


def ensure_teams(session: Session, teams: list[TeamDTO]) -> None:
    """Create team rows that are missing; never overwrite (used by the history backfill)."""
    conferences = conference_map()
    for t in teams:
        session.execute(
            insert(ProTeam)
            .values(
                id=t.id,
                name=t.name,
                code=t.code,
                city=t.city,
                nickname=t.nickname,
                logo_url=t.logo_url,
                conference=conferences.get(t.code),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )


def _player_values(p: PlayerDTO) -> dict:
    return {
        "id": p.id,
        "person_id": p.person_id,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "position": p.position.value if p.position else None,
        "raw_position": p.raw_position,
        "pro_team_id": p.team_id,
        "jersey": p.jersey,
        "feed_status": p.feed_status,
        "birthdate": p.birthdate,
    }


def upsert_roster_players(session: Session, players: list[PlayerDTO]) -> None:
    """Roster data is authoritative for team, jersey and feed status, so it overwrites.

    A player created by the history backfill is `inactive` with no team; appearing on a roster
    makes them `active`. Any other availability status (injured, ltir) is left alone.
    """
    status = Player.__table__.c.status
    for p in players:
        stmt = insert(Player).values(**_player_values(p))
        set_ = {k: getattr(stmt.excluded, k) for k in _player_values(p) if k != "id"}
        set_["status"] = case((status == "inactive", "active"), else_=status)
        session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=set_))


def ensure_players(session: Session, players: list[PlayerDTO], history_only: bool = False) -> None:
    """Create bare rows for box-score players the roster sync has not seen; never overwrite.

    `history_only` (the backfill of past seasons) creates them `inactive` with no team, so a
    retired player never shows up as active or on a current team.
    """
    for p in players:
        values = _player_values(p)
        if history_only:
            values.update(pro_team_id=None, status="inactive")
        session.execute(
            insert(Player).values(**values).on_conflict_do_nothing(index_elements=["id"])
        )


def upsert_games(session: Session, games: list[GameDTO]) -> None:
    for g in games:
        values = {
            "id": g.id,
            "season_id": g.season_id,
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "start_utc": g.start_utc,
            "status": g.status.value,
            "status_text": g.status_text,
            "result_type": g.result_type.value if g.result_type else None,
            "home_goals": g.home_goals,
            "away_goals": g.away_goals,
            "venue_name": g.venue_name,
            "venue_location": g.venue_location,
            "notes": g.notes,
            "feed_last_modified": g.last_modified,
        }
        stmt = insert(Game).values(**values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={k: getattr(stmt.excluded, k) for k in values if k != "id"},
            )
        )


def upsert_box_score(session: Session, box: BoxScoreDTO, history_only: bool = False) -> None:
    """Replace this game's stat lines with the latest box score (idempotent)."""
    ensure_players(session, box.players, history_only=history_only)
    game = session.get(Game, box.game_id)
    if game is None:
        raise ValueError(f"game {box.game_id} is not in the schedule; sync the schedule first")
    game.status = box.status.value
    game.status_text = box.status_text
    game.result_type = box.result_type.value if box.result_type else None

    for model, rows in (
        (SkaterGameStats, [asdict(s) for s in box.skaters]),
        (GoalieGameStats, [asdict(g) for g in box.goalies]),
    ):
        for row in rows:
            row.pop("position", None)
            row["game_id"] = box.game_id
            stmt = insert(model).values(**row)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["game_id", "player_id"],
                    set_={
                        k: getattr(stmt.excluded, k)
                        for k in row
                        if k not in ("game_id", "player_id")
                    },
                )
            )


def upsert_play_by_play(session: Session, pbp: PlayByPlayDTO, history_only: bool = False) -> None:
    """Replace this game's shot and penalty rows with the latest play-by-play (idempotent)."""
    if session.get(Game, pbp.game_id) is None:
        raise ValueError(f"game {pbp.game_id} is not in the schedule; sync the schedule first")
    ensure_players(session, pbp.players, history_only=history_only)
    session.execute(delete(ShotEvent).where(ShotEvent.game_id == pbp.game_id))
    session.execute(delete(PenaltyEvent).where(PenaltyEvent.game_id == pbp.game_id))
    session.flush()
    session.add_all(ShotEvent(game_id=pbp.game_id, **asdict(s)) for s in pbp.shots)
    session.add_all(PenaltyEvent(game_id=pbp.game_id, **asdict(p)) for p in pbp.penalties)
