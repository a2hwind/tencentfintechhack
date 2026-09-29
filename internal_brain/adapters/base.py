"""The adapter interface: the first of the four shared contracts.

    changes_since(cursor)      -> ([ItemRef], new_cursor)   incremental discovery, tombstones included
    get_item(item_id)          -> Item                       full content under the connector credential
    get_acl(item_id)           -> Acl                        effective read ACL projected to principal tokens
    principals_for(user)       -> [token]                    the user's live entitlements on this platform
    can_read(user, item_id)    -> ReadCheck                  the source platform's answer, right now, as that user

Two credentials, two purposes. The sync worker reads content under the connector's
read-scoped app credential so it can index everything with its ACL as data. Every
query-time check (principals_for, can_read) runs under the asker's identity; the
LLM never holds either credential.

A real adapter keeps the same five methods and swaps the base URL, auth headers and
JSON field names. The mocks expose the real APIs' shapes, so the swap is small.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from ..models import Acl, Item, ItemRef, ReadCheck

EPOCH = "1970-01-01T00:00:00Z"


class AdapterError(Exception):
    """Transport or protocol failure. Gate 2 treats it as unverifiable and fails closed."""


class PlatformAdapter(Protocol):
    platform: str

    async def changes_since(self, cursor: str | None) -> tuple[list[ItemRef], str]: ...

    async def get_item(self, item_id: str) -> Item: ...

    async def get_acl(self, item_id: str) -> Acl: ...

    async def principals_for(self, platform_user_id: str) -> list[str]: ...

    async def can_read(self, platform_user_id: str, item_id: str) -> ReadCheck: ...


class HttpAdapter:
    """Shared HTTP plumbing. Subclasses implement the five interface methods."""

    platform: str = ""

    def __init__(self, client: httpx.AsyncClient, web_base_url: str = ""):
        self.client = client
        self.web_base_url = web_base_url.rstrip("/")

    @staticmethod
    def local_id(item_id: str) -> str:
        """'confluence:8812' -> '8812'."""
        platform, _, rest = item_id.partition(":")
        return rest

    async def _get(self, path: str, params: dict[str, Any] | None = None, act_as: str | None = None) -> httpx.Response:
        headers = {"X-Act-As": act_as} if act_as else {}
        try:
            return await self.client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:  # timeouts, connection errors, protocol errors
            raise AdapterError(f"{self.platform}: {exc.__class__.__name__}: {exc}") from exc

    async def _get_json(self, path: str, params: dict[str, Any] | None = None, act_as: str | None = None) -> dict[str, Any]:
        response = await self._get(path, params=params, act_as=act_as)
        if response.status_code >= 400:
            raise AdapterError(f"{self.platform}: GET {path} -> {response.status_code}")
        return response.json()
