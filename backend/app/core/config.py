"""Application configuration, sourced exclusively from the environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]

# Used only when SECRET_KEY is unset. Rejected outside local/test so a
# deployment cannot accidentally sign sessions with a published value.
DEV_SECRET_KEY = "devpilot-insecure-development-key"


class Settings(BaseSettings):
    """Runtime configuration.

    Values come from the process environment (or a local ``.env`` during
    development). Secrets are never logged or returned by the API; only their
    presence is ever exposed, via ``/api/v1/meta/integrations``.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Field(default="local", alias="DEVPILOT_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://devpilot:devpilot@localhost:5432/devpilot",
        alias="DATABASE_URL",
    )

    # NoDecode stops pydantic-settings from JSON-parsing this before the
    # validator below runs; without it a plain "a,b" in .env raises at startup.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:3000"], alias="CORS_ORIGINS"
    )

    # Where to send the browser once the GitHub OAuth callback completes.
    frontend_url: str = Field(default="http://localhost:3000", alias="NEXT_PUBLIC_APP_URL")

    # Signs session cookies and OAuth state, and derives the key that encrypts
    # stored GitHub tokens. Rotating it invalidates sessions and stored tokens.
    secret_key: str = Field(default=DEV_SECRET_KEY, alias="SECRET_KEY")

    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Overridable so tests can point the client at a local stand-in.
    github_api_url: str = Field(default="https://api.github.com", alias="GITHUB_API_URL")
    github_timeout_seconds: float = Field(default=20.0, alias="GITHUB_TIMEOUT_SECONDS")
    # Concurrent blob downloads. GitHub tolerates modest parallelism; this is
    # deliberately far below any rate limit.
    github_max_concurrency: int = Field(default=8, ge=1, le=32, alias="GITHUB_MAX_CONCURRENCY")

    # ---- Embeddings ------------------------------------------------------
    # Two hosted providers sit behind the one EmbeddingProvider interface:
    # "voyage" (Voyage AI, voyage-code-3) and "gemini" (Google Gemini API,
    # gemini-embedding-001). Nothing above app.services.embeddings.get_provider
    # knows which is active. Switching this requires re-indexing every
    # repository: vectors from different models are not comparable, and the
    # per-row `model`/`provider` metadata makes mixing them impossible rather
    # than merely unlikely.
    embedding_provider: Literal["voyage", "gemini"] = Field(
        default="voyage", alias="EMBEDDING_PROVIDER"
    )

    # The stored pgvector column width. Both providers are asked to emit exactly
    # this many dimensions, so it stays consistent across provider, model,
    # database column, ANN index, retrieval query and validation. pgvector's
    # HNSW index is capped at 2000 dimensions, which is the real ceiling here —
    # not the 4096 the field once allowed. Changing it is an Alembic migration
    # (the column type) plus a re-index, never a config-only change.
    embedding_dimensions: int = Field(default=1024, ge=64, le=2000, alias="EMBEDDING_DIMENSIONS")
    embedding_timeout_seconds: float = Field(default=60.0, alias="EMBEDDING_TIMEOUT_SECONDS")
    # Wall-clock ceiling on retrying one batch against a rate limit. A free-tier
    # key's window can be on the order of a minute, so this needs real headroom —
    # but it is still a ceiling: indexing must eventually fail loudly rather than
    # retry forever. Shared by both providers.
    embedding_max_retry_seconds: float = Field(
        default=120.0, ge=1.0, alias="EMBEDDING_MAX_RETRY_SECONDS"
    )

    # ---- Voyage AI ------------------------------------------------------
    # voyage-code-3 is trained on source code, which matters more for retrieval
    # quality here than a general-purpose text model would. It can emit
    # 256/512/1024/2048; keep EMBEDDING_DIMENSIONS to one of those when this
    # provider is active.
    voyage_api_key: str = Field(default="", alias="VOYAGE_API_KEY")
    embedding_model: str = Field(default="voyage-code-3", alias="EMBEDDING_MODEL")
    # Voyage accepts up to 128 inputs per request; staying under it leaves room
    # for the payload-size guard to trigger first on large chunks.
    embedding_batch_size: int = Field(default=64, ge=1, le=128, alias="EMBEDDING_BATCH_SIZE")
    embedding_api_url: str = Field(
        default="https://api.voyageai.com/v1/embeddings", alias="EMBEDDING_API_URL"
    )

    # ---- Google Gemini embeddings ------------------------------------------
    # gemini-embedding-001 over the Gemini API (generativelanguage.googleapis.com).
    # Talked to as plain HTTPS, like every other integration here — no vendor SDK.
    # The model's native default width is 3072; it is a Matryoshka (MRL) model,
    # so `outputDimensionality` returns a genuine lower-width embedding rather
    # than a truncation. We request EMBEDDING_DIMENSIONS (1024 by default, an
    # officially supported flexible value) so the existing vector(1024) schema,
    # HNSW index and retrieval query are untouched. The Google-recommended MRL
    # widths are 768 / 1536 / 3072; 1536 needs the optional dimension migration,
    # 3072 cannot be HNSW-indexed by pgvector at all.
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001", alias="GEMINI_EMBEDDING_MODEL"
    )
    # Base URL; the client appends `/models/<model>:embedContent` and
    # `:batchEmbedContents`.
    gemini_api_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta", alias="GEMINI_API_URL"
    )
    # `batchEmbedContents` (the synchronous batch call, not the async Batch API)
    # has no documented hard cap; 100 is a conservative, well-tested size.
    gemini_embedding_batch_size: int = Field(
        default=100, ge=1, le=250, alias="GEMINI_EMBEDDING_BATCH_SIZE"
    )

    # ---- Language model --------------------------------------------------
    # Answer generation and code-change investigation. Separate from the
    # embedding provider: they are different models with different failure modes.
    #
    # Which vendor is behind `LLMProvider`:
    #   "anthropic"  — pay-per-token, claude-opus-5.
    #   "openrouter" — one OpenAI-compatible endpoint in front of many models.
    #   "gemini"     — Google Gemini API (generativelanguage.googleapis.com),
    #                  the primary free development stack alongside Gemini
    #                  embeddings + pgvector.
    #   "groq"       — Groq's OpenAI-compatible API, openai/gpt-oss-120b. A fast
    #                  free tier evaluated for tool calling and structured
    #                  output; Gemini embeddings + pgvector are unchanged.
    # Switching this touches no caller: every side of get_provider() implements
    # the same Protocol, and nothing above the provider layer knows which is in
    # use.
    llm_provider: Literal["anthropic", "openrouter", "gemini", "groq"] = Field(
        default="anthropic", alias="LLM_PROVIDER"
    )
    llm_model: str = Field(default="claude-opus-5", alias="LLM_MODEL")
    # Streaming is always used, so this can be generous without risking an HTTP
    # timeout; it bounds a runaway answer rather than shaping a normal one.
    llm_max_output_tokens: int = Field(
        default=8_000, ge=256, le=64_000, alias="LLM_MAX_OUTPUT_TOKENS"
    )
    llm_timeout_seconds: float = Field(default=120.0, alias="LLM_TIMEOUT_SECONDS")
    # Thinking depth. "high" is the default for intelligence-sensitive work;
    # investigation runs benefit from more, plain Q&A rarely needs it.
    llm_effort: str = Field(default="high", alias="LLM_EFFORT")

    # OpenRouter: one OpenAI-compatible endpoint in front of many models, so the
    # model actually used is a config value rather than a vendor SDK choice.
    # The free model below is verified (2026-08-28) two ways: its catalogue
    # entry at OpenRouter's own /api/v1/models lists tools/streaming/a 1M-token
    # context, and a real request against it — plain completion, streaming,
    # and an actual tool call — was run and returned correctly. (A different
    # free model, z-ai/glm-5.2:free, checked out identically on paper but was
    # returning 429s from an overloaded shared pool at verification time —
    # this one is the model that was actually confirmed working, not just
    # theoretically capable.)
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="minimax/minimax-m3:free", alias="OPENROUTER_MODEL")
    openrouter_api_url: str = Field(
        default="https://openrouter.ai/api/v1/chat/completions", alias="OPENROUTER_API_URL"
    )

    # Gemini as the generative provider. Conceptually separate from the Gemini
    # *embedding* configuration above — different model, different failure modes
    # — but the key may be shared: GEMINI_LLM_API_KEY falls back to GEMINI_API_KEY
    # when left blank (see `gemini_llm_key`). Model verified against Google's live
    # catalogue 2026-09-01: gemini-2.5-flash is retired for new keys and Google's
    # own 404 points to gemini-3.6-flash — a stable (non-preview) model with a
    # 1,048,576-token context, function calling, native responseSchema structured
    # output and SSE streaming, all confirmed with a real free-tier request.
    gemini_llm_api_key: str = Field(default="", alias="GEMINI_LLM_API_KEY")
    gemini_llm_model: str = Field(default="gemini-3.6-flash", alias="GEMINI_LLM_MODEL")
    # Base URL; the client appends `/models/<model>:generateContent` and
    # `:streamGenerateContent`.
    gemini_llm_api_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta", alias="GEMINI_LLM_API_URL"
    )
    # Wall-clock ceiling on retrying one call against a rate limit or a transient
    # 5xx. Deliberately small: the free tier should be backed off from, not
    # hammered, and a request must fail loudly rather than retry forever.
    gemini_llm_max_retry_seconds: float = Field(
        default=30.0, ge=0.0, alias="GEMINI_LLM_MAX_RETRY_SECONDS"
    )

    # Groq as the generative provider. OpenAI-compatible API
    # (api.groq.com/openai/v1); model default openai/gpt-oss-120b. Verified
    # against Groq's live docs 2026-09-01: 131,072-token context, tool use,
    # JSON-Schema structured output (strict), and reasoning controls. DevPilot
    # sends reasoning_format=hidden — it never receives reasoning tokens.
    # Structured Outputs cannot be combined with tools or streaming in one
    # request; the provider raises a typed capability error rather than a 400.
    # Free-tier quotas are externally controlled by Groq and can change.
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    groq_api_url: str = Field(
        default="https://api.groq.com/openai/v1/chat/completions", alias="GROQ_API_URL"
    )
    # Wall-clock ceiling on retrying one call against a rate limit or transient
    # 5xx. Small on purpose: the free tier is backed off from, not hammered, and
    # a call must fail loudly rather than retry forever.
    groq_max_retry_seconds: float = Field(
        default=30.0, ge=0.0, alias="GROQ_MAX_RETRY_SECONDS"
    )

    # ---- Context budget --------------------------------------------------
    # Ceiling on retrieved source sent to the model, in characters. Chosen as a
    # character budget rather than tokens because tokenising every chunk to make
    # a packing decision costs more than the headroom it would buy.
    context_max_chars: int = Field(default=60_000, ge=2_000, alias="CONTEXT_MAX_CHARS")
    # Recent turns replayed for follow-up questions. Bounded so a long thread
    # cannot crowd out the repository evidence, which is the point of the answer.
    conversation_history_turns: int = Field(
        default=6, ge=0, le=40, alias="CONVERSATION_HISTORY_TURNS"
    )

    # ---- Investigation agent ---------------------------------------------
    # Hard ceiling on tool calls in one change request. The loop is bounded, not
    # open-ended: it investigates and proposes, it does not roam.
    agent_max_tool_calls: int = Field(default=8, ge=1, le=25, alias="AGENT_MAX_TOOL_CALLS")
    agent_max_steps: int = Field(default=10, ge=1, le=30, alias="AGENT_MAX_STEPS")
    # Total source a single investigation may pull in through tools.
    agent_max_tool_output_chars: int = Field(
        default=120_000, ge=5_000, alias="AGENT_MAX_TOOL_OUTPUT_CHARS"
    )

    # ---- Retrieval -------------------------------------------------------
    search_default_top_k: int = Field(default=8, ge=1, le=100, alias="SEARCH_DEFAULT_TOP_K")
    # Hard ceiling so a client cannot ask for thousands of rows of source.
    search_max_top_k: int = Field(default=50, ge=1, le=200, alias="SEARCH_MAX_TOP_K")

    # ---- Indexing limits -------------------------------------------------
    # A file larger than this is recorded as skipped rather than downloaded.
    # 512 KiB comfortably holds real source files; anything larger is usually
    # generated, vendored or data.
    index_max_file_bytes: int = Field(default=512_000, ge=1_000, alias="INDEX_MAX_FILE_BYTES")
    # Ceiling on one repository's indexed source, so a large repository cannot
    # exhaust memory or disk.
    index_max_total_bytes: int = Field(
        default=50_000_000, ge=100_000, alias="INDEX_MAX_TOTAL_BYTES"
    )
    index_max_files: int = Field(default=5_000, ge=1, alias="INDEX_MAX_FILES")
    # Chunks longer than this are split; large enough to hold most functions.
    index_max_chunk_chars: int = Field(default=8_000, ge=500, alias="INDEX_MAX_CHUNK_CHARS")
    # Line window used when a file has no parseable structure, and when a single
    # symbol exceeds the character limit.
    index_fallback_chunk_lines: int = Field(default=120, ge=10, alias="INDEX_FALLBACK_CHUNK_LINES")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b`` as well as a real list, so .env stays readable."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _require_real_secret_outside_development(self) -> Settings:
        if not self.is_local and self.secret_key == DEV_SECRET_KEY:
            raise ValueError("SECRET_KEY must be set to a unique value outside local development.")
        return self

    @property
    def is_local(self) -> bool:
        return self.environment in ("local", "test")

    @property
    def github_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @property
    def ai_provider_configured(self) -> bool:
        return self.llm_configured

    @property
    def gemini_llm_key(self) -> str:
        """The key the Gemini generative provider uses.

        Falls back to the Gemini *embedding* key: one Google API key normally
        covers both surfaces, so a deployment that already set GEMINI_API_KEY
        does not have to repeat it. The two settings stay conceptually separate
        — either can be pointed at a different key — but the common case is one.
        """
        return self.gemini_llm_api_key or self.gemini_api_key

    @property
    def llm_configured(self) -> bool:
        """Whether answer generation is possible in this deployment.

        Checked before any retrieval work so an unconfigured deployment reports
        a configuration state instead of doing work it cannot finish. Depends
        on which provider is selected — the others' keys being present (or
        absent) is irrelevant.
        """
        if self.llm_provider == "gemini":
            return bool(self.gemini_llm_key)
        if self.llm_provider == "openrouter":
            return bool(self.openrouter_api_key)
        if self.llm_provider == "groq":
            return bool(self.groq_api_key)
        return bool(self.anthropic_api_key)

    @property
    def embeddings_configured(self) -> bool:
        """Whether this deployment can generate embeddings at all.

        Checked before indexing and before search so the API can answer with a
        configuration error rather than a provider exception. Depends on which
        provider is selected — the other one's key is irrelevant.
        """
        if self.embedding_provider == "gemini":
            return bool(self.gemini_api_key)
        return bool(self.voyage_api_key)

    @property
    def active_embedding_model(self) -> str:
        """The model name of the configured embedding provider.

        Used as the fallback when a repository row has no recorded model yet;
        a repository that has been indexed always carries its own.
        """
        if self.embedding_provider == "gemini":
            return self.gemini_embedding_model
        return self.embedding_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache after mutating the environment."""
    return Settings()
