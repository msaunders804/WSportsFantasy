# Backlog: product ideas not yet built

Things the owner wants, written down so they are not lost. Scope and rules stay in [Plan.MD](../Plan.MD); design decisions in [decisions.md](decisions.md). Add new items at the top of each section.

## Draft room: what the demo does not do yet (Phase 5 makes it real)
- **Real, shared draft:** the demo keeps the pick list in one browser. Phase 5 needs picks as single database transactions with constraints, a server-side clock, live updates to everyone (SSE), reconnects, commissioner controls and a draft-order screen. The pure rules in `services/draft.py` carry over.
- **Recommendation realism (wanted, "dedicated time"):** today it is value over replacement from last season's points per game, shrunk toward the position average, weighted by roster needs. Still to do: injuries and the games each team plays (some teams have lighter schedules), players with no PWHL history (rookies, expansion teams), real average draft position from the first leagues, tuning the shrinkage and replacement levels, goalie platoons, bye-week and schedule strength, and checking the picks against what experienced players expect. Revisit once the first mock draft has run.
- **Queue:** drag to reorder (up/down buttons today). Pick trades, draft grades, a "you reached" or "great value" note after each pick, and a pause for the commissioner.
- **Beginner help:** a short first-time tour of the board, and a plain-language note when someone ignores a recommendation in a way that leaves a gap (for example no goalie).

## Chat (decided, not built)
Decisions and reasons are in [decisions.md](decisions.md) (2026-10-15, in-app chat). This is the build list. Nothing here is started, and no code has been written.
- **Blocked on Plan Phase 3:** email and password auth, server-side sessions and CSRF, league and team tables, membership checks, and named commissioners and co-commissioners (Plan.MD open question 3). Every chat endpoint and SSE stream checks league membership.
- **Tables:** `chat_message` (league, optional trade, author team, body, created_at, edited_at, deleted_at, deleted_by), `chat_read_state` (last message seen per member), `chat_report`. Trade threads reuse `chat_message` with a trade id.
- **Rules in `fantasy/chat.py`:** length limit, post rate limit, edit window of 5 minutes, author or commissioner can delete, only the two teams and the commissioner see a trade thread, a closed trade thread is read-only.
- **Real-time:** a message write fires NOTIFY, and open pages stream it over SSE with `Last-Event-ID` so a dropped connection catches up. One listener per web process fans out to its streams, to keep database connections low. Connect in session mode, never through a pooler.
- **Pages:** League tab room, an embedded panel in the draft room, a thread on each trade offer, unread badges. Load the last 50 messages and fetch older ones on scroll.
- **Moderation before launch:** commissioner delete, a report button that reaches us, a short conduct rule accepted when joining, and a "removed by commissioner" placeholder in place of a deleted message.
- **Privacy page:** add a chat section covering what is stored, that it is kept for the season, and who can read it. The page currently says email is used for invites and updates only.
- **Rendering safety:** escape all text, no raw HTML, auto-link URLs with `rel="nofollow noopener"`.
- **Not in the first version:** chat emails and push notifications (needs notification preferences), direct messages outside trades, images, and reactions.
- **Enable:** set `CHAT_ENABLED` (add to `.env.example`), try it in one league first, and have it on before the draft window opens Nov 30. Draft chat needs auth and moderation too, so a draft-only room is not a way around them.
- **Tests:** service rules (edit window, delete rights, trade visibility), database tests for membership checks and the paging cursor, and an SSE stream test using fixtures.
- **Open:** whether the commissioner can read trade threads (assumed yes), and a time limit on how long ago a member can scroll back.

## Profile page (wanted)
- A profile page where a manager can **change their team name**, **add a team photo**, and **give their players nicknames**.
- Where it shows up: the team photo replaces the initials circle on the Matchup scoreboard (the placeholder is in `app/_macros.html`, `avatar`); the team name shows everywhere a team appears; nicknames show next to or in place of the player's name on Matchup and Team.
- Needs: image upload and storage (size and type limits, moderation of what others see), a nickname length limit, and the commissioner being able to reset a team name or photo.

## Player pages
- **Headlines** (wanted): news about the player gathered from around the web, plus our own notes on the numbers and the story. The player page has a placeholder card for it. Open questions: sources and licensing, how to link out versus summarise, and who reviews our own notes. Do not scrape without checking terms.
- More on the page later: full game log, schedule, trend chart, ownership across leagues, add/claim/trade buttons.

## Players tab
- **Trending** is a first-pass heuristic: last 3 games vs. season average, plus "just off a shutout", "won last 3 starts" and similar. Tune it against real play once the season is under way, and decide how it should treat small samples.

## League tab
- The invite link was removed from the League tab: this season's leagues are managed by Undertow. Plan section 6 (join by invite link) and Phase 3 (invites) need revisiting: do we still need invite links, or does the commissioner add people directly? Also check the landing page step "You'll get an invite link" for the same reason.

## Explain as we go
- Brand rule: never leave a user guessing. Every term or number that is not obvious gets a "?" tip (`help` macro in `app/_macros.html`). When a new page adds a term, add its tip in the same change.

## Account updates (wanted)
- **Staff-posted updates on `/account`:** the account page shows the member's status and the key dates, but not a feed of announcements ("drafts are open", "your league is ready"). Needs an `announcement` table (title, body, published_at, optional league), a staff screen to post, and an "unread" marker. Decide whether each post also emails, which needs notification preferences and an unsubscribe link in every email.
- **Unsubscribe:** the signup consent says "I can unsubscribe any time" and today that means emailing us. Add an unsubscribe link and a stored preference before sending anything beyond account links.
- Account page extras: change name, password and email, and a "log out everywhere" button (`auth.end_all_sessions` already exists).
