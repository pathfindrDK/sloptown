"""miira_vibe — SlopTown platform SDK for Vibe app backends.

    from fastapi import Depends, FastAPI
    from miira_vibe import User, ai, current_user, members, require_role, setup

    app = setup(FastAPI())

See sdk/AGENTS.md for the full reference and versioning rules.
"""

from . import ai, db, members
from .ai import BudgetExceeded
from .identity import ROLE_RANK, Role, User, current_user, optional_user, require_role
from .members import Member, on_members_changed
from .router import setup

__version__ = "1.0.0"

__all__ = [
    "ROLE_RANK",
    "BudgetExceeded",
    "Member",
    "Role",
    "User",
    "ai",
    "current_user",
    "db",
    "members",
    "on_members_changed",
    "optional_user",
    "require_role",
    "setup",
]
