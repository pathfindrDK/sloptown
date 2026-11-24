"""miira_vibe — SlopTown platform SDK for Vibe app backends.

    from fastapi import Depends, FastAPI
    from miira_vibe import User, ai, current_user, members, require_role, search, setup

    app = setup(FastAPI())

See sdk/AGENTS.md for the full reference and versioning rules.
"""

from . import ai, db, members, search
from .ai import BudgetExceeded
from .identity import ROLE_RANK, Role, User, current_user, optional_user, require_role
from .members import Member, on_members_changed
from .router import setup
from .search import SearchError, SearchResult

__version__ = "1.1.0"

__all__ = [
    "ROLE_RANK",
    "BudgetExceeded",
    "Member",
    "Role",
    "SearchError",
    "SearchResult",
    "User",
    "ai",
    "current_user",
    "db",
    "members",
    "on_members_changed",
    "optional_user",
    "require_role",
    "search",
    "setup",
]
