# PWHL Fantasy: Manual To-Dos (things only you can do)

Written Oct 12, 2026. Ordered by when each blocks development. Phase numbers refer to [Plan.MD](Plan.MD).

## What I found in the current environment

- `WSportsFantasy/` has only `Plan.MD`. Git remote is `github.com/msaunders804/WSportsFantasy` and `gh` is logged in, so repo access is fine. No code, Docker files, or CI exist yet.
- This dev box (`redcastle-ec2.mrsl.internal`, 32 cores, 123 GB RAM) has Docker 29, Compose v5, psql 16, Python 3.12, Node 18. Good for local dev. **Node 18 is old**, which matters only if we build Tailwind with a current CLI (the standalone Tailwind binary avoids this).
- Docker is already running a Caddy proxy for another project (Plane, bound to 127.0.0.1:3100). This is a **company** machine on an internal hostname. I would not put a public, multi-user app with real user passwords on it.
- **Plan conflict:** Plan.MD says "cloud VPS, Docker Compose with Caddy". Your Football Survivor `hosting_plan.md` says you already pay for **Railway ($5/mo)** and have used it for PWHL analysis. Football Survivor also has a `render.yaml`. See item 1 below. It changes the shape of Phases 1 and 6.

---

## A. Decide this week (blocks Phase 1, due Oct 19)

### 1. Pick the hosting platform
The plan needs three long-running pieces: `web`, `worker` (scheduler, live poller, draft clock), and Postgres, with LISTEN/NOTIFY and SSE.

| Option | Pros | Cons |
|---|---|---|
| **Railway (recommended)** | You already have an account and a $5 plan. Git-push deploys, managed Postgres, multiple services per project (web + worker). | The plan's Caddy and Compose are dropped. Usage-based billing, and this project plus the other PWHL work may push you to ~$10/mo in Nov-Dec. Check that long-lived SSE connections and the worker's always-on usage fit the credit. |
| Small VPS (Hetzner, DigitalOcean, Lightsail, about $6-12/mo) | Matches Plan.MD exactly (Compose + Caddy + Postgres). Full control, predictable cost. | You own patching, backups, TLS, and uptime. More ops work during the draft crunch. |
| Render | You already have a blueprint pattern. | Free tier sleeps, which kills the worker and the draft timer. You would need paid always-on services. |

**Decision needed:** Railway or VPS. Tell me and I will adjust Phase 1 (Dockerfile + `railway.toml`, or Compose + Caddy). Either way I will build with a Dockerfile and env-var config so switching later is cheap.

### 2. Decide the database location
- Railway: provision Postgres in the same project. Prefer a **separate project** (or at least a separate database) from your PWHL analysis SQL so a bad migration cannot touch it.
- VPS: Postgres in Compose with a volume.
- Whatever you pick, I need a **dev** database (I can run local Postgres in Docker, no action needed) and a **prod** database (you create it).
- **Important for the live draft:** the app uses Postgres LISTEN/NOTIFY. This needs a **direct** (session-mode) connection, not a transaction-mode pooler (e.g. PgBouncer, Supabase pooler on port 6543). Keep this in mind if you choose a managed Postgres that fronts everything with a pooler.

### 3. Pick the domain / public URL
- Buy or choose a domain (or subdomain of one you own). Needed for TLS, session cookies (`Secure`), and invite links.
- A Railway-provided `*.up.railway.app` URL is fine for the mock draft if you want to defer this, but invite links you send people should be on the final domain.
- Tell me the final hostname when you have it.

### 4. Name the app
Used in the page title, email sender name, and domain choice.

---

## B. Accounts and credentials (blocks Phases 2-6)

### 5. GitHub repo setup
- Repo exists. Decide: **private** (recommended) or public.
- Decide the branch policy (I suggest `main` protected, work on feature branches, PRs).
- Enable GitHub Actions on the repo (needed for CI in Phase 1). Optional: Dependabot.
- Add any collaborators (the Survivor plan mentions coworkers; same question here).

### 6. Email sending (password reset, invites, alert emails)
Plan.MD calls for email alerts when live polling fails, and password reset needs email.
- Pick a provider: Resend, Postmark, or Amazon SES (you are on AWS already). Free tiers are fine at this scale.
- Verify your sending domain (SPF/DKIM DNS records), which depends on item 3.
- Create an API key and give it to me as an env var, not in chat or git.
- Alert address: which inbox gets "live feed down during a game" alerts?

### 7. Off-site backup storage
Plan: nightly `pg_dump` to off-site storage with a restore test.
- Create a bucket (S3, Backblaze B2, or Cloudflare R2) and a write-only credential for the backup job.
- Railway's built-in backups do not count as off-site; this is separate.

### 8. Secrets management
- Decide where secrets live: Railway variables or VPS `.env` (never in git). You will set: `DATABASE_URL`, `SECRET_KEY`, `HOCKEYTECH_KEY`, email API key, backup credentials.
- I will provide a `.env.example` with every variable named but empty.

### 9. Monitoring (optional but recommended before the mock draft)
- An uptime monitor on the public health page (UptimeRobot / Better Stack free tier) so you hear about outages before your leagues do.

---

## C. League and product decisions only you can make (blocks Phase 3, due Nov 2)

### 10. Commissioner and co-commissioner for each league (Plan open item 3)
Four people needed (2 leagues). The plan requires a co-commissioner in each league to handle trades where the commissioner is a party. Email addresses for each to seed the first accounts.

### 11. Recruit and schedule people
- 20 managers across 2 leagues (10 each). Collect emails, or plan to share invite links.
- **Pick a draft date for each league.** Must be before Dec 5, and the earlier the better. Suggested window: the week after preseason starts (Nov 22), so training camp rosters are known. Check against the Nov 20 MVP target: drafts after Nov 22 leave you a mock-draft window.
- Schedule a **mock draft with real users** for about Nov 18-20 (Phase 6).

### 12. Confirm default rules (from Plan open items, can be answered later)
- **Scoring table:** you need to confirm against ESPN's current hockey points defaults before Phase 7 (about Nov 16). I can pull it up, but you decide.
- Roster size and lineup (3F/2D/1UTIL/1G, 4 bench, 1 IR). OK as-is?
- Waiver length (48 h), trade review (24 h), trade deadline (end of Week 13). OK as-is?
- Timezone behavior and draft pick timer length (e.g. 60/90/120 seconds). Mock draft will tell you.

---

## D. Data source risk (needs your judgement, Phase 0 due Oct 16)

### 13. Accept (or reject) the unofficial HockeyTech feed
Plan.MD relies on an undocumented feed using a public client key. The feed is not licensed for this use.
- I will verify it works, cache aggressively, and use an identifying User-Agent.
- **You decide** whether you are comfortable with that risk for a private, small-group app, and whether to contact the PWHL. Also decide on a **fallback** if the feed breaks mid-season (manual stat entry by commissioners is the realistic one; I can build an admin override if you want it).
- No signup is needed for the key itself. I will find it as part of Phase 0.

### 14. Odds API
Not used in this project. Ignore. (Noted only because your memory says to conserve those calls.)

---

## E. Before launch (Phase 6, due Nov 20)

- [ ] Point the domain's DNS at the host and confirm TLS works.
- [ ] Put production secrets in the host's environment.
- [ ] Run one restore test from the off-site backup.
- [ ] Send invite links to the managers (collect addresses from item 10).
- [ ] Tell everyone about the data caveat: the rankings are a rough guide, and expansion players have no PWHL history.
- [ ] Privacy basics: a short note on what is collected (email, name, password hash). Optional for a private group, required if you let strangers sign up.

---

## Fast path: the minimum I need from you to start Phase 0-1

1. Hosting choice (item 1): Railway or VPS.
2. OK to use the private GitHub repo `msaunders804/WSportsFantasy`, with feature branches (item 5).
3. App name, or "pick one for now" (item 4).

Everything else can wait until the phase that needs it. Phase 0 (feed discovery) and local development need none of the accounts above.
