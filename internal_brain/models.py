"""Shared contracts, agreed before any feature code.

Four contracts are load-bearing across the three streams:
  1. the adapter interface (see adapters/base.py) and its return types below,
  2. the chunk record stored in the index,
  3. the audit entry,
  4. the /ask request and response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["confluence", "jira", "slack", "gdrive"]
PLATFORMS: tuple[str, ...] = ("confluence", "jira", "slack", "gdrive")

# The uniform no-result message. A denied query must be indistinguishable from an empty one.
NO_RESULT_MESSAGE = (
    "I couldn't find anything you have access to on that. "
    "Try rephrasing, or ask the owner of the relevant space or channel."
)
NO_ANSWER_TOKEN = "NO_ANSWER"


# ---------------------------------------------------------------------------
# Adapter return types
# ---------------------------------------------------------------------------
class ItemRef(BaseModel):
    """One changed item, as returned by changes_since(cursor)."""

    item_id: str  # platform-prefixed: "confluence:8812", "jira:PAY-231", "slack:C0DBM:1727.0001", "gdrive:1abc"
    platform: Platform
    version: int
    last_modified: str  # ISO-8601 UTC
    deleted: bool = False


class Item(BaseModel):
    """Full item content, as returned by get_item(item_id)."""

    item_id: str
    platform: Platform
    title: str
    text: str
    version: int
    last_modified: str
    author: str | None = None
    url: str | None = None
    container: str | None = None  # confluence:space:PAYGW | jira:project:PAY | slack:channel:C0DBM | gdrive:drive:ENGDRIVE
    container_label: str | None = None  # human label for planner hints: PAYGW | PAY | #db-migration | Engineering
    links: list[str] = Field(default_factory=list)  # explicit cross-references, as item ids
    deleted: bool = False


class Acl(BaseModel):
    """Effective read ACL of an item, projected to principal tokens (platform:scope:id[:role])."""

    item_id: str
    allowed_principals: list[str]
    version: int


class ReadCheck(BaseModel):
    """Result of can_read(user, item): the live answer from the source platform."""

    allowed: bool
    reason: Literal["ok", "not_member", "deleted", "unverifiable"]
    version: int | None = None
    last_modified: str | None = None


class PrincipalSet(BaseModel):
    """A user's entitlements, resolved live: principal tokens plus per-platform user ids."""

    user_id: str
    tokens: list[str]
    platform_user_ids: dict[str, str]
    resolved_at: str

    def has(self, token: str) -> bool:
        return token in self.tokens


# ---------------------------------------------------------------------------
# Index contract
# ---------------------------------------------------------------------------
class ChunkRecord(BaseModel):
    chunk_id: str  # "<item_id>#<ord>"
    platform: Platform
    item_id: str
    ord: int
    text: str
    allowed_principals: list[str]
    version: int
    last_modified: str
    title: str = ""
    container: str | None = None
    container_label: str | None = None
    url: str | None = None
    author: str | None = None
    links: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Planner output (schema-validated before use)
# ---------------------------------------------------------------------------
class SubQuery(BaseModel):
    platform: Platform
    query: str = Field(min_length=1, max_length=500)
    window_days: int | None = Field(default=None, ge=1, le=3650)
    container: str | None = Field(default=None, max_length=100)  # project key, channel name, space key or drive name


class Plan(BaseModel):
    platforms: list[Platform] = Field(min_length=1, max_length=4)
    subqueries: list[SubQuery] = Field(min_length=1, max_length=8)
    intent: Literal["status", "root_cause", "summary", "lookup", "other"] = "other"
    window_days: int | None = Field(default=None, ge=1, le=3650)
    fallback: bool = False  # true when the planner output failed validation and the raw question was used


# ---------------------------------------------------------------------------
# Decisions, citations, provenance
# ---------------------------------------------------------------------------
DenyRule = Literal["not_member", "revoked", "deleted", "unverifiable"]


class Decision(BaseModel):
    doc: str
    platform: Platform
    gate1: Literal["allow", "deny"]
    gate2: Literal["allow", "deny", "skipped"] = "skipped"
    rule: str  # granting principal token on allow; not_member | revoked | deleted | unverifiable on deny
    gate1_rule: str | None = None  # the token that passed Gate 1, kept when Gate 2 then denies (stale entitlement)
    version: int | None = None
    refreshed: bool = False
    source: Literal["retrieval", "expansion"] = "retrieval"
    container: str | None = None  # confluence:space:PAYGW etc., for the compliance console's container filter


class Citation(BaseModel):
    doc: str
    platform: Platform
    title: str
    url: str | None = None
    updated: str | None = None
    chunks: list[str] = Field(default_factory=list)  # the chunk ids of this document that went into the prompt


class EvidenceQuote(BaseModel):
    """The passage in a cited chunk that best supports one answer sentence (masked text only)."""

    doc: str
    chunk: str
    quote: str


class AnswerSentence(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list)
    evidence: list[EvidenceQuote] = Field(default_factory=list)


class Provenance(BaseModel):
    """Why the asker could see a cited document: the entitlement that granted it, verified live."""

    doc: str
    rule: str
    verified_at: str
    version: int | None = None


# ---------------------------------------------------------------------------
# /ask contract
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    no_result: bool = False
    sentences: list[AnswerSentence] = Field(default_factory=list)  # the answer, sentence by sentence, with evidence


SOURCE_UNAVAILABLE_MESSAGE = "This source isn't available to you right now."


class SourceChunk(BaseModel):
    chunk: str
    text: str


class SourceView(BaseModel):
    """GET /sources/{doc}: a cited document re-checked live at open. Unavailable is one uniform shape."""

    available: bool
    message: str | None = None
    doc: str | None = None
    platform: Platform | None = None
    title: str | None = None
    url: str | None = None
    version: int | None = None
    updated: str | None = None
    rule: str | None = None
    verified_at: str | None = None
    refreshed: bool = False
    chunks: list[SourceChunk] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit entry contract
# ---------------------------------------------------------------------------
class AuditActor(BaseModel):
    id: str
    principals: list[str] = Field(default_factory=list)


class GuardStats(BaseModel):
    citations_rejected: int = 0
    claims_stripped: int = 0
    unsupported_claims: int = 0
    events: list[str] = Field(default_factory=list)  # e.g. "guard:citation_rejected: confluence:9001"
    redactions: dict[str, int] = Field(default_factory=dict)  # DLP masks applied to the model's output, by type


class SentChunk(BaseModel):
    chunk: str
    sha256: str


class AuditEntry(BaseModel):
    """One entry per query, permission event, sync event or audit read.

    entry_hash = SHA-256(canonical_json(all fields except prev_hash/entry_hash) || prev_hash)
    """

    seq: int
    ts: str
    kind: Literal["query", "permission_event", "sync_event", "audit_read", "content_event", "source_open", "alert"]
    actor: AuditActor
    # query entries
    query: str | None = None
    plan: dict | None = None
    decisions: list[Decision] = Field(default_factory=list)
    sent_to_model: list[SentChunk] = Field(default_factory=list)
    answer: str | None = None
    guard: GuardStats | None = None
    outcome: Literal["answered", "no_result"] | None = None
    latency_ms: int | None = None
    model: str | None = None
    timings_ms: dict[str, float] | None = None  # per pipeline stage, measured
    # event / read / alert entries
    event: dict | None = None
    # chain
    prev_hash: str = ""
    entry_hash: str = ""


class Identity(BaseModel):
    """The signed-in user, as the API resolves it from the identity provider (the fixture, in the demo)."""

    id: str
    name: str
    email: str
    roles: list[str] = Field(default_factory=list)
    title: str = ""

    def has_role(self, role: str) -> bool:
        return role in self.roles
