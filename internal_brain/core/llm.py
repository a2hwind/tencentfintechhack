"""OpenAI-compatible chat client: Tencent TokenHub (Hy models), Hunyuan, or any provider.

Untrusted compute by design: the model receives text from the control plane and returns text
to it. It holds no credentials and no tools. Without an API key the planner and answerer fall
back to deterministic stubs so the demo runs offline.

Provider quirks handled here, so the rest of the system never sees them:
  - JSON mode: `response_format` is sent when asked for; a provider that rejects it gets one
    retry without it, and JSON mode is then switched off for the session.
  - Reasoning models: `<think>...</think>` blocks in the content are removed.
  - Extra body fields: LLM_EXTRA_BODY (JSON) is merged into every request. For Hunyuan's
    endpoint the default is {"enable_enhancement": false}, so the model never adds web search
    results to an answer that must be grounded in company documents only.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class LLMError(Exception):
    pass


def default_extra_body(base_url: str) -> dict[str, Any]:
    if "hunyuan" in (base_url or "").lower():
        return {"enable_enhancement": False}
    return {}


class LLMClient:
    def __init__(self, base_url: str = "", api_key: str = "", timeout_s: float = 30.0, extra_body: dict[str, Any] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.extra_body = default_extra_body(base_url) if extra_body is None else dict(extra_body)
        self.json_mode_supported: bool | None = None  # learned on first use
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        try:
            return await self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{exc.__class__.__name__}: {exc}") from exc

    async def chat(self, model: str, system: str, user: str, temperature: float = 0.0, json_mode: bool = False, max_tokens: int = 900) -> str:
        if not self.enabled:
            raise LLMError("LLM not configured")
        payload: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **self.extra_body,
        }
        use_json = json_mode and self.json_mode_supported is not False
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        response = await self._post(payload)
        if response.status_code == 400 and use_json:
            # Some OpenAI-compatible providers reject response_format: retry once without it.
            payload.pop("response_format", None)
            response = await self._post(payload)
            if response.status_code < 400:
                self.json_mode_supported = False
        elif use_json and response.status_code < 400:
            self.json_mode_supported = True
        if response.status_code >= 400:
            raise LLMError(f"{response.status_code}: {response.text[:300]}")
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from exc
        return THINK_RE.sub("", content).strip()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may wrap it in prose or a ``` fence."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
