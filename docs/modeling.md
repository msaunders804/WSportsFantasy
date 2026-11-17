# Modelling: weekly game and season predictions

Status: DRAFT for Megan's review, 2026-11-16. Nothing is built. Storage, access rules and publication are in [predictions-design.md](predictions-design.md); this document is the method. Facts tagged **(verified)** were checked against our own data. Facts tagged **(unverified)** came from web search snippets or my own knowledge and must be confirmed before we rely on them.

## 1. What we are predicting
1. **Per game:** probabilities of regulation win, overtime/shootout win and loss for each side, plus expected goals, with an uncertainty interval.
2. **Per season:** expected points and playoff odds per team, by simulating the 180-game 2026-27 schedule.
3. **Per player:** a rating with an uncertainty and an evidence label, published as the derived rating only.

Everything is shown as probabilities and fair odds, never as betting content.

## 2. Principles
- **Founded on data, honest about uncertainty.** A season is 30 games per team. Intervals matter more than point predictions.
- **Players first, teams second.** Four expansion teams and 130 camp players with no PWHL history (**verified**, 37% of the 347-player camp pool) mean team history cannot carry the model. Rosters can.
- **Calibration over accuracy.** Hockey is high-variance. Success is "when we say 60%, it happens about 60% of the time".
- **No leakage.** Every backtest uses only information available at the prediction time.
- **Start simple, add complexity only when the backtest rewards it.** Each added source or model layer must show a measured gain.

## 3. Data inventory

### In hand (**verified**, on Railway and local)
| Data | Volume | Use |
|---|---|---|
| Box scores, 2024 to 2025-26 | 282 games, 10,669 skater and 1,128 goalie lines | Production rates, ice time, goalie save rates |
| Shots with x/y, shot type, goalie, blocker | 21,662 rows | Expected-goals (xG) model, goalie quality |
| Penalties with clock | about 1,800 | Rebuild power-play and penalty-kill time |
| 2026-27 schedule | 180 games | Rest, travel, simulation |
| Player basics | birthdate, position, rookie/draft flags | Age curve, priors |

Limits of the feed (**verified**, see [feed.md](feed.md)): no missed-net shots; no players-on-ice data, so on-ice impact (Corsi, on-ice goals for and against) cannot be computed; strength state is given only for goals, so we rebuild it from penalties; no line or ice-time deployment beyond total time on ice.

### To collect
| Source | What | Status | Notes |
|---|---|---|---|
| Final 2026-27 rosters | Who is actually on each team | Not published as of Nov 16 (**verified**: season 11 roster feed empty) | Camp pool only; projected lineup until cuts |
| NCAA Division I women's hockey | Points, shots, goalie save % by player | [henrygd/ncaa-api](https://github.com/henrygd/ncaa-api) (MIT-licensed wrapper over ncaa.com) returned women's hockey (`icehockey-women`, `d1`) scoreboard and **player-level box scores** on a Nov 15, 2025 test (**verified**, 2 games' worth of requests): per player name, position, number, shots, goals, assists, PP/SH goals, GWG, blocks, +/-, faceoffs; goalies have minutes, goals allowed, saves. No player ID, class year or birthdate, and skater ice time is zero. Season-stat endpoints and the schedule route are **not working or not tested** (the schedule route returned a validation error). The README states no NCAA terms. | Largest source: 48 of 130 no-history players list the US, most of them NCAA |
| U SPORTS, Canadian junior/other | Same | Not investigated | 59 of the 130 list Canada |
| Olympics (Feb 2026) | Scoring, shots, goalie stats per player | Elite Prospects has a 2025-26 Olympic table **(unverified, fragmentary)**; IIHF event pages exist for the tournament **(unverified)** | Small sample, about 5-7 games per player |
| IIHF Women's World Championship | Same | A 2026 championship appears under way or just finished; IIHF pages returned fragments only **(unverified)**. Confirm dates and which edition is relevant before use. | Annual; gives repeat samples for national-team regulars |
| European leagues (SDHL, Finland, Czech, Swiss, others) | Same | Not investigated | About 14 of the 130 list Finland, Germany, Sweden, Russia or Norway |
| Elite Prospects | Cross-league player IDs, birthdates, career stats | Not investigated; licensing unknown | Likely the ID backbone |
| Availability | Injuries, IR, transactions | The PWHL site has a Transactions view **(unverified)**; nothing stored | Manual first |

**Publication rule (decided):** raw third-party data stays local. Only derived ratings are published.

### NCAA pilot (Nov 16, 2026; scripts in `analysis/ncaa_pilot/`, data local and gitignored)
- **Who we know played NCAA.** HockeyTech has no school for these players (**verified**: empty bio, drafts and commitment fields on the 8 profiles probed; no draft info on any of the 130 roster rows). The only school list available is the 2026 PWHL draft (72 picks; schools from The Hockey News tracker, cross-checked against College Hockey Inc., which disagree on a few names). **68 of the 72 picks matched a camp-roster player**, all with no PWHL history; **56 are NCAA players from 19 schools**. The other **62 no-history players are not on the draft list**, so their schools are unknown (28 camp invitees, 26 with no status, 8 others, mostly Canadian or US). Closing that gap needs another source (free-agent signing announcements or Elite Prospects).
- **What the limit saved.** Only schools of those 56 players were pulled for 2025-26: 188 daily scoreboards (no player data) and **492 box scores of the 809 games** in the division that season, about 39% fewer box-score requests than pulling everything. At one request per second, a bit over 11 minutes.
- **Result.** **55 of 56 players found** in the box scores (the miss is a nickname: Tory vs Victoria Mariano). Games played range 18 to 41, median 35, so season lines are usable. Goalies: Tia Chan 37 GP, Katie DeSa 31, Hailey MacLeod 30, Emma-Sofie Nordström 18. Per-player lines are in `data/ncaa_player_lines_2025_26.csv`.
- **Training cohort built (Nov 16).** 2024 draft (42 picks, 30 NCAA) and 2025 draft (48 picks, 45 NCAA): **69 players played NCAA and then the PWHL** (62 skaters, 7 goalies; median 27 PWHL games in their first season, 61 with at least 10). For each we have NCAA lines for the last two seasons before the draft. Pulled seasons 2022-23, 2023-24 and 2024-25 for the schools involved, plus the previous schools of players who transferred. **Final-season lines found for 65 of 69; prior-season for 64 of 69** (7 of those at a different school: transfers). Missing: Claire Thompson, Gabby Rosenthal (final season), Michelle Karvinen (North Dakota has no recent D-I team), Sara Hjalmarsson, and a few prior seasons. About 8% of 2022-23 box scores kept failing with server errors.
- **First sanity check:** for 53 skaters with at least 10 games in both NCAA and PWHL, mean points per game is **0.91 NCAA vs 0.29 PWHL (about a third)**, and the rank correlation between them is **0.44**. That is a usable but modest signal, and consistent with the older PHF figure, so expect wide uncertainty on any translated rating. Not yet adjusted for age, position or team strength.
- **Data quirk:** some box scores abbreviate a first name to an initial or add a leading space, so one player can appear under several name strings. The loader keeps source names as given and the training table merges the variants; do not group by name alone.
- **Stored in the Railway `research` schema** (private, never read by the web app): see [decisions.md](decisions.md).
- **Not yet done:** the 62 unknown no-history players (schools not on any draft list); the 2023 draft class and undrafted free agents as extra training data; fitting the translation itself (needs age, position and team-strength adjustments). Stats are per game only, since skater ice time is not in the data. We have not checked NCAA terms of use for the underlying data.

## 3b. NCAA-to-PWHL translation fit (Nov 16, 2026; `analysis/translation_fit/fit.py`)
**Setup.** 53 skaters from the 2024 and 2025 drafts with at least 10 games in both their final NCAA season and their first PWHL season (21 from 2024, 32 from 2025; 17 defenders). Outcome: PWHL points per game. Six models were fixed before looking at results and compared by leave-one-out (LOO) error and by training on one draft class and predicting the other. The 7 goalies are **not modelled** (too few); use the position-average prior until more data exists.

**Pre-specified result.** The simple ratio wins: **PWHL points per game is about 0.32 times NCAA points per game** (0.315, 90% bootstrap interval 0.26 to 0.37). Mean NCAA 0.91 vs PWHL 0.29 per game. Adding position, age, or points share of team goals made out-of-sample error **worse**, not better, so none of those adjustments is used.

| Model | LOO RMSE | LOO R2 | Class 2024 to 2025 / 2025 to 2024 (vs mean-only) |
|---|---|---|---|
| Mean only | 0.234 | -0.04 | 0.230 / 0.243 |
| Ratio (k times NCAA ppg) | **0.205** | **0.20** | **0.191 vs 0.230 / 0.217 vs 0.243** |
| + position + age | 0.216 | 0.11 | 0.217 / 0.231 |
| Points share + position + age | 0.241 | -0.11 | 0.256 / 0.245 |

**How good is it? Modest.** NCAA scoring explains about 20% of out-of-sample variance. LOO residual sd is 0.21 points per game against an outcome sd of 0.23, and errors grow with the level (sd 0.17 for low predictions, 0.24 for high). The ratio beat the mean-only baseline in both directions of the class split. The model was picked from five alternatives, so its LOO score is slightly optimistic. Biggest misses: Sarah Fillier (actual 0.97, predicted 0.44), Izzy Daniel (0.23 vs 0.58), Jenna Buglioni (0.00 vs 0.36), Vanessa Upson (0.04 vs 0.36).

**Exploratory finding (not pre-specified; a hypothesis, not a validated result).** **Draft position predicts PWHL scoring about as well as NCAA stats do and adds to them.** Using ln(pick divided by teams in the draft) (normalised, since the 2024, 2025 and 2026 drafts had 6, 8 and 12 teams): LOO R2 0.35 with draft position alone, 0.36 with NCAA ppg added, against 0.17 for NCAA ppg alone on the same players. Draft position tracks PWHL ice time most closely (rank correlation 0.59 with minutes per game), so it is mainly a **role** signal: teams give early picks more ice time, and more ice time means more points. Per-60 rates were no easier to predict from NCAA scoring (LOO R2 0.11). This was found after seeing the misses, so it carries extra risk of overfitting; it needs checking on the 2023 class or on the 2026 season as it plays out. It also carries a causal caveat: the pick itself partly reflects NCAA production.

**Applied to the 2026 class.** 50 NCAA skaters from the draft have a translated prior with an 80% interval (empirical LOO residual quantiles) in `analysis/translation_fit/output/translated_priors_2026.csv` (local). Model A is the pre-specified ratio; model B adds draft position. Examples (A / B, points per game): Abbey Murphy 0.67 / 0.76, Tessa Janecke 0.49 / 0.63, Laila Edwards 0.51 / 0.61, Lacey Eden 0.59 / 0.61. All 2026 players are inside the training range of NCAA scoring (training maximum 2.15; 2026 maximum 2.13). Intervals are wide, for example 0.43 to 0.95 for Murphy under model A.

**Limitations and cautions.**
- **Small sample:** 53 players; bootstrap and LOO intervals reflect that, but the true uncertainty is larger because we also chose models.
- **Survivors only:** training includes only drafted players who made the PWHL, so the translation tends to be too generous to outside players. Only about 5 to 6 NCAA draftees per class never played, so the bias is probably small but unmeasured.
- **Role confounding:** points per game mix talent with ice time. The 2026 expansion teams are thin, so early picks there may get more ice time than the 2024 and 2025 picks did.
- **NCAA skater ice time is unavailable,** so we cannot translate per 60.
- **Season length:** 26 PWHL games on average, so each outcome is itself noisy.
- **Coverage:** the 12 non-NCAA draftees in 2024 and 3 in 2025 (Europe, U SPORTS) are not in this fit, and neither are 62 camp players with no draft record.

**Decision needed from Megan:** whether the production prior for rookies should be model A only (validated, weak), or A plus draft position (stronger in-sample, but exploratory). My recommendation: use model B but label it provisional, and **test it on the backtest harness before relying on it** (milestone M0).

## 4. Model structure
Five layers, built in order. Each layer is testable on its own.

### Layer 0: shot quality (xG)
Logistic model on `shot_event`: distance and angle from the normalised coordinates (the home team attacks toward low x, so mirror away shots; **verified**), shot type, blocked or not, power play or short-handed where known, rebound and rush proxies from the previous event's clock. Trained on 2024 and 2024-25, tested on 2025-26. Output is judged on log loss and calibration by distance band. Because misses are absent, xG is the probability a *recorded* shot scores, not a full shot-attempt model; we state that wherever it is published.

### Layer 1: player skill from PWHL history
Hierarchical Bayesian (partial pooling) by position group, so a player with little ice time is pulled toward the position average and a player with a lot keeps their own number.
- **Skaters:** individual xG per 60, shots per 60, assist rate per 60, split by even strength and power play where the data allows, penalties taken. Defensive impact is only weakly observable (blocks, hits, plus-minus with ice time); we say so and shrink defenders' defensive ratings hard.
- **Goalies:** goals saved above expected from `shot_event` (actual saves vs xG faced), shrunk heavily because goalie results are noisy. Prior for new goalies from outside leagues (layer 2).
- **Time:** recent seasons weighted more than old ones; an age curve from birthdates, with its shape set from external literature or assumptions and flagged as such, since three seasons cannot estimate it.
- **Output:** a rating mean and standard deviation per component, plus an evidence label (`pwhl_3plus`, `pwhl_1_2`, `external_only`, `none`).

### Layer 2: priors for players with no PWHL history
Translate outside production into a PWHL-scale prior.
- **Fit our own translation.** Published factors are thin. The only direct number I found is an older drop of about 0.37 points per game for NCAA players moving to the PHF, from a very small sample **(unverified)**, and a 2020 analysis said there was not enough data to build reliable factors **(unverified)**. We have something better: every PWHL rookie from three years. Match them to their pre-PWHL NCAA and other stats, and regress PWHL production on prior-league production, age, position and league, with league as a random effect. This also gives an honest uncertainty for the translation.
- **Olympics and Worlds as weak evidence.** Samples are about 5-7 games against uneven opposition, so raw tournament points are not used directly. They help in two ways: (a) a low-weight signal for players who otherwise have little data, such as European players with no league match; (b) a cross-league anchor, because players appearing in both a national tournament and the PWHL or NCAA calibrate the scales against each other. Their weight in the model is set by the backtest, not by hand.
- **No outside data:** a position-average rookie prior with wide uncertainty. This is the v0 behaviour and the benchmark each external source has to beat.

### Layer 3: team strength from lineups
Team attack and defence = sum of player contributions weighted by projected ice time, plus a small team effect (system, coaching, chemistry) with strong shrinkage toward zero. Expansion teams start with that effect at zero and a wider interval. Player uncertainty adds up, so a team with many unknowns gets wider game and season intervals automatically.

Lineups: before final rosters, a projected roster (signed and returning players first, camp invitees and draftees by their chance of making the team). After cuts, the actual roster. Goalie starts are modelled as a probability split from each goalie's past share, since starters are not announced.

### Layer 4: the game and the season
- **Game:** goals for each side as Poisson (or negative binomial if the data are over-dispersed), from the two teams' attack and defence, plus home ice, rest days, back-to-backs and travel from the schedule. A tie after regulation goes to overtime and shootout, modelled as close to a coin flip tilted by strength.
- **Points and standings:** simulate all 180 games many times. The points rule is assumed to be 3 for a regulation win, 2 for an overtime or shootout win and 1 for an overtime or shootout loss, and **must be checked against 2025-26 standings** before use. Playoffs: 8 teams from two conferences, from the October 2026 league announcement; the exact bracket rules are to be confirmed.
- **Outputs:** expected points, probability of each finishing position and of making the playoffs, with the simulation seed and version stored.

### Layer 5: availability
First version ignores injuries and says so on every page. Then a manual log of status (active, day-to-day, out, injured reserve), with each run recording what it used. Impact is estimated by removing the player's contribution from the lineup.

## 5. Evaluation
**Backtest design.** Time-ordered. Predict each game using only data from before it, with weekly updating, exactly as the live system will run.
- **2025-26** is the main test: Seattle and Vancouver were new, which is the cold-start problem. Prior data: 2024 and 2024-25 box scores and shots, plus opening-day rosters.
- **2024-25** is a weaker second test, with only 72 games of history and six teams.
- Opening-day rosters are approximated from who played in the first games of the season. That is a small leak (it knows who stayed); we note it and also test with the preseason roster.

**Baselines the model must beat:**
1. Home-ice-only (constant win probability).
2. Last season's points percentage regressed toward average.
3. An Elo-style team rating with carry-over between seasons.

**Metrics:** log loss and Brier score for game win probability; calibration by probability bucket; for the season, rank correlation and the error of expected points, and whether actual results fall inside the stated intervals. For the cold-start question, report the same numbers for Seattle and Vancouver separately.

**Power warning.** Two test seasons and about 210 games is small. A log-loss improvement of a few thousandths would be indistinguishable from noise, so we report bootstrap intervals and avoid claiming small wins. Complexity is added only for a clear gain.

## 6. Where the results go
Outputs map onto the `analytics` tables in the design doc. One addition is needed: a `season_projection` table (`model_run_id`, `pro_team_id`, expected points, finishing-position and playoff probabilities, simulation count and seed). Predictions stay append-only and versioned. The track-record page shows backtests separately from live predictions.

## 7. Risks and limits
- **Thin samples:** 30 games per team per season; goalie results especially noisy.
- **No on-ice data:** defensive skill is mostly a guess; the model's defence ratings will be wide and shrunk.
- **No missed shots:** xG is conditional on a shot being recorded.
- **Roster uncertainty until cuts:** early predictions are labelled preliminary.
- **Expansion effects** that no data can show: team identity, chemistry, building a first-year culture. Handled only by wider intervals.
- **Cross-league translation** is estimated from small, self-selected groups (players who made the PWHL), so it may overstate how good outside players are. We check by also training on drafted players who did not make teams, where we have them.
- **Source terms and reliability:** the HockeyTech feed is unofficial (see [feed.md](feed.md)); NCAA, Elite Prospects and IIHF terms are unchecked; players need matching across sources, with a manual review queue for uncertain matches.

## 8. Milestones
| # | Milestone | Done when |
|---|---|---|
| M0 | Points rule and format check, backtest harness, three baselines | Baselines scored on 2025-26 |
| M1 | xG model | Calibrated on held-out 2025-26; log loss beats a distance-only model |
| M2 | Player ratings from PWHL history, position-average priors for new players | Team ratings from lineups beat the Elo baseline or we learn why not |
| M3 | Game model and season simulation | Calibration report on 2025-26 including Seattle and Vancouver |
| M4 | First preliminary 2026-27 run on a projected roster | Reviewed by Megan before anything is published |
| M5 | External priors: NCAA first, then Europe, then Olympics and Worlds as weak evidence | Each source's backtest gain is measured and reported |
| M6 | Final-roster run, availability log, publish via the `analytics` schema | Public pages live with the track record |

## 9. Open questions for Megan
1. Which external source first? I suggest NCAA, since it covers the largest group of no-history players.
2. Is a preliminary 2026-27 prediction, clearly labelled, acceptable to publish before final rosters? I suggest yes, with intervals and a visible "preliminary" tag.
3. Who enters injury and availability data at first? I suggest you, from public sources, with a simple form or spreadsheet.
4. Any preference on tooling? I suggest empirical-Bayes in pandas and numpy for v0, moving to a full Bayesian library only if the backtest justifies it.

## Sources consulted (search results; not read in depth)
- [IIHF Olympic women's scoring leaders](https://eu-west.iihf.com/en/events/2026/olympic-w/skaters/scoringleaders)
- [Elite Prospects Olympic women's stats](https://www.eliteprospects.com/league/og-w/stats)
- [Hockey Canada women's Rivalry Series player stats](https://video.hockeycanada.ca/en-ca/team-canada/women/national/2025-26/rivalry-series/stats/Player-Stats)
- [Yale women's hockey cumulative statistics (example of NCAA school stats pages)](https://yalebulldogs.com/sports/womens-ice-hockey/stats)
- [The Hockey News on NCAA and U SPORTS rookies' scoring in the PHF](https://thehockeynews.com/womens/college/predicting-scoring-of-ncaa-and-usports-rookies-in-the-phf)
- [Hockey-Graphs: NWHLe and translation factors](https://hockey-graphs.com/2020/05/14/introducing-nwhle-and-translation-factors/)
