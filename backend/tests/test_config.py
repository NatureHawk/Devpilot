"""Settings must load from real environment values, not just init kwargs.

The distinction matters: pydantic-settings decodes list fields differently when
the value arrives from the environment, which is where a plain ``a,b`` string
once broke startup.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_cors_origins_accepts_a_comma_separated_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,https://devpilot.example")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:3000", "https://devpilot.example"]


def test_cors_origins_accepts_a_single_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:3000"]


def test_cors_origins_ignores_blank_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, ,")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:3000"]


def test_settings_load_from_the_shipped_env_example(tmp_path, monkeypatch) -> None:
    """.env.example must actually parse — it is what every developer copies."""
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    example = (tmp_path / ".env").resolve()
    example.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+psycopg://devpilot:devpilot@localhost:5432/devpilot",
                "DEVPILOT_ENV=local",
                "CORS_ORIGINS=http://localhost:3000",
                "GITHUB_CLIENT_ID=",
                "ANTHROPIC_API_KEY=",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=example)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:3000"]
    assert settings.github_configured is False
    assert settings.ai_provider_configured is False
