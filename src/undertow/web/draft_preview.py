"""Demo draft room (/preview/draft). The page keeps the pick list in the browser; every decision
(whose turn it is, the computer teams' picks, recommendations) comes from `services.draft`, so
there is one implementation of the draft rules. Replaced by the real, database-backed draft
room in Phase 5."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from undertow.services import draft
from undertow.web import preview_data as sample

router = APIRouter()
CFG = draft.DraftConfig()
POOL = [
    draft.DraftPlayer(p["id"], p["name"], p["pos"], p["team"], p["fpg"], p["gp"], p["total"])
    for p in sample.DRAFT["pool"]
]
VALS = draft.valuations(POOL, CFG)
BY_ID = {p.id: p for p in POOL}
assert len(sample.DRAFT["order"]) == CFG.teams
ME = sample.DRAFT["me"]
SLOT_KEYS = ("F", "D", "UTIL", "G", "BN")


class StepIn(BaseModel):
    picks: list[int] = Field(default_factory=list, max_length=CFG.total_picks)
    seed: int = Field(default=1, ge=0, le=1_000_000)


def page_data(clock: int, pace: int) -> dict:
    ranked = sorted(POOL, key=lambda p: VALS[p.id].rank)
    return {
        "teams": [
            {"name": t["name"], "owner": t["owner"], "mine": i == ME}
            for i, t in enumerate(sample.DRAFT["order"])
        ],
        "me": ME,
        "rounds": CFG.rounds,
        "clock": clock,
        "pace": pace,
        "pool": [
            {
                "id": p.id,
                "name": p.name,
                "pos": p.pos,
                "team": p.team,
                "fpg": round(p.fpg, 1),
                "gp": p.gp,
                "rank": VALS[p.id].rank,
                "pos_rank": VALS[p.id].pos_rank,
            }
            for p in ranked
        ],
    }


@router.get("/preview/draft", response_class=HTMLResponse)
def draft_page(request: Request, clock: int = 90, pace: int = 650) -> Response:
    from undertow.web.app import templates  # the app's shared Jinja environment

    clock = min(max(clock, 5), 300)  # seconds on your own clock
    pace = min(max(pace, 0), 3000)  # milliseconds between the computer teams' picks
    context = {"data": page_data(clock, pace), "n_teams": CFG.teams, "rounds": CFG.rounds}
    return templates.TemplateResponse(request, "draft.html", context)


@router.post("/preview/draft/step")
def draft_step(body: StepIn) -> dict:
    try:
        state = draft.replay(body.picks, POOL, CFG)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    recs = draft.recommend(state, ME, VALS, CFG, n=3)
    roster = draft.assign_slots(state.rosters[ME], CFG)
    out = {
        "pick_no": state.pick_no,
        "round": state.round,
        "team": state.team,
        "on_clock": state.team,
        "complete": state.complete,
        "cpu_pick": None,
        "recommend": [{"id": r.player.id, "tag": r.tag, "reason": r.reason} for r in recs],
        "needs": {
            **draft.needs(state.rosters[ME], CFG),
            "picks_left": CFG.rounds - len(state.rosters[ME]),
        },
        "roster": {k: [p.id for p in roster[k]] for k in SLOT_KEYS},
    }
    if not state.complete and state.team != ME:
        out["cpu_pick"] = draft.cpu_pick(state, state.team, VALS, CFG, seed=body.seed).id
    return out
