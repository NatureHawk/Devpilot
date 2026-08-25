from __future__ import annotations

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class User(Base, UUIDPrimaryKey, Timestamps):
    """A DevPilot account, established by signing in with GitHub.

    ``email`` is nullable because GitHub only releases it when the account has a
    public address and the granted scopes allow it; ``github_id`` is the stable
    identity we actually key on, since a user can rename their login.
    """

    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    github_login: Mapped[str | None] = mapped_column(String(100), unique=True)
    # GitHub numeric ids exceed 32-bit range, so BigInteger rather than Integer.
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000))

    # Fernet ciphertext, never the raw token. Nullable so a user row can exist
    # before or after a token is revoked.
    github_token_encrypted: Mapped[str | None] = mapped_column(Text)
    github_token_scopes: Mapped[str | None] = mapped_column(String(500))

    @property
    def has_github_token(self) -> bool:
        return self.github_token_encrypted is not None

    def __repr__(self) -> str:
        return f"<User {self.github_login or self.email or self.id}>"
