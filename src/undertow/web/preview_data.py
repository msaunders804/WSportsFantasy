"""Sample data for the /preview mockups.

Generated from real 2025-26 PWHL box scores by `undertow.web.build_preview` and committed as
`preview_data.json`. Real: player names, teams, positions and stat lines, scored with our scoring
engine. Invented: the fantasy teams and managers, the draft, standings and transactions. The
pages say so. Replaced by real data in later phases.
"""

import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("preview_data.json").read_text(encoding="utf-8"))

LEAGUE = _DATA["league"]
MY_TEAM = _DATA["my_team"]
OPP_TEAM = _DATA["opp_team"]
MY_LINEUP = _DATA["my_lineup"]
OPP_LINEUP = _DATA["opp_lineup"]
BENCH = _DATA["bench"]
IR: list = []  # empty on purpose: we do not invent injuries for real players
PLAYERS = _DATA["players"]
STANDINGS = _DATA["standings"]
SCHEDULE = _DATA["schedule"]
ACTIVITY = _DATA["activity"]
