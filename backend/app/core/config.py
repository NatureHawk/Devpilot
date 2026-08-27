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
    # Voyage AI. voyage-code-3 is trained on source code, which matters more for
    # retrieval quality here than a general-purpose text model would.
    voyage_api_key: str = Field(default="", alias="VOYAGE_API_KEY")
    embedding_model: str = Field(default="voyage-code-3", alias="EMBEDDING_MODEL")
    # voyage-code-3 can emit 256/512/1024/2048; 1024 is its default and the
    # balance we index at. The value is fixed per index: vectors of different
    # widths are not comparable, so changing it requires a re-index.
    embedding_dimensions: int = Field(default=1024, ge=64, le=4096, alias="EMBEDDING_DIMENSIONS")
    # Voyage accepts up to 128 inputs per request; staying under it leaves room
    # for the payload-size guard to trigger first on large chunks.
    embedding_batch_size: int = Field(default=64, ge=1, le=128, alias="EMBEDDING_BATCH_SIZE")
    embedding_timeout_seconds: float = Field(default=60.0, alias="EMBEDDING_TIMEOUT_SECONDS")
    embedding_api_url: str = Field(
        default="https://api.voyageai.com/v1/embeddings", alias="EMBEDDING_API_URL"
    )

    # ---- Language model --------------------------------------------------
    # Answer generation and code-change investigation. Separate from the
    # embedding provider: they are different models with different failure modes.
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
        return bool(self.anthropic_api_key)

    @property
    def llm_configured(self) -> bool:
        """Whether answer generation is possible in this deployment.

        Checked before any retrieval work so an unconfigured deployment reports
        a configuration state instead of doing work it cannot finish.
        """
        return bool(self.anthropic_api_key)

    @property
    def embeddings_configured(self) -> bool:
        """Whether this deployment can generate embeddings at all.

        Checked before indexing and before search so the API can answer with a
        configuration error rather than a provider exception.
        """
        return bool(self.voyage_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache after mutating the environment."""
    return Settings()
