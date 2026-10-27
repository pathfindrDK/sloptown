"""Backend for this Vibe. Served under /api (the frontend calls /api/...).

Identity, members and AI come from the platform SDK `miira_vibe`; see AGENTS.md.
Data lives in SQLite at /data/app.sqlite via `db.connect()`.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from miira_vibe import BudgetExceeded, User, ai, current_user, db, members, on_members_changed, require_role, setup


def init_db() -> None:
    with db.session() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS hilsner (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 tekst TEXT NOT NULL,
                 forfatter_id TEXT NOT NULL,
                 forfatter TEXT NOT NULL,
                 oprettet TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = setup(FastAPI(title="Min Vibe", lifespan=lifespan))


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


class NyHilsen(BaseModel):
    tekst: str = Field(min_length=1, max_length=500)


@app.get("/hilsner")
def hilsner(user: User = Depends(current_user)) -> list[dict]:
    with db.session() as conn:
        rows = conn.execute("SELECT id, tekst, forfatter, oprettet FROM hilsner ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


@app.post("/hilsner")
def ny_hilsen(body: NyHilsen, user: User = Depends(current_user)) -> dict:
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO hilsner (tekst, forfatter_id, forfatter) VALUES (?, ?, ?)", (body.tekst, user.id, user.name)
        )
        row = conn.execute("SELECT id, tekst, forfatter, oprettet FROM hilsner WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


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
