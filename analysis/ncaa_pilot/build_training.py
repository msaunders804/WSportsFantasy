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


STAT_KEYS = ("gp", "goals", "assists", "shots", "saves", "goalsAllowed", "goalieMinutes")


def _name_parts(player: str) -> tuple[str, str]:
    first, _, last = player.partition(" ")
    return norm(first), norm(last.replace(" ", ""))


def _collect(season: str, fn: str, ln: str, seo: str | None):
    """Full-name rows for this player (same last name, first name sharing 3 letters), plus
    initial-only rows ("G Philips"), which box scores use for the same player in some games."""
    full, initial = {}, {}
    for (s, t, last_k, first_k), v in lines.items():
        if s != season or (seo is not None and t != seo):
            continue
        if not (last_k == ln or last_k.startswith(ln) or ln.startswith(last_k)):
            continue
        if first_k[:3] == fn[:3] and len(first_k) >= 3:
            full[(t, last_k, first_k)] = v
        elif len(first_k) == 1 and first_k == fn[:1]:
            initial[(t, last_k, first_k)] = v
    return full, initial


def lookup(season: str, seo: str, player: str):
    """(stats, school_slug) or (None, None). Tries the draft school, then any school (transfer).

    Ambiguous cases (two different full first names at the same school, or full-name rows at more
    than one school) return None rather than guess. Initial-only rows are merged in only when no
    other player with the same last name and initial exists at that school that season.
    """
    fn, ln = _name_parts(player)
    for scope in (seo, None):
        full, initial = _collect(season, fn, ln, scope)
        firsts = {(t, f) for (t, _, f) in full}
        schools = {t for (t, _) in firsts}
        if not full or len(schools) != 1:
            if len(schools) > 1 and scope is None:
                return None, None
            continue
        t = next(iter(schools))
        names_here = {f for (tt, f) in firsts if tt == t}
        if len(names_here) > 1 and fn not in names_here:
            return None, None
        keys = [k for k in full if k[0] == t and (len(names_here) == 1 or k[2] == fn)]
        merged: dict[str, float] = defaultdict(float)
        for k in keys:
            for sk in STAT_KEYS:
                merged[sk] += full[k][sk]
        same_initial_others = {f for (tt, f) in firsts if tt == t} - {fn}
        if not same_initial_others:
            for k, v in initial.items():
                if k[0] == t:
                    for sk in STAT_KEYS:
                        merged[sk] += v[sk]
        return merged, t
    return None, None


cohort = list(csv.DictReader(open(HERE / "data/cohort_players.csv", encoding="utf-8")))
out_rows, cover = [], defaultdict(int)
for c in cohort:
    year, seo = int(c["class"]), slug(c["school"])
    row = dict(c)
    for tag, season in (("final", FINAL[year]), ("prior", PRIOR[year])):
        L, found_at = lookup(season, seo, c["player"])
        how = (
            "missing"
            if L is None
            else ("found_draft_school" if found_at == seo else "found_other_school")
        )
        cover[(tag, how)] += 1
        for k in STAT_KEYS:
            row[f"ncaa_{tag}_{k}"] = int(L[k]) if L else ""
        row[f"ncaa_{tag}_season"] = season
        row[f"ncaa_{tag}_school"] = found_at or ""
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


# ---- 2026 class: NCAA lines for the players we will apply the translation to ----
targets = list(csv.DictReader(open(HERE / "data/ncaa_targets.csv", encoding="utf-8")))
applied = []
for t in targets:
    L, found_at = lookup("2025-26", slug(t["school"]), t["name"])
    if L is None:
        continue
    applied.append(
        {
            "player": t["name"],
            "pos": t["pos"],
            "school": t["school"],
            "pick": t["pick"],
            "pwhl_team": t["pwhl_team"],
            "ncaa_school_slug": found_at,
            **{f"ncaa_{k}": int(L[k]) for k in STAT_KEYS},
        }
    )
with open(HERE / "data/applied_2026.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(applied[0]))
    w.writeheader()
    w.writerows(applied)
print(f"2026 class lines: {len(applied)} of {len(targets)} targets -> applied_2026.csv")
