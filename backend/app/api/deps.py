"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import session_scope

DbSession = Annotated[Session, Depends(session_scope)]
AppSettings = Annotated[Settings, Depends(get_settings)]
