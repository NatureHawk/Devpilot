from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class User(Base, UUIDPrimaryKey, Timestamps):
    """A DevPilot account.

    Email is the stable identity; ``github_login`` is filled in once the GitHub
    integration exists and is nullable until then. No credential columns yet —
    authentication is a later milestone and adding password/token fields now
    would mean storing secrets we do not use.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    github_login: Mapped[str | None] = mapped_column(String(100), unique=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000))

    def __repr__(self) -> str:
        return f"<User {self.email}>"
