"""Build the translation training table: NCAA seasons before the draft -> first PWHL season.

Offline. Inputs: cached NCAA box scores and scoreboards (data/ncaa_cache), data/cohort_players.csv
(from match_cohorts.py). Each box score is assigned to a season by its scoreboard date. A cohort
player is looked up at her draft school (a transfer to another school is not followed; those players
show as missing for earlier seasons). Writes data/training_table.csv and prints coverage plus a
rough sanity check (rank correlation of NCAA vs PWHL points per game).
"""

import csv
import glob
import importlib.util
import json
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "data" / "ncaa_cache"
spec = importlib.util.spec_from_file_location("pull", HERE / "pull.py")
pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pull)  # reuse ALIASES and file_date

FINAL = {2024: "2023-24", 2025: "2024-25"}
PRIOR = {2024: "2022-23", 2025: "2023-24"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


def num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def label(d: date) -> str:
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def slug(school: str) -> str:
    return pull.ALIASES.get(school) or school.lower().replace(" ", "-").replace(".", "")


game_season: dict[str, str] = {}
for f in CACHE.glob("scoreboard__*.json"):
    season = label(pull.file_date(f))
    for g in json.loads(f.read_text()).get("games", []):
        game_season[g["game"]["gameID"]] = season

lines: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
for f in glob.glob(str(CACHE / "game__*__boxscore.json")):
    gid = Path(f).name.split("__")[1]
    season = game_season.get(gid)
    if season is None:
        continue
    d = json.load(open(f))
    teams = {str(t["teamId"]): t["seoname"] for t in d["teams"]}
    for tb in d["teamBoxscore"]:
        seo = teams.get(str(tb["teamId"]))
        for p in tb["playerStats"]:
            if not p.get("participated"):
                continue
            L = lines[(season, seo, norm(p["lastName"]), norm(p["firstName"]))]
            L["gp"] += 1
            for k in ("goals", "assists", "shots", "saves", "goalsAllowed", "goalieMinutes"):
                L[k] += num(p.get(k))


def lookup(season: str, seo: str, player: str):
    first, _, last = player.partition(" ")
    fn, ln = norm(first), norm(last.replace(" ", ""))
    hits = [
        v
        for (s, t, last_k, first_k), v in lines.items()
        if s == season
        and t == seo
        and (last_k == ln or last_k.startswith(ln) or ln.startswith(last_k))
        and (first_k[:3] == fn[:3] or first_k[:1] == fn[:1])
    ]
    return hits[0] if len(hits) == 1 else None


cohort = list(csv.DictReader(open(HERE / "data/cohort_players.csv", encoding="utf-8")))
out_rows, cover = [], defaultdict(int)
for c in cohort:
    year, seo = int(c["class"]), slug(c["school"])
    row = dict(c)
    for tag, season in (("final", FINAL[year]), ("prior", PRIOR[year])):
        L = lookup(season, seo, c["player"])
        cover[(tag, "found" if L else "missing")] += 1
        for k in ("gp", "goals", "assists", "shots", "saves", "goalsAllowed", "goalieMinutes"):
            row[f"ncaa_{tag}_{k}"] = int(L[k]) if L else ""
        row[f"ncaa_{tag}_season"] = season
    out_rows.append(row)

out = HERE / "data/training_table.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
    w.writeheader()
    w.writerows(out_rows)
print(f"cohort players: {len(cohort)}; wrote {out.name}")
print("coverage:", {f"{a}_{b}": v for (a, b), v in sorted(cover.items())})

sk = [
    r
    for r in out_rows
    if r["kind"] == "S"
    and r["ncaa_final_gp"] != ""
    and int(r["ncaa_final_gp"]) >= 10
    and int(r["pwhl_gp"]) >= 10
]
xs = [
    (int(r["ncaa_final_goals"]) + int(r["ncaa_final_assists"])) / int(r["ncaa_final_gp"])
    for r in sk
]
ys = [(int(r["pwhl_goals"]) + int(r["pwhl_assists"])) / int(r["pwhl_gp"]) for r in sk]


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for pos, i in enumerate(order):
        r[i] = pos
    return r


def corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    return cov / ((sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5)


if len(sk) > 5:
    print(f"\nskaters with >=10 GP in both NCAA final season and PWHL: {len(sk)}")
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    print(f"  mean NCAA points/GP {mx:.2f} vs mean PWHL points/GP {my:.2f}")
    print(f"  Spearman rank correlation (NCAA ppg vs PWHL ppg): {corr(ranks(xs), ranks(ys)):.2f}")
