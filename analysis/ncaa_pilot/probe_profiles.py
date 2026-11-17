"""Probe: what school/college information does the PWHL player profile hold?

Fetches a few profiles (cached under ./data/) for no-PWHL-history players from the coverage
audit and prints the fields that could name a school. Run from repo root with HOCKEYTECH_KEY set.
"""

import json
import re
import sys
from pathlib import Path

from undertow.ingest.feed import HockeyTechFeed
from undertow.ingest.parsing import strip_jsonp
from undertow.ingest.replay import CachingFeed

HERE = Path(__file__).parent
rows = json.loads((HERE.parent / "pwhl_coverage_audit/output/audit_rows.json").read_text())
new = [r for r in rows if r["roster_season"] == 10 and r["n_pwhl_seasons"] == 0]
want = int(sys.argv[1]) if len(sys.argv) > 1 else 8
# a spread of statuses so we see how Draftee / Signed / Camp Invitee profiles differ
picked, seen = [], {}
for r in new:
    if seen.get(r["status"], 0) < max(1, want // 4) and len(picked) < want:
        picked.append(r)
        seen[r["status"]] = seen.get(r["status"], 0) + 1

feed = CachingFeed(HockeyTechFeed(), HERE / "data")
for r in picked:
    d = strip_jsonp(feed.player_profile(int(r["player_id"]), 10).body)
    print(f"\n== {r['name']} ({r['status']}, {r['country']}) ==")
    info = d.get("info", {})
    print(" commitment:", info.get("commitment"), "| drafts:", json.dumps(info.get("drafts"))[:200])
    bio = re.sub(r"<[^>]+>", " | ", info.get("bio") or "")
    print(" bio:", bio[:500])
    print(" draftInfo:", json.dumps(d.get("draftInfo"))[:300])
