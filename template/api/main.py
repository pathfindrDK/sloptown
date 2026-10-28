"""Backend for this Vibe. Served under /api (the frontend calls /api/...).

Identity, members and AI come from the platform SDK `miira_vibe`; see AGENTS.md.
Data lives in SQLite at /data/app.sqlite via `db.session()`.
"""

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from miira_vibe import BudgetExceeded, User, ai, current_user, members, on_members_changed, require_role, setup

app = setup(FastAPI(title="Min Vibe"))


@on_members_changed
async def opdater_medlemmer(version: str) -> None:
    """Keep the local `members` table in step with who has access in Miira."""
    await members.sync()


@app.get("/hej")
async def hej(user: User = Depends(current_user)) -> dict:
    """Who am I."""
    return {"besked": f"Hej {user.name}!", "bruger": user.to_dict()}


@app.get("/klassen")
async def klassen(user: User = Depends(require_role("editor"))) -> dict:
    """Everyone with access, grouped by class. Teachers and co-editors only."""
    grupper: dict[str, list[str]] = {}
    for m in await members.list():
        for klasse in m.classes or ["Uden klasse"]:
            grupper.setdefault(klasse, []).append(m.name)
    return {"klasser": grupper}


class Spoergsmaal(BaseModel):
    spoergsmaal: str = Field(min_length=1, max_length=2000)


@app.post("/spoerg")
async def spoerg(body: Spoergsmaal, user: User = Depends(current_user)) -> dict:
    """An AI call, charged to this app's weekly budget."""
    try:
        svar = await ai.chat(
            [
                {"role": "system", "content": "Du er en venlig hjælper for elever i folkeskolen. Svar kort og på dansk."},
                {"role": "user", "content": body.spoergsmaal},
            ],
            user=user,
        )
    except BudgetExceeded as e:
        raise HTTPException(429, detail=e.message) from e
    return {"svar": svar}
