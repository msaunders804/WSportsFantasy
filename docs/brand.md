# Undertow Sports: brand foundation (draft v0.1, Oct 14, 2026)

Owner and final say: Megan. Everything that ships goes through her. This page is the tie-breaker. Visual values here become design tokens in code (`src/undertow/web/static/tokens.css`) so web, app and social templates start from the same values.

## Architecture
- **Undertow Sports**: women's sports coverage built on numbers and stories. The parent brand.
- **Undertow Fantasy**: the PWHL fantasy leagues. The first product, under the parent.

## Who it's for
- **Undertow Sports:** sports fans who want more women's sports and want to understand the numbers and stories behind what happens on the ice or field.
- **Undertow Fantasy:** newcomers to fantasy who love women's sports, and people looking for a way into women's hockey. They want to connect with the PWHL community and have competitive fun during the season.

## Promise
Women's sports coverage built on the numbers and the stories, by women, for women's sports.

## The name
Numbers rarely tell the whole story, and stories rarely show all the numbers. Undertow is the current underneath: the combination of both. Josh Allen's "this is why we play the game, it isn't played on paper" is the spirit: anything can happen, and the numbers can't capture every force at work. We use stats and the current situation to paint the full picture.

## We are not
Bro-ish, focused on men's sports, a gatekeeper, spammy, fake, or exaggerated. No driving engagement through discourse, and no dunking on failures or slip-ups.

## Personality (three people at the game)
1. **The invested friend** who knows way too much about the league and drops facts when you get to the game. *Knowledgeable, but never showing off.*
2. **The big fan who isn't biased.** Loves the sport, doesn't glaze a team or favour a player. *Passionate, but fair.*
3. **The friend who got you to your first game** and makes sure you understand what's happening and are never left in the dark. *Welcoming, but never condescending.*

## Voice and tone
- **Voice (fixed):** friendly, human, clear. Not robotic. Plain words, no jargon without a quick explanation. Transparent about what we know and don't know.
- **Tone (varies):** celebratory when things go well, kind and straightforward when they don't, never mocking.
- **Product UI:** say only what's needed. Extra text appears only when it helps.
- **Explain as we go:** never leave a user guessing. Anything that is not obvious (a column like "Games", a slot like UTIL, a sort like Trending) gets a small "?" tip on hover, focus or tap, written in plain words.
- Sample lines (to be refined):
  - Draft pick confirm: "Round 3, Pick 4: Select **Player Name** (F). Confirm?"
  - Pick made: "Locked in. **Player Name** is yours."
  - Pick failed: "That pick didn't go through. **Player Name** was taken a moment ago. Here's who's left."
  - Lost matchup: "Close one. You were 3.2 points back. Rematch next week."
  - Error: "Something went wrong on our end. Your picks are safe. Try again."

## Feel
First three seconds: **welcoming, calm excitement, a sense of belonging.** Web app: calm, informative and fun. Social: louder and brighter, featuring video with Megan's face and voice.

## Look
- **Likes:** colour that isn't afraid but stays sleek and cohesive (PWHL); simple, instantly recognisable marks (Nike, Barstool); bright enough to feel excited, not oversaturated; bright blues.
- **Dislikes:** too much purple; too much black and grey in marketing; bro energy and failure-focused content.
- **Avoid:** colour schemes that match any PWHL or WNBA team.
- **Signature moves:** (1) waves, with pages that load with a fill or wave motion, and (2) one recognisable colour, our equivalent of PWHL's purple.
- **Theme:** designed for both, default dark.
- **Motion:** the wave fill must respect `prefers-reduced-motion` and stay under about 1 second so it never delays content.

## Draft design tokens (proposal, to be checked on screen)
Dark is the default; light mirrors it.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#0A1628` | `#F5F9FD` | page background (deep navy, not black or grey) |
| `--surface` | `#132540` | `#FFFFFF` | cards, panels |
| `--text` | `#EAF3FC` | `#0A1628` | body text |
| `--text-muted` | `#9DB4CC` | `#4A6078` | secondary text |
| `--primary` (the signature colour) | `#2F9BFF` | `#1769D1` | buttons, links, wave, brand mark |
| `--accent` | `#FF7A59` | `#E85A38` | celebration, highlights, live badges (warm contrast to the blues; no purple) |
| `--positive` / `--negative` | `#2ED3A0` / `#FF6B7A` | darker equivalents | stat deltas |

- **Type:** two families. Headings in a friendly geometric sans with character (candidates: Sora or Outfit). Body and UI in Inter. Tabular numerals for all stats.
- **Shape:** generous radii (12 to 16 px cards), soft wave dividers, no hard black borders.
- **Check before locking:** the primary blue against each PWHL team's palette, so we meet the "no matching team colours" rule. Several teams use blues and teals.

## Cohesion rules
- **Identical everywhere:** logo, 2 to 3 colours (`--primary`, `--accent`, `--bg`), 2 typefaces, voice, and the wave.
- **May flex:** social is louder and brighter and uses video; the web app is calmer and denser.

## League marks and trust
- Decision for now: official PWHL photos and logos are used, there's no intention to make money, and if that ever changes we talk to the PWHL first. Every page carries a clear "unofficial and unaffiliated" marker.
- Trust on the landing page: screenshots and a short demo video of the app, Megan on camera, the existing TikToks, and a plain privacy note next to the signup.
- Keep league marks isolated (one asset folder, one template partial) so they can be removed in one change if requested.
