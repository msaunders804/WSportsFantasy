"""Pre-launch interest list for the landing page."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from undertow.db import Base


class WaitlistSignup(Base):
    __tablename__ = "waitlist_signup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stored lower-cased and trimmed; unique so a repeat signup is a no-op.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    first_name: Mapped[str | None] = mapped_column(String(100))
    # "new", "some" or "lots": how much fantasy the person has played.
    experience: Mapped[str | None] = mapped_column(String(10))
    utm_source: Mapped[str | None] = mapped_column(String(100))
    utm_medium: Mapped[str | None] = mapped_column(String(100))
    utm_campaign: Mapped[str | None] = mapped_column(String(100))
    # When they ticked the consent box (the form refuses to submit without it).
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
