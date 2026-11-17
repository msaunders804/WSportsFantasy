"""The dates people are told about, in one place so every page agrees.

Pages read these through the `dates` template variable (`dates.draft.when`). Change a date here
and it changes on the landing page, Fantasy 101 and the account page together. The season and
playoff calendar used for scoring comes from the schedule data, not from this list.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyDate:
    key: str
    when: str
    what: str
    # Shown before `when` in the Fantasy 101 table ("From Nov 20"); left out of running text.
    qualifier: str = ""

    @property
    def table_when(self) -> str:
        return f"{self.qualifier}{self.when}"


KEY_DATES = (
    # Leagues are filled as people sign up, starting Nov 20, and sign-ups stay open until Dec 1.
    KeyDate(
        "invites",
        "Nov 20",
        "Leagues assigned and invites emailed as people sign up",
        qualifier="From ",
    ),
    KeyDate("signups_close", "Dec 1", "Sign-ups close"),
    KeyDate("draft", "Nov 30 to Dec 4", "Live snake draft, after the preseason"),
    KeyDate("season_opens", "Dec 5", "Season opens, Week 1 begins"),
)

DATES = {d.key: d for d in KEY_DATES}
