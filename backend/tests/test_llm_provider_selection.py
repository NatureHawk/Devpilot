"""Which LLM implementation `get_provider` builds, and when a deployment counts
as configured, both follow LLM_PROVIDER and nothing else.

Adding Groq must not change how the other three are selected — that is the
point of the `LLMProvider` seam, and this suite pins it.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.integrations.anthropic_llm import AnthropicLLMProvider
from app.integrations.gemini_llm import GeminiLLMProvider
from app.integrations.groq_llm import GroqLLMProvider
from app.integrations.openrouter_llm import OpenRouterLLMProvider
from app.services.llm import LLMNotConfiguredError, get_provider

_KEYS = (
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_LLM_API_KEY",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "LLM_MODEL",
)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestConfiguredFlag:
    def test_groq_selected_needs_only_the_groq_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = _settings(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="gsk-x")
        assert s.llm_configured is True
        assert s.ai_provider_configured is True

        # Another provider's key does not make a Groq deployment configured.
        s = _settings(monkeypatch, LLM_PROVIDER="groq", ANTHROPIC_API_KEY="sk-x")
        assert s.llm_configured is False

    @pytest.mark.parametrize(
        ("provider", "key_env"),
        [
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
            ("gemini", "GEMINI_LLM_API_KEY"),
            ("groq", "GROQ_API_KEY"),
        ],
    )
    def test_each_provider_counts_configured_with_its_own_key(
        self, monkeypatch: pytest.MonkeyPatch, provider: str, key_env: str
    ) -> None:
        s = _settings(monkeypatch, LLM_PROVIDER=provider, **{key_env: "a-key"})
        assert s.llm_configured is True


class TestProviderConstruction:
    def test_groq_builds_a_groq_provider_with_the_configured_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _settings(
            monkeypatch,
            LLM_PROVIDER="groq",
            GROQ_API_KEY="gsk-x",
            GROQ_MODEL="openai/gpt-oss-120b",
        )
        provider = get_provider(s)
        assert isinstance(provider, GroqLLMProvider)
        assert provider.model == "openai/gpt-oss-120b"
        provider.close()

    @pytest.mark.parametrize(
        ("provider_name", "key_env", "cls"),
        [
            ("anthropic", "ANTHROPIC_API_KEY", AnthropicLLMProvider),
            ("openrouter", "OPENROUTER_API_KEY", OpenRouterLLMProvider),
            ("gemini", "GEMINI_LLM_API_KEY", GeminiLLMProvider),
            ("groq", "GROQ_API_KEY", GroqLLMProvider),
        ],
    )
    def test_llm_provider_selects_the_matching_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        provider_name: str,
        key_env: str,
        cls: type,
    ) -> None:
        s = _settings(monkeypatch, LLM_PROVIDER=provider_name, **{key_env: "a-key"})
        provider = get_provider(s)
        assert isinstance(provider, cls)
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    def test_an_unconfigured_deployment_raises_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _settings(monkeypatch, LLM_PROVIDER="groq")
        with pytest.raises(LLMNotConfiguredError):
            get_provider(s)
