"""Fantasy tables: accounts, sessions, leagues, settings, teams, membership, audit log.

Roles and statuses are short strings guarded by CHECK constraints (the allowed values live in
`LEAGUE_ROLES` / `LEAGUE_STATUSES`). All times are UTC.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from undertow.data import Base

LEAGUE_ROLES = ("member", "commissioner", "co_commissioner")
LEAGUE_STATUSES = ("setup", "drafting", "in_season", "playoffs", "complete")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stored lower-cased and trimmed.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(60))
    # None until the person sets one from the emailed link (accounts made from the waitlist).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    # Undertow staff: may create leagues and remove chat messages.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set to lock an account without deleting it.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthToken(Base):
    """A one-time emailed link: set a first password, or reset a forgotten one."""

    __tablename__ = "auth_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))
    # SHA-256 of the token in the link. The raw token is only ever in the email.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_in("purpose", ("set_password",)), name="purpose_valid"),
        Index("ix_auth_token_user_id", "user_id"),
    )


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    # SHA-256 of the cookie token. The raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_auth_session_user_id", "user_id"),)


class League(Base):
    __tablename__ = "league"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"))
    # Number of teams. Must be even so there is no bye week or median game (Plan 4.5).
    size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="setup", server_default="setup")
    # Anyone holding the code can join until the league is full, so treat it like a password.
    invite_code: Mapped[str] = mapped_column(String(32), unique=True)
    # When this league's draft starts (UTC). None until staff set it; pages then fall back to the
    # general draft window from `fantasy.key_dates`.
    draft_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("size >= 4 AND size <= 12 AND MOD(size, 2) = 0", name="size_even_4_to_12"),
        CheckConstraint(_in("status", LEAGUE_STATUSES), name="status_valid"),
        UniqueConstraint("season_id", "name", name="uq_league_season_name"),
    )


class LeagueSettings(Base):
    __tablename__ = "league_settings"

    league_id: Mapped[int] = mapped_column(
        ForeignKey("league.id", ondelete="CASCADE"), primary_key=True
    )
    roster_f: Mapped[int] = mapped_column(Integer, default=3)
    roster_d: Mapped[int] = mapped_column(Integer, default=2)
    roster_util: Mapped[int] = mapped_column(Integer, default=1)
    roster_g: Mapped[int] = mapped_column(Integer, default=1)
    roster_bench: Mapped[int] = mapped_column(Integer, default=4)
    roster_ir: Mapped[int] = mapped_column(Integer, default=2)
    max_goalies: Mapped[int] = mapped_column(Integer, default=2)
    # Exact per-stat points as strings (ScoringConfig.to_dict), so no float ever enters storage.
    scoring_config: Mapped[dict] = mapped_column(JSONB)
    waiver_hours: Mapped[int] = mapped_column(Integer, default=48)
    trade_review_hours: Mapped[int] = mapped_column(Integer, default=24)
    trade_deadline_week: Mapped[int] = mapped_column(Integer, default=11)
    regular_season_weeks: Mapped[int] = mapped_column(Integer, default=12)
    playoff_teams: Mapped[int] = mapped_column(Integer, default=6)
    # Players lock at their own game's puck drop (Plan 4.3). The only rule for now.
    lock_rule: Mapped[str] = mapped_column(String(20), default="game_start")

    __table_args__ = (
        CheckConstraint(
            "roster_f >= 0 AND roster_d >= 0 AND roster_util >= 0 AND roster_g >= 1 "
            "AND roster_bench >= 0 AND roster_ir >= 0 AND max_goalies >= roster_g",
            name="roster_sane",
        ),
        CheckConstraint(
            "waiver_hours >= 0 AND trade_review_hours >= 0 AND trade_deadline_week >= 1 "
            "AND regular_season_weeks >= 1 AND playoff_teams >= 2",
            name="timing_sane",
        ),
    )


class LeagueMember(Base):
    __tablename__ = "league_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16), default="member", server_default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(_in("role", LEAGUE_ROLES), name="role_valid"),
        UniqueConstraint("league_id", "user_id", name="uq_league_member_league_user"),
        # At most one commissioner and one co-commissioner per league (Plan 4.7).
        Index(
            "uq_league_member_one_commissioner",
            "league_id",
            unique=True,
            postgresql_where=text("role = 'commissioner'"),
        ),
        Index(
            "uq_league_member_one_co_commissioner",
            "league_id",
            unique=True,
            postgresql_where=text("role = 'co_commissioner'"),
        ),
        Index("ix_league_member_user_id", "user_id"),
    )


class FantasyTeam(Base):
    __tablename__ = "fantasy_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id", ondelete="CASCADE"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(40))
    # Set when the draft order is known (reverse of draft order, Plan 4.7).
    waiver_priority: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # One team per person per league.
        UniqueConstraint("league_id", "owner_id", name="uq_fantasy_team_league_owner"),
        Index("uq_fantasy_team_league_name", "league_id", func.lower(name), unique=True),
    )


class AuditLog(Base):
    """Every commissioner or admin action, with before and after, so it can be reviewed."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("league.id", ondelete="SET NULL"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_log_league_id", "league_id"),)
