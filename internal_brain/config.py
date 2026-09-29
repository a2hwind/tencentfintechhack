"""Runtime settings, read from environment variables with working defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_map(name: str) -> dict[str, str]:
    """A JSON object, or 'a=b,c=d' (SLACK_USER_MAP=jdoe=U07ABC,mlim=U07DEF)."""
    raw = _env(name).strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        return {str(k): str(v) for k, v in json.loads(raw).items()}
    pairs = (part.split("=", 1) for part in raw.split(",") if "=" in part)
    return {k.strip(): v.strip() for k, v in pairs if k.strip() and v.strip()}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("BRAIN_DATA_DIR", "data")))
    db_path: Path | None = None
    fixture_path: Path = field(
        default_factory=lambda: Path(_env("BRAIN_FIXTURE", str(Path(__file__).parent / "mocks" / "fixtures" / "company_a.yaml")))
    )

    # LLM (OpenAI-compatible). No key => deterministic stub planner/answerer.
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model_planner: str = field(default_factory=lambda: _env("LLM_MODEL_PLANNER", "hy3"))
    llm_model_answer: str = field(default_factory=lambda: _env("LLM_MODEL_ANSWER", "hy3"))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT_S", 30.0))
    # JSON merged into every chat request; unset = provider default ({"enable_enhancement": false} for Hunyuan)
    llm_extra_body: dict | None = field(default_factory=lambda: json.loads(_env("LLM_EXTRA_BODY")) if _env("LLM_EXTRA_BODY") else None)
    verifier: str = field(default_factory=lambda: _env("VERIFIER", "lexical"))  # off | lexical | llm
    verifier_min_overlap: float = field(default_factory=lambda: _env_float("VERIFIER_MIN_OVERLAP", 0.25))

    embeddings: str = field(default_factory=lambda: _env("EMBEDDINGS", "hash"))  # hash | bge-small | hunyuan | openai
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "hunyuan-embedding"))
    embedding_min_similarity: float | None = field(
        default_factory=lambda: float(_env("EMBEDDING_MIN_SIMILARITY")) if _env("EMBEDDING_MIN_SIMILARITY") else None
    )

    # Slack: "mock" (the Company A fixture) or "real" (a workspace, through a bot token).
    slack_mode: str = field(default_factory=lambda: _env("SLACK_MODE", "mock"))
    slack_bot_token: str = field(default_factory=lambda: _env("SLACK_BOT_TOKEN"))
    slack_signing_secret: str = field(default_factory=lambda: _env("SLACK_SIGNING_SECRET"))  # enables POST /webhooks/slack
    slack_api_base: str = field(default_factory=lambda: _env("SLACK_API_BASE", "https://slack.com/api"))
    slack_user_map: dict[str, str] = field(default_factory=lambda: _env_map("SLACK_USER_MAP"))  # Brain user id -> Slack user id
    slack_history_days: float = field(default_factory=lambda: _env_float("SLACK_HISTORY_DAYS", 90.0))
    slack_lookback_days: float = field(default_factory=lambda: _env_float("SLACK_LOOKBACK_DAYS", 7.0))
    slack_membership_ttl_s: float = field(default_factory=lambda: _env_float("SLACK_MEMBERSHIP_TTL_S", 2.0))

    sync_interval_s: float = field(default_factory=lambda: _env_float("SYNC_INTERVAL_S", 60.0))
    entitlement_ttl_s: float = field(default_factory=lambda: _env_float("ENTITLEMENT_TTL_S", 60.0))
    gate2_timeout_s: float = field(default_factory=lambda: _env_float("GATE2_TIMEOUT_S", 2.0))

    checkpoint_every: int = field(default_factory=lambda: _env_int("CHECKPOINT_EVERY", 5))
    audit_key_dir: Path | None = field(
        default_factory=lambda: Path(_env("AUDIT_KEY_DIR")) if _env("AUDIT_KEY_DIR") else None
    )

    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    )

    # Constant-time floor for empty results: a denied query and a query that matches nothing
    # then take the same wall-clock time, closing the last timing side-channel. 0 disables.
    no_result_min_latency_ms: float = field(default_factory=lambda: _env_float("NO_RESULT_MIN_LATENCY_MS", 25.0))

    # Retrieval knobs
    top_k_per_retriever: int = 20
    fused_candidates: int = 30
    gate2_candidates: int = 12
    expand_top_items: int = 6
    context_budget_chars: int = 28_000  # ~7k tokens

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.db_path is None:
            self.db_path = self.data_dir / "brain.db"
        if self.audit_key_dir is None:
            self.audit_key_dir = self.data_dir

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url)
