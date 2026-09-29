"""Query planner: one small model call turns the question into a schema-validated plan.

The planner sees only the question, never documents, so it cannot be injected through
content. Its output is validated against the Plan schema; on failure the fallback is
all four platforms with the raw question. Platform queries are parameterised from the
schema, so there is no free-text path into JQL, CQL or Drive queries.

Without an LLM key a deterministic heuristic planner produces the same shape.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from ..models import PLATFORMS, Plan, SubQuery
from .llm import LLMClient, LLMError, extract_json

log = logging.getLogger(__name__)

PLANNER_SYSTEM = """You turn one employee question into a JSON retrieval plan for an enterprise search over four platforms: confluence (wiki pages, runbooks, decision docs), jira (tickets, issues, bugs), slack (channel threads, discussions), gdrive (documents, postmortems, spreadsheets).

Return only JSON with this shape:
{"platforms": ["confluence","jira","slack","gdrive"],
 "subqueries": [{"platform": "jira", "query": "database migration status", "window_days": null, "container": "DBM"}],
 "intent": "status" | "root_cause" | "summary" | "lookup" | "other",
 "window_days": null}

Rules: include every platform that could plausibly hold the answer (usually all four); one subquery per platform; put a time window only on subqueries the question scopes in time ("last week" = 7, "yesterday" = 2, "last month" = 30, "last quarter" = 90); set container only when the question names a project key, channel, space or drive; keep queries short keyword phrases."""

PLATFORM_WORDS = {
    "slack": ["slack", "channel", "thread", "dm", "message"],
    "jira": ["jira", "ticket", "tickets", "issue", "issues", "bug", "bugs", "epic"],
    "confluence": ["confluence", "runbook", "wiki", "decision doc", "design doc", "page", "space"],
    "gdrive": ["drive", "gdrive", "google doc", "postmortem", "post-mortem", "spreadsheet", "sheet", "file", "folder", "document"],
}

WINDOWS = [
    (re.compile(r"\btoday\b"), 1),
    (re.compile(r"\byesterday\b"), 2),
    (re.compile(r"\b(last|past|this) week\b|\blast 7 days\b"), 7),
    (re.compile(r"\b(last|past|this) (two|2) weeks\b|\blast 14 days\b"), 14),
    (re.compile(r"\b(last|past|this) month\b|\blast 30 days\b"), 30),
    (re.compile(r"\b(last|past|this) quarter\b|\blast 90 days\b"), 90),
]

INTENTS = [
    (re.compile(r"\b(status|progress|where are we|how far)\b"), "status"),
    (re.compile(r"\b(root cause|why|caused|what happened)\b"), "root_cause"),
    (re.compile(r"\b(summari[sz]e|summary|recap|tl;?dr)\b"), "summary"),
]

ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,5})-\d+\b")
CHANNEL_RE = re.compile(r"#([a-z0-9][a-z0-9_\-]+)")
SPACE_RE = re.compile(r"\b([A-Z]{2,8})\s+space\b")


def heuristic_plan(question: str) -> Plan:
    q = question.lower()
    mentioned = [p for p, words in PLATFORM_WORDS.items() if any(re.search(rf"\b{re.escape(w)}s?\b", q) for w in words)]
    window = next((days for pattern, days in WINDOWS if pattern.search(q)), None)
    intent = next((name for pattern, name in INTENTS if pattern.search(q)), "lookup")

    containers: dict[str, str] = {}
    key = ISSUE_KEY_RE.search(question)
    if key:
        containers["jira"] = key.group(1)
    channel = CHANNEL_RE.search(q)
    if channel:
        containers["slack"] = channel.group(1)
    space = SPACE_RE.search(question)
    if space:
        containers["confluence"] = space.group(1)

    # Strip platform names from the keyword query so "in slack" does not become a search term
    # (content words such as "runbook" or "ticket" stay: they are what the user is looking for).
    stripped = q
    for name in ("google drive", "gdrive", "slack", "jira", "confluence", "drive"):
        stripped = re.sub(rf"\b(in|on|from)?\s*{re.escape(name)}\b", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ?.!,") or question

    subqueries = []
    for platform in PLATFORMS:
        subqueries.append(
            SubQuery(
                platform=platform,  # type: ignore[arg-type]
                query=stripped,
                window_days=window if (platform in mentioned or not mentioned) and window else None,
                container=containers.get(platform),
            )
        )
    return Plan(platforms=list(PLATFORMS), subqueries=subqueries, intent=intent, window_days=window)  # type: ignore[arg-type]


def fallback_plan(question: str) -> Plan:
    return Plan(
        platforms=list(PLATFORMS),  # type: ignore[arg-type]
        subqueries=[SubQuery(platform=p, query=question[:500]) for p in PLATFORMS],  # type: ignore[arg-type]
        intent="other",
        fallback=True,
    )


class Planner:
    def __init__(self, llm: LLMClient, model: str):
        self.llm = llm
        self.model = model

    @property
    def mode(self) -> str:
        return "llm" if self.llm.enabled else "heuristic"

    async def plan(self, question: str) -> Plan:
        if not self.llm.enabled:
            return heuristic_plan(question)
        try:
            raw = await self.llm.chat(self.model, PLANNER_SYSTEM, question, temperature=0.0, json_mode=True, max_tokens=400)
            data = extract_json(raw)
            plan = Plan.model_validate(data)
            plan.platforms = list(dict.fromkeys(sq.platform for sq in plan.subqueries)) or plan.platforms
            return plan
        except (LLMError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            log.warning("planner fallback: %s", exc)
            return fallback_plan(question)
