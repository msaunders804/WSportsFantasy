# Decisions

## 2026-10-12: Hosting is Railway
Decided by Megan. Web, worker and Postgres as separate services in one Railway project, built from a Dockerfile with env-var config. Drops the Compose and Caddy parts of Plan.MD section 6; Railway provides TLS. Use a Postgres database separate from the PWHL analysis one, and a direct (non-pooled) connection so LISTEN/NOTIFY works. App name: Undertow Sports.

## 2026-10-12: Playoff timing (DECIDED by Megan: three 2-week rounds)
Plan.MD sections 3, 4.4, 4.5 and 4.7 updated to match. Commissioners should be told before the drafts.
Problem: with one-week rounds, Week 16 (Mar 29 to Apr 4) has 6 games league-wide and Las Vegas plays 0. Games per PWHL team in any one week range 0 to 3, so single-week rounds are noisy by nature.

Searched every layout of 3 playoff rounds of 1 to 3 weeks each, ending Apr 18 or Apr 11, using the 2026-27 schedule fixture. Best by far: **three 2-week rounds**.

| Round | Fantasy weeks | Dates | Games per PWHL team |
|---|---|---|---|
| Quarterfinal (seeds 3-6) | 13-14 | Mar 8 to Mar 21 | 2 to 4 |
| Semifinal | 15-16 | Mar 22 to Apr 4 | 2 to 4 |
| Final and 3rd place | 17-18 | Apr 5 to Apr 18 | 2 to 4 |

- Every team plays at least 2 games in every round. Current plan (1-week rounds) has a team with 0 in round 1.
- Spread is at most 2 games per team per round, the lowest of any layout. 3-week rounds are worse (spread 3-4).
- The season ends on the real last day, so end-of-season rest risk stays as in Plan 4.4.
- Cost: regular season becomes 12 weeks (Weeks 1-12) instead of 15. With 10 teams each plays 9 opponents once plus 3 repeats. Trade deadline moves from end of Week 13 to end of Week 11. Longer rounds also reduce variance, which suits about 1.6 games per week.
- Alternative if you want a longer regular season: 14 weeks with rounds of 2, 1, 1 weeks (Mar 22 start). Same worst-round spread but Week 17-18 rounds have a team with only 1 game.

The schedule can still change (postponements), so playoff round boundaries must be config, and a check script should re-run this analysis whenever the schedule sync detects changes.

## 2026-10-12: Phase 1 tooling choices
- Package management with `uv` (lockfile committed, Python pinned to 3.12 via `.python-version` to match the Docker image).
- Single Docker image for both services. `web` is the default command; `worker` overrides the start command to `python -m undertow.worker.main`. Migrations run as Railway's `preDeployCommand` on `web` only, so they cannot race.
- Worker takes a Postgres session-level advisory lock before starting APScheduler, so an accidental second worker instance idles instead of duplicating jobs.
- Local dev Postgres is published on host port 5433 because 5432 is already used on the shared dev box.
- Baseline Alembic revision is empty on purpose; tables arrive with Phase 2 once the schema is checked against the fixtures.
- Tailwind and Alpine are deferred until the first real page (Phase 3). Pages are unstyled for now.

## 2026-10-12: Phase 2 ingestion and scoring choices
- Primary keys of real-hockey tables are the HockeyTech IDs. Players found only in a box score get a bare row (insert-if-missing); roster sync is authoritative and overwrites.
- Game start time comes from `GameDateISO8601` (has a real offset). The feed's `date_time_played` is local time labelled `Z` and is ignored.
- Raw snapshots store the decoded JSON (JSONP stripped) with a SHA-256 of the canonical form; a response identical to the latest for the same URL is not stored again. Snapshot URLs never contain the API key.
- Shutout is derived: the team's only goalie to play, with 0 goals against. Goalie decision is one per (game, goalie) from `goalieLog` (W > OTL > SOL > L when stints differ). REG/OT/SO comes from the game's final status text.
- PP/SH points and GWG are derived from the goal list, which already excludes shootout goals.
- Conference (East/West) is a small data file keyed by feed team code (`data/conferences.json`), since the feed has no conference. Note LV is `VGS` in the feed.
- Scoring uses exact `Fraction` arithmetic so the 2/3 OT-win multiplier stays exact. `ScoringConfig.from_dict` treats a missing key as 0 (not scored), so league settings must always store the full `to_dict()`.
- Not yet built: `player_team_history` (needs mid-season move data), roster injury/LTIR status (not in the feed), and worker job registration for the syncs.

## 2026-10-13: Worker jobs and season replay
- Jobs (all in `worker`, each recorded in `job_run`, failures never stop the scheduler): `daily_sync` 04:30 ET (seasons, teams, schedule, rosters), `nightly_resync` 05:00 ET (schedule, then re-fetch the last 7 days of games), `poll_live` every 90 s, `reconcile_finals` every 30 min. Ingestion jobs are only registered when `HOCKEYTECH_KEY` is set. Seasons to sync come from `INGEST_SEASON_IDS` (comma-separated, default `11`).
- The live poll makes no requests unless a game is inside its window (10 min before start to 5 h after). It also picks up games the schedule already calls final but whose box score we never captured (`final_at` is null), so a schedule sync that flips a game to final cannot cause a missed box score.
- `game.final_at` is stamped the first time we see a box score final; `reconcile_count` tracks the +6 h and +24 h follow-up fetches.
- Season replay (`python -m undertow.ingest.replay`) drives these same jobs on a simulated clock, caching every response under `data/replay_cache/` (gitignored). The first run hits the live feed (throttled, about 130 requests); later runs are offline. Integrity checks: one W and one matching loss decision per game (L, OTL or SOL by result type), skater goals per team equal the team score minus the shootout-winning goal, and the opposing goalies' goals against match too.
- Not built: scoring recompute (needs leagues, Phase 7) and email alerts when polling fails during a game window (needs the email provider). `job_run` has the data for both.
- Replay findings that changed the code: box scores say `Final OT1` (schedule: `Final OT`), so result type is matched loosely; empty-net goals are stored per skater (`en_goals`) because they are not charged to the goalie; an empty goalie result means no decision. Details in `docs/feed.md`.
- Logging: the `httpx` logger is set to WARNING in both entry points because it logs full request URLs (including the API key) at INFO.

## 2026-10-14: Railway service settings are set in the dashboard
- First deploy: Railway did not apply `railway.toml` to the services (the web service showed no pre-deploy command or healthcheck), so migrations never ran and the worker failed with `relation "game" does not exist`. Fix: set the values in each service's dashboard settings, which take precedence over the file.
- `web`: Pre-deploy Command `alembic upgrade head`, Healthcheck Path `/healthz`. Start command is left blank so the Dockerfile `CMD` (uvicorn) is used.
- `worker`: Start Command `python -m undertow.worker.main`. Pre-deploy Command and Healthcheck Path stay empty (no HTTP server; migrations run from `web` only so they cannot race).
- `railway.toml` is kept as documentation of the intended web settings. If a new service or environment is created, re-check these fields by hand, and confirm in the deploy log that the pre-deploy step ran.
- Variables: `HOCKEYTECH_KEY` and `HOCKEYTECH_USER_AGENT` are set on `worker` only (nothing in `web` touches the feed). `DATABASE_URL` uses the `postgresql+psycopg://` scheme on the private (internal) host on both services.
- Branching: one branch per phase, each PR opened directly against `main` (stacked PRs merged into each other's branches left Phase 2 off `main` until a follow-up PR).

## 2026-10-14: Landing page and waitlist
- Brand foundation is in [brand.md](brand.md); design tokens live in `web/static/tokens.css` and every page styles from them. Dark is the default theme, with a light toggle remembered in `localStorage`.
- The landing page is for Undertow Fantasy only (the wider Undertow Sports site is a separate project). Wordmark only for now (no logo). It carries an "unofficial and unaffiliated" marker in the footer of every page.
- League marks: the brand owner decided official PWHL logos and photos may be used, with no monetisation. The landing page uses none of them (team names only as text), and the social posts are organic (no paid ads). Keep any league marks in one asset folder and one template partial so they can be removed in one change.
- The "sample screen" in the page is a CSS mock labelled as a sample, with placeholder names. Replace it with real screenshots or a demo video once the draft room exists.
- Signups go to `waitlist_signup` (migration 0005): email (lower-cased, unique, so repeats are a silent no-op), optional first name and fantasy experience, `utm_*` from the link, and `consented_at`. The form posts, then redirects to `/thanks` (same answer for duplicates, so the list cannot be probed for who has signed up).
- Spam: a hidden honeypot field (silently accepted and dropped) and an in-memory limit of 5 posts per 10 minutes per client IP (first `X-Forwarded-For` entry). The limiter resets on restart and is per process, which is fine for one web instance. No CSRF token on this form since there is no session yet; accounts (Phase 3) add CSRF.
- Form bodies are parsed with the standard library (`urllib.parse.parse_qs`, 8 KB cap) so the page needs no `python-multipart` dependency. Phase 3 will add it for login forms.
- No alerts or admin page yet (deferred). Read signups in Railway's Postgres Data tab. `CONTACT_EMAIL` (optional env var) is shown on the privacy page; until set, the page says to message the social accounts.
- Fonts (Sora, Inter) load from Google Fonts, which the privacy page discloses. Self-host them later if that matters.

## 2026-10-14: App shell and tab mockups (/preview)
- The four tabs (Matchup, Team, Players, League) are real Jinja templates under `web/templates/app/`, styled by `static/app.css` from the same tokens as the landing page. Mobile: fixed bottom tab bar; desktop (800px and up): tabs under the header. These templates are the design that later phases wire to real data.
- Until then they render at `/preview/{matchup,team,players,league}` on sample data from `web/preview_data.py`. Player names, teams and points there are invented, and every preview page carries a "sample data" banner. The previews are always on outside prod, and in prod only when `PREVIEW_ENABLED=true`. They are `noindex`.
- Matchup shows both lineups slot by slot with status (final, live, yet to play) and the stat line. Team shows the lineup, bench and IR with games this week and a lock indicator. Players has search, position and availability filters (client-side for the demo). League has the invite link, standings, this week's matchups and the activity feed.
- Lesson: `backdrop-filter` on the sticky header made the `position: fixed` bottom tabs position relative to the header. The tabs are now a sibling of the header, not a child.

## 2026-10-14: Preview mockups use real names and real 2025-26 stats
- `/preview` now shows real PWHL players, teams, positions and per-game stat lines from the 2025-26 season (week 4 of that season, Dec 15 to Dec 21), scored by our own scoring engine, so the demo shows what the app really computes.
- What is invented, and said so on every preview page: the two fantasy teams and their managers, the draft, the standings and the transactions. We never invent injuries, and we do not show a game as live (there is no live data for a past season); players are either done for the week or have games to come.
- `src/undertow/web/preview_data.json` is generated, not hand-edited: load the season into a throwaway local database (`uv run python -m undertow.ingest.replay --season 8 --offline`), then `uv run python -m undertow.web.build_preview --season 8`. The app only reads the committed JSON, so no database or feed is needed at runtime.
- Same unofficial-and-unaffiliated marker applies. Player names and stats are public facts; do not add photos or logos to the demo without revisiting the league-marks decision above.

## 2026-10-14: Preview v2 (bench, rivals, help tips, player pages, trending)
- Matchup shows both benches under the starters with their points, a "points left on the bench" total, and a highlight on each team's best bench performer when they beat a starter. Starters and bench names link to player pages. The scoreboard has a team-photo placeholder (initials) until the profile page exists (see [backlog.md](backlog.md)).
- Every one of the 10 sample teams has a real roster drafted by points per game, so League matchups link to a full matchup page (`/preview/matchup/{n}`, matchup 1 is the viewer's). Rosters are one goalie plus F, F, D, D on the bench because only 12 goalies had played enough games to draft.
- "Now" is week 8 of 2025-26 (Jan 12 to 18), picked as an early week with many games and a goalie just off a shutout. Season-to-date stats count only games before that week, so the player page's weekly history stops at week 7.
- Help tips: a `?` button (hover, keyboard focus or tap) with a plain-language explanation, via the `help` macro. First uses: Games, Lineup, Bench, IR, Locked, Trending, FP/game, PF, UTIL. Rule recorded in brand.md.
- Player pages exist for every player on a sample roster or in the Players tab; the featured one is the top forward on the viewer's team. Headlines are a placeholder card; building them is in the backlog.
- Players tab sorts by Trending (default), FP/game or Season, in the browser. Trending = last 3 games' average minus season average, plus a bonus for a goalie fresh off a shutout; the Hot tag appears at 1.0 or more. First-pass heuristic.
- League tab: the invite card is removed (leagues are managed by Undertow this season) and each matchup row links to its matchup page. This conflicts with Plan section 6 and Phase 3; recorded in the backlog to revisit.

## 2026-10-14: Draft room demo and the basic recommendation model
- `/preview/draft` is a full-screen practice draft: snake board (every team a column, your column highlighted, the pick on the clock glowing, → and ← showing direction), clock banner with your timer, a Recommended card with a plain-English reason and a "Draft" button, a confirm sheet ("Round 1, Pick 4: Select Name (F)?"), Players (search, position filter, queue star), Queue, My team (what the lineup still needs), a strip of the latest picks, and an auto-draft switch. The intro explains snake order, roster shape and Recommended in plain words, with `?` tips. You pick 4th of 10, 11 rounds (4 F including the UTIL flex, 2 D, 1 G, 4 bench), and the other nine teams are drafted by the computer.
- Plan section 5 is followed (board, banner, available players with search, queue, my roster, live pick feed). Drag-to-reorder for the queue is deferred (up/down buttons for now).
- One implementation of the rules: `services/draft.py` is pure (no HTTP or database) and holds snake order, needs, valuations, recommendations and computer picks. The page keeps the pick list in the browser and asks `POST /preview/draft/step` what happens next, so the demo cannot drift from the real rules. Query options for demos: `?autostart=1`, `?clock=<seconds>`, `?pace=<ms between computer picks>`.
- Model, deliberately basic: (1) points per game last season (2025-26, real, scored by our engine), shrunk toward the position average with a 4-game prior so a small sample does not top the list; (2) projected season points = shrunk rate times games, with games regressed 30% toward a full season; (3) value over replacement, where replacement is the best player who would not start (half a bench past the starters for F and D, the last team's starter for G); (4) a pick that fills a starting spot counts fully, bench depth counts 45% (a spare goalie 12%); (5) hard rules: at most 2 goalies, and once only enough picks remain to fill the starting spots every pick must fill one; (6) goalies are flagged and taken when the pool barely covers the teams that still need one. Computer teams pick the best fit 60% of the time and one of the next three otherwise, with a small fixed lean per team, seeded so a draft is repeatable.
- Result checked: every computer-drafted team ends with at least 4 F, 2 D and 1-2 G over many seeds; a first round mixes the four elite goalies with the top forwards and defenders. An earlier version put eight goalies in the first ten picks because its replacement-level goalie was too low; fixed by using the last starter as replacement. Goalies are genuinely valuable under the default scoring, so they still go early, and the recommendation explains why.
- Known limits are listed in [backlog.md](backlog.md) (injuries, schedule, no-history players, real average draft position). The draft room is always available outside prod and in prod only with `PREVIEW_ENABLED=true`.

## 2026-10-14: Landing page screenshots and static asset versioning
- The landing page's "A peek at the app" section is a swipeable row of six phone screens (draft room recommendation, draft board, Matchup, Team with a `?` tip open, Players on Trending, League), each with a one-line caption and alt text. The earlier CSS mock of a draft screen is removed. Images are 390x844 at 2x as WebP (about 75 KB each, lazy-loaded) in `static/demo/`.
- They come from the `/preview` pages (real 2025-26 PWHL players and stats, made-up teams). The "sample data" banner is cropped out of the images, and the section says in words that these are sample screens with real stats and made-up teams. League logos and photos are not in them.
- Regenerate them whenever the design changes: start the dev server, then `uv run --with websocket-client python scripts/capture_demo_screenshots.py` (needs Google Chrome; the script drives it through DevTools and plays the first draft rounds by hand for the board shot). `websocket-client` is not a project dependency.
- Static asset URLs now carry `?v=<hash of the static files>` (templates use `{{ v }}`), so a changed stylesheet, script or image always loads fresh after a deploy instead of coming from a browser's stale cache. Found while checking the screenshots: a reused browser profile served an old `site.css` and the new section looked unstyled. Visitors would have hit the same thing for a while after any CSS change.
- Also fixed on the way: the draft room's "Other good options" box picked up the FAQ `details` styling, and turning auto-draft off during its short delay still made the pick.

## 2026-10-15: Signup supports requesting a friend's league placement
- The landing signup form has an optional "Signing up with a friend? Their email" field (`commissioner_note`, free text up to 300 chars, migration 0006). It is stored as-is and read manually when placing people into the two leagues this season; there is no automated matching yet, since leagues are hand-assembled by Undertow for 2026-27 (see the League tab entry above). FAQ entry added explaining it.

## 2026-10-15: A bolder, two-tone brand mark
- Compared against a competitor (pdubfantasy.com): calm and uncluttered is right for the in-app screens, but the brand itself read as safe/generic next to their loud purple hero and distinctive logotype. Decision: spend personality on a few deliberate moments (logo, hero, primary CTA) rather than spreading it everywhere, keeping the flat-list app screens as they are.
- The wordmark now carries a small two-tone wave mark (blue wave, orange crest behind it, using `--primary`/`--accent`) in the header on every page and in `favicon.svg` — the smallest instance of the logo now shows the same wave. Landing hero gained a third wave layer in orange behind the existing two blue ones, with all three layers bumped in opacity. The primary button (`.btn`, used for every main CTA including the draft room's) is now a blue-to-orange gradient instead of flat blue.
