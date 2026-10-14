# Backlog: product ideas not yet built

Things the owner wants, written down so they are not lost. Scope and rules stay in [Plan.MD](../Plan.MD); design decisions in [decisions.md](decisions.md). Add new items at the top of each section.

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
