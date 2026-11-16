# ruff: noqa: E501
"""Fit the NCAA -> PWHL translation for skaters on the 2024/2025 draftee training table.

Reads research.translation_training (+ player birthdates, NCAA team scoring) from DATABASE_URL.
Outcome: PWHL points per game in the first PWHL season. Models are fixed in advance (below), compared
by leave-one-out error and by training on one draft class and predicting the other. Only players with
at least MIN_GP games in both the final NCAA season and the PWHL season are used. Goalies (7 rows) are
not modelled, only described. Run: uv run --group analysis python analysis/translation_fit/fit.py
"""

import csv
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, text

from undertow.config import get_settings

MIN_GP = 10
rng = np.random.default_rng(20261116)

eng = create_engine(get_settings().database_url)
with eng.connect() as c:
    rows = (
        c.execute(
            text(
                """
        select t.*, p.birthdate, s.start_date as pwhl_start, d.pick as draft_pick
        from research.translation_training t
        join player p on p.id = t.pwhl_player_id
        join season s on s.id = t.pwhl_season_id
        left join research.draft_pick d on d.draft_year = t.draft_year and d.player = t.player
        """
            )
        )
        .mappings()
        .all()
    )
    games = c.execute(
        text("select season, home_slug, away_slug, home_score, away_score from research.ncaa_game")
    ).all()

# NCAA team scoring environment per (season, school): goals for / game, goals against / game
gf, ga, gp_team = {}, {}, {}
for season, h, a, hs, as_ in games:
    if hs is None or as_ is None:
        continue
    for team, f, g in ((h, hs, as_), (a, as_, hs)):
        k = (season, team)
        gf[k] = gf.get(k, 0) + f
        ga[k] = ga.get(k, 0) + g
        gp_team[k] = gp_team.get(k, 0) + 1


def is_d(pos: str) -> int:
    return int(pos.upper().startswith("D") or pos.upper() in ("LD", "RD"))


data = []
for r in rows:
    if r["kind"] != "S" or not r["final_gp"] or r["final_gp"] < MIN_GP or r["pwhl_gp"] < MIN_GP:
        continue
    k = (r["final_season"], r["final_school_slug"])
    if k not in gp_team:
        continue
    pts = r["final_goals"] + r["final_assists"]
    team_gf_pg = gf[k] / gp_team[k]
    # two-season rate: games-weighted points per game over final and prior season, when we have both
    pp_pts = (r["prior_goals"] or 0) + (r["prior_assists"] or 0)
    pp_gp = r["prior_gp"] or 0
    two_season = (pts + pp_pts) / (r["final_gp"] + pp_gp)
    data.append(
        {
            "player": r["player"],
            "cls": r["draft_year"],
            "ppg_ncaa": pts / r["final_gp"],
            "share": pts / max(gf[k], 1),  # share of her team's goals she was on
            "team_gf": team_gf_pg,
            "two_season": two_season,
            "is_d": is_d(r["position"]),
            "age": (r["pwhl_start"] - r["birthdate"]).days / 365.25,
            "y": (r["pwhl_goals"] + r["pwhl_assists"]) / r["pwhl_gp"],
            "pwhl_gp": r["pwhl_gp"],
            "toi_pg": r["pwhl_toi_seconds"] / 60 / r["pwhl_gp"],
            "pick": r["draft_pick"],
        }
    )
n = len(data)
col = lambda k: np.array([d[k] for d in data], dtype=float)  # noqa: E731
y, ppg, share, team_gf, two, isd, age, cls = (
    col("y"),
    col("ppg_ncaa"),
    col("share"),
    col("team_gf"),
    col("two_season"),
    col("is_d"),
    col("age"),
    col("cls"),
)
age_c = age - age.mean()
league_gf = np.mean(list(gf[k] / gp_team[k] for k in gp_team))
print(
    f"skaters used: {n} (class 2024: {int((cls == 2024).sum())}, class 2025: {int((cls == 2025).sum())})"
)
print(
    f"mean PWHL ppg {y.mean():.3f} (sd {y.std(ddof=1):.3f}); mean NCAA ppg {ppg.mean():.3f}; defenders {int(isd.sum())}"
)
print(
    f"mean age at PWHL season start {age.mean():.1f}; NCAA school goals/game {team_gf.mean():.2f}\n"
)

ones = np.ones(n)
SPECS = {  # pre-specified; name -> design matrix
    "M0 mean only": np.column_stack([ones]),
    "M1 ratio (y = k * ncaa_ppg)": np.column_stack([ppg]),
    "M2 ncaa_ppg": np.column_stack([ones, ppg]),
    "M3 + position + age": np.column_stack([ones, ppg, isd, age_c]),
    "M4 points share of team goals + pos + age": np.column_stack([ones, share, isd, age_c]),
    "M5 two-season ppg + pos + age": np.column_stack([ones, two, isd, age_c]),
}


def ols(X, yy):
    beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
    return beta


def loo(X, yy):
    H = X @ np.linalg.pinv(X.T @ X) @ X.T
    e = yy - H @ yy
    return e / (1 - np.diag(H))


def ranks(v):
    order = np.argsort(v)
    r = np.empty(len(v))
    r[order] = np.arange(len(v))
    return r


def spearman(a, b):
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def class_split(X):
    out = []
    for tr, te in ((2024, 2025), (2025, 2024)):
        m_tr, m_te = cls == tr, cls == te
        if m_tr.sum() < X.shape[1] + 2:
            continue
        b = ols(X[m_tr], y[m_tr])
        pred = X[m_te] @ b
        out.append(float(np.sqrt(np.mean((y[m_te] - pred) ** 2))))
        base = float(np.sqrt(np.mean((y[m_te] - y[m_tr].mean()) ** 2)))
        out.append(base)
    return out


print(
    f"{'model':46} {'k':>2} {'in-RMSE':>8} {'LOO-RMSE':>9} {'LOO-R2':>7} {'LOO-rho':>8}  24->25 / 25->24 (RMSE vs mean-only)"
)
sst = float(np.sum((y - y.mean()) ** 2))
results = {}
for name, X in SPECS.items():
    b = ols(X, y)
    fit = X @ b
    e = loo(X, y)
    loo_pred = y - e
    rmse_in = float(np.sqrt(np.mean((y - fit) ** 2)))
    rmse_loo = float(np.sqrt(np.mean(e**2)))
    r2 = 1 - float(np.sum(e**2)) / sst
    rho = spearman(loo_pred, y) if X.shape[1] > 1 else float("nan")
    cs = class_split(X)
    cs_txt = " / ".join(f"{cs[i]:.3f} vs {cs[i + 1]:.3f}" for i in range(0, len(cs), 2))
    print(
        f"{name:46} {X.shape[1]:>2} {rmse_in:8.3f} {rmse_loo:9.3f} {r2:7.2f} {rho:8.2f}  {cs_txt}"
    )
    results[name] = (X, b, e)

best = min(
    (k for k in results if not k.startswith("M0")),
    key=lambda k: np.sqrt(np.mean(results[k][2] ** 2)),
)
print(
    f"\nlowest LOO-RMSE among non-baseline models: {best}  (picked from 5, so its LOO score is optimistic)"
)

X, b, e = results[best]
names = {
    "M1": ["ncaa_ppg"],
    "M2": ["intercept", "ncaa_ppg"],
    "M3": ["intercept", "ncaa_ppg", "is_defender", "age (yrs, centred)"],
    "M4": ["intercept", "points_share", "is_defender", "age (yrs, centred)"],
    "M5": ["intercept", "two_season_ppg", "is_defender", "age (yrs, centred)"],
}[best[:2]]
boots = []
for _ in range(2000):
    idx = rng.integers(0, n, n)
    try:
        boots.append(ols(X[idx], y[idx]))
    except np.linalg.LinAlgError:
        pass
boots = np.array(boots)
print(f"\ncoefficients for {best} (bootstrap 2000, 90% interval):")
for i, nm in enumerate(names):
    lo, hi = np.percentile(boots[:, i], [5, 95])
    print(f"  {nm:22} {b[i]:8.3f}   [{lo:7.3f}, {hi:7.3f}]")
loo_pred = y - e
q10, q90 = np.percentile(e, [10, 90])
print(f"\nLOO residuals: sd {e.std(ddof=1):.3f}; 10th/90th percentile {q10:.3f} / {q90:.3f}")
lo_half = loo_pred <= np.median(loo_pred)
print(
    f"  residual sd where predicted ppg is low {e[lo_half].std(ddof=1):.3f} vs high {e[~lo_half].std(ddof=1):.3f}"
)
print("  mean PWHL games played in outcome season:", round(float(col("pwhl_gp").mean()), 1))

# who is most over/under predicted (largest absolute LOO errors)
order = np.argsort(-np.abs(e))[:6]
print("\nlargest LOO misses (actual vs predicted PWHL ppg):")
for i in order:
    print(
        f"  {data[i]['player']:24} class {data[i]['cls']}  ncaa ppg {data[i]['ppg_ncaa']:.2f}  actual {y[i]:.2f}  pred {loo_pred[i]:.2f}"
    )


# ---- EXPLORATORY (not pre-specified): per-60 rates and draft position. Treat as hypotheses. ----
print("\n=== EXPLORATORY: does ice time / draft position explain the misses? ===")
ok = np.array([d["pick"] is not None and d["toi_pg"] > 0 for d in data])
toi = col("toi_pg")
pick = np.array([d["pick"] if d["pick"] is not None else 0 for d in data], dtype=float)
TEAMS = {2024: 6, 2025: 8, 2026: 12}  # teams in each draft: pick / teams is "round-equivalent"
teams_n = np.array([TEAMS[int(c)] for c in cls], dtype=float)
lnp = np.log(np.where(pick > 0, pick / teams_n, 1))
y60 = y / np.where(toi > 0, toi, 1) * 60
print(
    f"rows with a draft pick and ice time: {int(ok.sum())} of {n}; mean PWHL TOI/GP {toi[ok].mean():.1f} min"
)
print(
    f"rank corr: NCAA ppg vs PWHL pts/60 {spearman(ppg[ok], y60[ok]):.2f}; vs PWHL TOI/GP {spearman(ppg[ok], toi[ok]):.2f}; "
    f"draft pick vs TOI/GP {spearman(-pick[ok], toi[ok]):.2f}; draft pick vs PWHL ppg {spearman(-pick[ok], y[ok]):.2f}"
)
m = ok


def ex(label, X, yy):
    e = loo(X, yy)
    r2 = 1 - float(np.sum(e**2)) / float(np.sum((yy - yy.mean()) ** 2))
    print(f"  {label:48} LOO-RMSE {np.sqrt(np.mean(e**2)):.3f}  LOO-R2 {r2:5.2f}")


o = np.ones(int(m.sum()))
ex("PWHL ppg ~ ncaa_ppg (M2 on this subset)", np.column_stack([o, ppg[m]]), y[m])
ex("PWHL ppg ~ ncaa_ppg + ln(pick / teams)", np.column_stack([o, ppg[m], lnp[m]]), y[m])
ex("PWHL ppg ~ ln(pick / teams)", np.column_stack([o, lnp[m]]), y[m])
ex("PWHL pts/60 ~ ncaa_ppg", np.column_stack([o, ppg[m]]), y60[m])
ex("PWHL TOI/GP ~ ln(pick / teams)", np.column_stack([o, lnp[m]]), toi[m])
bb = ols(np.column_stack([o, ppg[m], lnp[m]]), y[m])
print(
    f"  ppg ~ ncaa_ppg + ln(pick) coefficients: intercept {bb[0]:.3f}, ncaa_ppg {bb[1]:.3f}, ln(pick) {bb[2]:.3f}"
)


# ---- Apply to the 2026 class (draft picks known, 2025-26 NCAA lines pulled) ----
root = Path(__file__).parent
applied = list(
    csv.DictReader(open(root.parent / "ncaa_pilot/data/applied_2026.csv", encoding="utf-8"))
)
mA = ok  # same subset for both fits
XA, XB = np.column_stack([ppg[m]]), np.column_stack([np.ones(int(m.sum())), ppg[m], lnp[m]])
bA, bB = ols(XA, y[m]), ols(XB, y[m])
eA, eB = loo(XA, y[m]), loo(XB, y[m])
rows_out = []
for a in applied:
    if a["pos"] == "G":
        continue
    gp_n = int(a["ncaa_gp"])
    if gp_n < MIN_GP:
        continue
    x = (int(a["ncaa_goals"]) + int(a["ncaa_assists"])) / gp_n
    pk = np.log(int(a["pick"]) / TEAMS[2026])
    pA = float(bA[0] * x)
    pB = float(bB[0] + bB[1] * x + bB[2] * pk)
    loA, hiA = np.percentile(eA, [10, 90])
    loB, hiB = np.percentile(eB, [10, 90])
    rows_out.append(
        {
            "player": a["player"],
            "school": a["school"],
            "pos": a["pos"],
            "pick": a["pick"],
            "ncaa_ppg": round(x, 2),
            "A_ratio_pred": round(max(pA, 0), 2),
            "A_lo80": round(max(pA + loA, 0), 2),
            "A_hi80": round(pA + hiA, 2),
            "B_pick_pred": round(max(pB, 0), 2),
            "B_lo80": round(max(pB + loB, 0), 2),
            "B_hi80": round(pB + hiB, 2),
        }
    )
out = root / "output"
out.mkdir(exist_ok=True)
with open(out / "translated_priors_2026.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows_out[0]))
    w.writeheader()
    w.writerows(rows_out)
print(f"\n2026 class skaters translated: {len(rows_out)} -> output/translated_priors_2026.csv")
print(
    "  A = pre-specified ratio model; B = exploratory ncaa_ppg + ln(pick/teams) (hypothesis, not validated)"
)
print("  top 8 by model B:")
for r in sorted(rows_out, key=lambda r: -r["B_pick_pred"])[:8]:
    print(
        f"   {r['player']:22} {r['school']:18} pick {r['pick']:>2}  ncaa {r['ncaa_ppg']:.2f}  A {r['A_ratio_pred']:.2f} [{r['A_lo80']:.2f}-{r['A_hi80']:.2f}]  B {r['B_pick_pred']:.2f} [{r['B_lo80']:.2f}-{r['B_hi80']:.2f}]"
    )
