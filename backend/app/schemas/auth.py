from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    """The signed-in account. Deliberately excludes anything token-related."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_login: str | None
    display_name: str | None
    avatar_url: str | None
    has_github_token: bool


class AuthorizeUrlResponse(BaseModel):
    authorize_url: str


class CallbackRequest(BaseModel):
    code: str
    state: str


class SessionResponse(BaseModel):
    """Issued to the Next.js server, which stores it as an HttpOnly cookie."""

    session_token: str
    redirect_path: str
    user: UserRead
