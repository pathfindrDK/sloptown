"""Terms of use: the current text and acceptance. Teachers must accept before creating a Vibe."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .audit import audit
from .auth import CurrentPrincipal, Db
from .config import get_settings
from .errors import ApiError
from .models import User

router = APIRouter()

TERMS_HTML = """
<h2>Vilkår for brug af Vibe</h2>
<p>Vibe lader dig bygge små apps til din undervisning med hjælp fra kunstig intelligens.</p>
<ul>
  <li>Du er ansvarlig for indholdet af de apps, du bygger og deler.</li>
  <li>Indtast ikke følsomme personoplysninger om elever i chatten eller i appen.</li>
  <li>Al brug logges. Forbrug af AI er begrænset pr. uge.</li>
  <li>Apps kan ses af dem, du deler dem med i Miira.</li>
</ul>
<hr>
<h2>Terms of use for Vibe</h2>
<p>You are responsible for the apps you build and share. Do not enter sensitive personal data
about students. All use is logged, and AI usage is limited per week.</p>
""".strip()


def terms_accepted(user: User) -> bool:
    return user.terms_version == get_settings().terms_version and user.terms_accepted_at is not None


@router.get("/api/terms")
async def get_terms() -> dict[str, Any]:
    return {"version": get_settings().terms_version, "html": TERMS_HTML}


class AcceptBody(BaseModel):
    version: str


@router.post("/api/terms/accept")
async def accept_terms(body: AcceptBody, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    if body.version != get_settings().terms_version:
        raise ApiError(409, "terms_outdated", "Vilkårene er blevet opdateret. Genindlæs siden.")
    user = principal.user
    user.terms_version = body.version
    user.terms_accepted_at = datetime.now(UTC)
    audit(db, "terms.accept", actor_sub=user.sub, details={"version": body.version}, request=request)
    await db.commit()
    return {"termsAcceptedAt": user.terms_accepted_at.isoformat(), "version": body.version}
