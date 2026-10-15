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

## 2026-10-15: Fixed accessibility and alignment defects from design review
- `--accent` and `--border` in the light theme (`tokens.css`) are darkened from the original brand values (`#e85a38`→`#c54d30`, `#c9d9ea`→`#7489a6`) to meet WCAG contrast minimums: white button text on the old accent was 3.5:1 against a 4.5:1 requirement, and the old border was ~1.4:1 against a 3:1 requirement for interactive-component boundaries (1.4.11). Dark theme was already fine and is unchanged.
- The theme boot script (`base.html`) only read `localStorage`; a device with no stored preference always got the hardcoded dark default regardless of system theme. It now falls back to `prefers-color-scheme` when nothing is stored.
- `.page`'s padding shorthand (`padding: var(--space-5) 0 var(--space-6)`) was zeroing out `.wrap`'s horizontal gutter wherever both classes were combined on the same element — affected every page using `.page` (Privacy, About, Fantasy 101, and the homepage FAQ section, which also got a readability-width `max-width` it shouldn't have had since it's a landing-page section, not a standalone reading page). Changed to `padding-top`/`padding-bottom` only, and dropped `.page` from the homepage FAQ wrap.
- The screenshot carousel's dots used `role="tab"` with `aria-current` (not `aria-selected`), no `aria-controls`/tabpanel, and all six were tab stops — an incomplete tabs pattern. Simplified to plain buttons with `aria-current`, which is what they were already functionally doing.
- Carousel dot tap targets were 8×8px (below the 24px minimum, WCAG 2.5.8); now a 24×24 button with an 8px visual dot via `::before`. The carousel arrows were missing `font: inherit`, so their `‹ ›` glyphs rendered in the browser's default UI font instead of Inter.
- `draft-recommendation.webp` and `draft-board.webp` are regenerated with the draft room's own header (`.dr-top`, which has its own "Light mode" pill) hidden during capture, via the same CSS-injection technique already used to hide the sample-data banner — the duplicate pill read as a second, confusing piece of real UI.

## 2026-10-16: New-signup email alerts, via Resend
- Megan chose Resend over Postmark/SES for `TODO_MANUAL.md` item 8 (simplest setup, generous free tier). `services/notify.py` posts to the Resend API; `EMAIL_API_KEY` and `ALERT_EMAIL` were already placeholders in `.env.example`, plus a new `EMAIL_FROM` (must be on a domain verified with Resend — still Megan's manual setup step, not done yet).
- Fires once per *new* signup only (not the quiet duplicate no-op), from the `/signup` route in a threadpool so it can't block the response. Never raises: a failed send is logged and swallowed, since a broken alert must never break the visitor's signup. With no `EMAIL_API_KEY`/`ALERT_EMAIL` set (true today, until Megan finishes the Resend setup), it's a silent no-op.

## 2026-10-15: Phase 3 foundations: accounts, leagues, teams
What is built: email and password accounts, login sessions, leagues with settings, teams, membership with commissioner roles, an audit log, and pages to register, log in, join by invite link, create a league (staff) and edit settings (commissioner). Rules are in `services/league_rules.py` (pure), `services/auth.py` and `services/leagues.py`; migration 0007.
- **Table is `user_account`, not `user`.** `user` is a reserved word in Postgres, so the Plan's `user` table would need quoting everywhere.
- **Passwords** use argon2 (library defaults), 10 to 128 characters, no composition rules. Login timing is the same for an unknown email and a wrong password, and logins are rate limited per IP and per email.
- **Sessions** are random tokens in an HttpOnly, SameSite=Lax cookie (Secure in prod). Only the SHA-256 of the token is stored. 30 days, then log in again. A disabled account stops working immediately.
- **CSRF** on every state-changing form: a random cookie plus a hidden field holding an HMAC of it under `SECRET_KEY`. It works for logged-out forms too (login, register). `SECRET_KEY` must be a real secret before accounts open; the app refuses to start in prod with the default.
- **Roles:** `member`, `commissioner`, `co_commissioner` (the Plan listed `owner`, which is just "has a team"). Partial unique indexes allow one commissioner and one co-commissioner per league. A person who is not a member sees a 404 for a league, not a 403, so league ids are not revealed.
- **Only Undertow staff (`user_account.is_admin`) create leagues and assign commissioners.** This season's leagues are run by Undertow (see the League tab note in backlog.md). Staff is set directly in the database for now. Commissioners edit settings and see the invite link; they cannot replace each other.
- **Invite links:** each league has a secret `invite_code`, and anyone with the link who has an account can join until the league is full. Chosen for now because it needs no email system; the open question in backlog.md (do we need invite links at all, or does the commissioner add people?) still stands. Joining locks the league row, so simultaneous joins cannot overfill a league (tested with threads), and one team per person per league and unique team names (case-insensitive) are database constraints.
- **Settings** are integer columns plus the scoring config as exact strings. The web page edits the integers only; scoring values are not editable yet. Settings lock when the league leaves `setup`. Every change is written to `audit_log` with before and after.
- **Default playoff teams is 6, capped at the league size**, so a 4-team league does not fail its own validation. Warnings (not errors) appear when rosters would use over half the active player pool or when goalie slots exceed the 12 starting goalies in the PWHL.
- **Feature flag:** `ACCOUNTS_ENABLED` (off by default in prod, always on in dev and test) hides the account pages and the "My leagues" nav link until the launch checklist says accounts open.
- **Not done in Phase 3 yet:** commissioner wizard (draft order, seeded schedule, regenerate), invite emails, password reset, an account page (change name, password, timezone), a staff screen to make someone staff, editing scoring values, deleting an account. Draft order and schedule generation belong with Phases 5 and 7.

## 2026-10-15: Two IR spots, kept outside the roster
Decided by Megan. Each team has 2 injured reserve spots that are separate from the 11-player roster (7 starters plus 4 bench), so a team can own up to 13 players. This replaces the Plan's single IR spot counted inside 12 players, and settles a clash with the Fantasy 101 page. The draft stays 11 rounds. Plan.MD 4.1, Fantasy 101 and the league defaults (`roster_ir = 2`) are updated. The player-pool warning counts players on IR (10 teams use up to 130 of about 276), since an owned player is off the waiver wire. No schema change: the default lives in code.

## 2026-10-15: In-app chat (DECIDED by Megan; design only, nothing built)
Full write-up of what is left to build is in [backlog.md](backlog.md#chat-decided-not-built).
- **Scope:** a league room for each league, plus trade threads, so managers can negotiate trades inside Undertow. The draft room embeds the league room, and chat is wanted for the draft (Nov 30 to Dec 4).
- **Moderation:** the commissioner can delete messages in their league. Undertow staff can also remove messages and handle reports. A "report message" button goes to us.
- **Retention:** messages are kept for the season, and new members can read the history. Deletion is a soft delete (`deleted_at`), so a removed message can be audited. The privacy page must say this before launch.
- **Editing:** an author can edit their own message for 5 minutes after posting. Edited messages show an "edited" mark. After 5 minutes a message can only be deleted, not changed.
- **How it is built:** the same SSE and Postgres LISTEN/NOTIFY design as the draft and matchup (Plan.MD section 2). No new infrastructure and no third-party chat service. Rules live in `services/chat.py` with no HTTP code. All times are UTC.
- **Gating:** behind a `CHAT_ENABLED` flag, off by default. It depends on auth, leagues and teams (Plan Phase 3), so chat cannot ship before those.
- **Why trade threads are in scope:** trades are in the plan (Plan.MD transactions), and a trade offer with nowhere to discuss it pushes people to outside group chats. A trade thread belongs to one trade, is visible only to the two teams involved and the commissioner, and closes when the trade is accepted, rejected or withdrawn. Open: whether the commissioner can see trade threads or only league rooms. Assumed yes, since trades need commissioner oversight; confirm before building.

## 2026-10-15: One repo for fantasy, data and content (package restructure)
Decided by Megan: a single repo handles all Undertow work, including analysis for social media. Code moved with `git mv` (history follows the files), with no behaviour change: the same 225 tests pass, a fresh database migrates to 0007, and the Docker image builds and imports.

| Before | After |
|---|---|
| `db/` | `data/base.py`, `data/session.py` (import from `undertow.data`) |
| `models/` | `data/models/` |
| `migrations/` | `data/migrations/` (`alembic.ini` points at it; revision ids are unchanged, so Railway's existing `alembic_version` still matches) |
| `scoring/` | `fantasy/scoring/` |
| `services/draft, auth, leagues, league_rules, stat_lines, waitlist` | `fantasy/` |
| `services/notify.py` | `notify.py` (shared by every package) |
| `tests/scoring`, `tests/services` | `tests/fantasy/`; web tests moved to `tests/web/` |

- **New:** `content/` (analytics, stat notes, sims; empty scaffold) and `analysis/` at the repo root (notebooks and one-off work, never deployed: not copied into the image).
- **Kept as they were:** `ingest/`, `web/` and `config.py`. `worker/` also stays, because Railway's worker start command is `python -m undertow.worker.main` and renaming it would mean a dashboard change.
- **Rule:** nothing outside `web` imports `web`; `fantasy`, `content` and `ingest` use `data`. The only cross-link is `ingest/replay.py`, which uses `fantasy.scoring` to check replayed games.
- **Older entries** in this file still mention the old paths (`services/draft.py` and so on). They describe what was true when written, so they were left as they are.
- No import shims were left behind. Any branch or notebook that imports `undertow.db`, `undertow.models`, `undertow.scoring` or `undertow.services` needs its imports updated.
