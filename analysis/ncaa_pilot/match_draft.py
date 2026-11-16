"""Match the 2026 draft list (school per player) to our no-PWHL-history camp players.

Offline: reads analysis/ncaa_pilot/data/draft_2026.csv and the coverage audit output.
Matching key is accent-stripped last name plus first-name compatibility (prefix or shared
initial, for Katie/Katelyn, Tory/Victoria style nicknames); ambiguous or missing matches are
listed for manual review rather than guessed.
"""

import csv
import json
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum() or c == " ").strip()


def split(name: str) -> tuple[str, str]:
    parts = norm(name).split()
    return (parts[0], "".join(parts[1:])) if parts else ("", "")


draft = list(csv.DictReader(open(HERE / "data/draft_2026.csv", encoding="utf-8")))
rows = json.loads((HERE.parent / "pwhl_coverage_audit/output/audit_rows.json").read_text())
camp = [r for r in rows if r["roster_season"] == 10]
nohist = [r for r in camp if r["n_pwhl_seasons"] == 0]

matched, unmatched_draft = [], []
used = set()
for d in draft:
    f, last = split(d["player"])
    cands = [
        r
        for r in camp
        if split(r["name"])[1] == last
        and (split(r["name"])[0][:1] == f[:1] or split(r["name"])[0].startswith(f[:3]))
    ]
    if len(cands) == 1:
        matched.append((d, cands[0]))
        used.add(cands[0]["player_id"])
    else:
        unmatched_draft.append((d, [c["name"] for c in cands]))

in_nohist = [(d, r) for d, r in matched if r["n_pwhl_seasons"] == 0]
print(f"Draft picks: {len(draft)}; matched to a 2026 camp roster player: {len(matched)}")
print(f"  of those with no PWHL history: {len(in_nohist)} (our no-history pool is {len(nohist)})")
print(f"No-history players NOT on the draft list (school unknown): {len(nohist) - len(in_nohist)}")
print("Unmatched draft picks:", [(d["pick"], d["player"], c) for d, c in unmatched_draft])

ncaa = [(d, r) for d, r in matched if d["league_or_ncaa"] == "NCAA"]
print(f"\nNCAA-listed picks matched to camp rosters: {len(ncaa)}")
schools = Counter(d["school_or_club"] for d, _ in ncaa)
print(f"Distinct NCAA schools: {len(schools)}")
print(sorted(schools.items(), key=lambda kv: (-kv[1], kv[0])))
out = HERE / "data/ncaa_targets.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(
        ["player_id", "name", "pos", "pwhl_team", "status", "n_pwhl_seasons", "school", "pick"]
    )
    for d, r in ncaa:
        w.writerow(
            [
                r["player_id"],
                r["name"],
                r["pos"],
                r["team"],
                r["status"],
                r["n_pwhl_seasons"],
                d["school_or_club"],
                d["pick"],
            ]
        )
print("wrote", out)
nh_unknown = [r for r in nohist if r["player_id"] not in used]
print(
    "\nNo-history players not on the draft list, by status:",
    dict(Counter(r["status"] for r in nh_unknown)),
)
print("  by country:", dict(Counter(r["country"] for r in nh_unknown).most_common(6)))
