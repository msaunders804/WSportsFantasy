"""Sample data for the /preview mockups.

Generated from real 2025-26 PWHL box scores by `undertow.web.build_preview` and committed as
`preview_data.json`. Real: player names, teams, positions, jersey numbers and stat lines, scored
with our scoring engine. Invented: the fantasy teams and managers, the draft, records,
standings and transactions. The pages say so. Replaced by real data in later phases.
"""

import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("preview_data.json").read_text(encoding="utf-8"))

LEAGUE = _DATA["league"]
MY_SLUG = _DATA["my_team"]
TEAMS = _DATA["teams"]  # slug -> {name, owner, record, score, bench_score, lineup, bench}
MATCHUPS = _DATA["matchups"]  # [{id, a, b}]; matchup 1 is the viewer's
PLAYERS = _DATA["players"]  # [name, pos, team, fp/g, season fp, gp, available, trend, why, id]
PLAYER_PAGES = _DATA["player_pages"]  # str(player id) -> page data
FEATURED_PLAYER_ID = _DATA["featured_player"]
STANDINGS = _DATA["standings"]
ACTIVITY = _DATA["activity"]
IR: list = []  # empty on purpose: we do not invent injuries for real players
