"""Training cohort: 2024 and 2025 draftees who played NCAA and then played in the PWHL.

Offline. Joins draft lists (school at draft) to PWHL per-season lines (data/pwhl_season_lines.csv,
exported from the dev DB). The outcome season is the first PWHL season at or after the one that
follows the draft (class 2024 -> season 5 = 2024-25; class 2025 -> season 8 = 2025-26). Matches are
accent-stripped last name plus first-name prefix/initial; ambiguous or missing ones are listed.
Writes data/cohort_players.csv and prints the (school, NCAA season) pulls it implies.
"""

import csv
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
MIN_SEASON = {2024: 5, 2025: 8}  # first PWHL regular season after the draft
# NCAA seasons (start year) to pull per class: the last two seasons before the draft
NCAA_SEASONS = {2024: ["2022-23", "2023-24"], 2025: ["2023-24", "2024-25"]}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


lines = list(csv.DictReader(open(HERE / "data/pwhl_season_lines.csv", encoding="utf-8")))
by_last = defaultdict(list)
for r in lines:
    by_last[norm(r["last_name"])].append(r)

cohort, report = [], {}
for year in (2024, 2025):
    draft = list(csv.DictReader(open(HERE / f"data/draft_{year}.csv", encoding="utf-8")))
    stats = Counter()
    unmatched, ambiguous = [], []
    for d in draft:
        stats["picks"] += 1
        if d["league_or_ncaa"] != "NCAA":
            stats["not_ncaa"] += 1
            continue
        stats["ncaa"] += 1
        first, _, last = d["player"].partition(" ")
        fn, ln = norm(first), norm(last.replace(" ", ""))
        # exact surname, or one is the start of the other (Curl vs Curl-Salemme)
        pool = [r for k, rs in by_last.items() if k.startswith(ln) or ln.startswith(k) for r in rs]
        cands = [
            r
            for r in pool
            if norm(r["first_name"])[:3] == fn[:3] or norm(r["first_name"])[:1] == fn[:1]
        ]
        pids = {r["player_id"] for r in cands}
        if len(pids) > 1:
            ambiguous.append((d["player"], sorted(pids)))
            continue
        # also try the surname alone for compound/hyphen cases
        if not pids:
            unmatched.append((d["player"], d["school_or_club"]))
            continue
        seasons = sorted(
            ((int(r["season_id"]), r) for r in cands if int(r["season_id"]) >= MIN_SEASON[year]),
            key=lambda t: t[0],
        )
        if not seasons:
            stats["in_pwhl_db_but_not_after_draft"] += 1
            continue
        sid, r = seasons[0]
        stats["played_pwhl"] += 1
        cohort.append(
            {
                "class": year,
                "player": d["player"],
                "pos": d["position"],
                "school": d["school_or_club"],
                "player_id": r["player_id"],
                "pwhl_season": sid,
                "pwhl_gp": r["gp"],
                "pwhl_goals": r["goals"],
                "pwhl_assists": r["assists"],
                "pwhl_shots": r["shots"],
                "pwhl_toi_s": r["toi_s"],
                "kind": r["kind"],
            }
        )
    report[year] = (stats, unmatched, ambiguous)

for year, (stats, unmatched, ambiguous) in report.items():
    print(f"\n== {year} draft ==  {dict(stats)}")
    print(
        " NCAA picks never seen in a PWHL game after the draft (rights only or name mismatch):",
        len(unmatched),
    )
    if ambiguous:
        print(" ambiguous:", ambiguous)

out = HERE / "data/cohort_players.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(cohort[0]))
    w.writeheader()
    w.writerows(cohort)
print(f"\ncohort rows: {len(cohort)} -> {out.name}")
print("by position kind:", Counter(c["kind"] for c in cohort))
gps = sorted(int(c["pwhl_gp"]) for c in cohort)
print(
    "PWHL GP in outcome season (min/median/max):",
    gps[0],
    gps[len(gps) // 2],
    gps[-1],
    "| players with GP>=10:",
    sum(g >= 10 for g in gps),
)

pulls = defaultdict(set)
for c in cohort:
    for season in NCAA_SEASONS[c["class"]]:
        pulls[season].add(c["school"])
print("\nNCAA (season -> schools) implied by the cohort:")
for s in sorted(pulls):
    print(f"  {s}: {len(pulls[s])} schools")
(HERE / "data/cohort_pulls.json").write_text(
    json.dumps({k: sorted(v) for k, v in pulls.items()}, indent=1, ensure_ascii=False)
)
print("unmatched examples:", [u for _, (st, un, am) in report.items() for u in un][:12])
