"""Budget notifications: tell the watchers in budget settings when a bucket crosses a level.

Levels, per subject (teacher for build, Vibe for app) and budget window: each
warning threshold (fraction of the hard limit), `nerfed` and `blocked`. Each is
announced once per window; the window floor is the week start or the last
admin reset, so a reset re-arms them. The unique index on budget_notifications
makes "once" hold across egress restarts and concurrent calls.

`check_after_usage` runs in the egress right after a usage event is recorded,
inside the same transaction; it returns the messages, and `send_later` mails
them after the commit (SMTP when configured, otherwise they are only logged).
"""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import budgets
from .config import get_settings
from .models import BudgetNotification, Project, User

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class Notice:
    to: tuple[str, ...]
    subject: str
    body: str


def _levels(settings: dict, status: dict) -> list[str]:
    """Levels reached, lowest first."""
    used, hard = status["usedTokens"], status["hardLimitTokens"]
    out = []
    if hard:
        out += [f"warning:{t:g}" for t in sorted(settings.get("warningThresholds") or []) if t and used >= t * hard]
    if status["nerfLimitTokens"] is not None and used >= status["nerfLimitTokens"]:
        out.append("nerfed")
    if hard is not None and used >= hard:
        out.append("blocked")
    return out


async def _label(db: AsyncSession, subject: str, subject_id: str) -> str:
    if subject == "user":
        u = await db.get(User, subject_id)
        return f"læreren {u.name}" if u and u.name else f"læreren {subject_id}"
    p = await db.get(Project, subject_id)
    return f"Vibe'en “{p.title}”" if p else f"Vibe'en {subject_id}"


def _text(label: str, bucket: str, level: str, status: dict) -> tuple[str, str]:
    what = "byggebudget" if bucket == "build" else "app-budget"
    used, hard = status["usedTokens"], status["hardLimitTokens"]
    if level.startswith("warning:"):
        pct = round(float(level.split(":", 1)[1]) * 100)
        head = f"{label} har brugt {pct}% af sit ugentlige {what}"
    elif level == "nerfed":
        head = f"{label} er sat ned på den lille model ({what})"
    else:
        head = f"{label} er blokeret ({what} brugt op)"
    body = (
        f"{head}.\n\nBrugt denne uge: {used:,} tokens".replace(",", ".")
        + (f" af {hard:,}".replace(",", ".") if hard else "")
        + f".\nBudgettet nulstilles {status['resetsAt']}.\n\n"
        "Du kan nulstille eller give ekstra tokens under System admin → Vibe i Miira.\n"
    )
    return f"[SlopTown] {head}", body


async def check_after_usage(
    db: AsyncSession, *, bucket: str, project_id: str, user_sub: str | None, now: datetime | None = None
) -> list[Notice]:
    """Record newly reached levels for the bucket just charged; return the mails to send."""
    now = now or datetime.now(timezone.utc)
    settings = await budgets.get_settings_payload(db)
    watchers = tuple(e for e in settings.get("watcherEmails") or [] if e)
    if not settings["enabled"]:
        return []
    subject, subject_id = ("user", user_sub) if bucket == "build" else ("project", project_id)
    if not subject_id:
        return []
    role = await budgets._user_role(db, user_sub) if bucket == "build" else None
    status = await budgets.bucket_status(db, bucket, subject_id, role, now)
    floor = (await budgets._window(db, subject, subject_id, bucket, now)).floor
    notices: list[Notice] = []
    for level in _levels(settings, status):
        inserted = await db.execute(
            insert(BudgetNotification)
            .values(subject=subject, subject_id=subject_id, bucket=bucket, window_floor=floor,
                    level=level, used_tokens=status["usedTokens"])
            .on_conflict_do_nothing()
            .returning(BudgetNotification.id)
        )
        if inserted.first() is None:
            continue  # already announced in this window
        title, body = _text(await _label(db, subject, subject_id), bucket, level, status)
        log.info("budget notice %s %s %s: %s", subject, subject_id, level, title)
        if watchers:
            notices.append(Notice(watchers, title, body))
    return notices


def _send(notice: Notice) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(notice.to)
    msg["Subject"] = notice.subject
    msg.set_content(notice.body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def _deliver(notices: list[Notice]) -> None:
    for n in notices:
        try:
            await asyncio.to_thread(_send, n)
        except Exception:
            log.exception("could not mail budget notice %r", n.subject)


def send_later(notices: list[Notice]) -> None:
    """Mail after the transaction committed, without delaying the caller."""
    if not notices:
        return
    if not get_settings().smtp_host:
        for n in notices:
            log.warning("SMTP not configured; budget notice not mailed to %s: %s", ", ".join(n.to), n.subject)
        return
    task = asyncio.get_running_loop().create_task(_deliver(notices))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
