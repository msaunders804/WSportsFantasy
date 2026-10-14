# Manual To-Dos (things only you can do)

Updated Oct 14, 2026. Phase numbers refer to [Plan.MD](Plan.MD). Decisions already made are in [docs/decisions.md](docs/decisions.md).

## Done
- Hosting: **Railway** (web, worker, Postgres as separate services). App name: **Undertow Sports**.
- Playoff format: three 2-week rounds. Trade deadline end of Week 11.
- Phase 0 (feed discovery), Phase 1 (skeleton, PR #1) and Phase 2 (ingestion, worker jobs, scoring engine, PR #2) are built.

## Do now
1. **GitHub token scope** (blocks CI): run `gh auth refresh -s workflow`, then tell me. I will move `ci/ci.yml.pending` to `.github/workflows/ci.yml`.
2. **Review and merge** PR #1, then PR #2 (retarget each to `main` after its parent merges). Phase 0 is on branch `phase-0-feed-discovery` and has no PR yet.
3. **Repo settings:** confirm the repo is private, protect `main`, enable GitHub Actions, add collaborators if any.

## Needed before Phase 3 (Nov 2)
4. **Commissioner and co-commissioner for each league** (4 people, with emails). The co-commissioner handles trades the commissioner is party to.
5. **Railway project:** create it with a Postgres service. Use a database separate from your PWHL analysis one, and a direct (non-pooled) connection, because LISTEN/NOTIFY and the worker's advisory lock break behind a transaction pooler. Add a `worker` service with start command `python -m undertow.worker.main`.
6. **Domain:** choose the public hostname. Needed for secure cookies, invite links and email. A `*.up.railway.app` URL is fine for the mock draft.
7. **Secrets** in Railway variables (never in git): `DATABASE_URL`, `SECRET_KEY`, `HOCKEYTECH_KEY`, `HOCKEYTECH_USER_AGENT` (with a contact address), email and backup credentials. `.env.example` lists every variable.

## Needed before the mock draft (Nov 18-20)
8. **Email provider** (Resend, Postmark or SES): verify the sending domain, create an API key, and pick the inbox for "live feed down" alerts.
9. **Off-site backup bucket** (S3, B2 or R2) with a write-only credential, and one restore test. Railway's own backups do not count.
10. **Uptime monitor** on the health page (optional, free tier).
11. **Recruit 20 managers** (10 per league) and **pick a draft date for each league**. Both before Dec 5; after training camp rosters (announced from Oct 18) and ideally the week after preseason starts (Nov 22). Schedule a mock draft with real users for Nov 18-20.

## Decisions for you
12. **Scoring table** (before Phase 7, about Nov 16): confirm against ESPN's current hockey points defaults. Roster slots (3F/2D/1UTIL/1G, 4 bench, 1 IR), 48 h waivers, 24 h trade review and the draft pick timer (60/90/120 s) are the other defaults to confirm.
13. **Feed risk:** the HockeyTech feed is unofficial and unlicensed for this use. Decide whether you accept that for a small private app, whether to contact the PWHL, and whether you want a manual stat-entry fallback for commissioners if the feed breaks.
14. **Clean the fixtures (optional):** the recorded fixtures contain the public site key in their `Parameters` block. Say so if you want it scrubbed.

## Before sharing the landing page link (before the first social post)
- [ ] **Use your own domain.** Railway web service → Settings → Networking → Custom Domain; add the CNAME it gives you at your DNS host; wait for TLS. Then set `BASE_URL` to the new domain and share that link, not the `*.up.railway.app` one.
- [ ] **Set up domain email forwarding** (for example `hello@yourdomain` into your inbox) at your registrar or with Cloudflare Email Routing. This is receiving mail; sending invite emails later needs an email provider (item 8).
- [ ] **Update the feed User-Agent to the Undertow address:** on the `worker` service set `HOCKEYTECH_USER_AGENT` to something like `UndertowSports/0.1 (contact: hello@yourdomain)`, then redeploy the worker.
- [ ] **Set `CONTACT_EMAIL`** on the `web` service to the same address, so the privacy page shows it instead of "message us on social".
- [ ] **Hide the design previews:** delete (or set to `false`) `PREVIEW_ENABLED` on the `web` service, redeploy, and confirm `/preview/matchup` returns a 404.
- [ ] Confirm the landing page claims are true (for example "free to join") and the "unofficial and unaffiliated" footer is on every page.
- [ ] Delete test rows from the `waitlist_signup` table.

## Launch checklist (Phase 6, Nov 20)
- [ ] DNS points at Railway and TLS works (see the custom domain item above).
- [ ] `PREVIEW_ENABLED` is not set on `web` in production.
- [ ] Production secrets set.
- [ ] Restore test from off-site backup done.
- [ ] Invite links sent to managers.
- [ ] Managers told that rankings are a rough guide and expansion players have no PWHL history.
- [ ] Short privacy note (email, name, password hash collected).

## Notes
- Oct 13: I ran the full 2025-26 replay against the live feed (about 130 throttled requests, once). Responses are cached locally in `data/replay_cache/` (gitignored), so it does not need to hit the feed again.
- The scoring defaults are untested against real play beyond the replay; confirm item 12 before Phase 7.
