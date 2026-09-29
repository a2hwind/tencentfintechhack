"""Confluence adapter.

Native rule: space permission AND page restriction, restrictions inherited down the
page tree. A restriction only ever narrows: a person named in it still needs to see the space.

Projection:
  open page                        confluence:space:<KEY>
  one restriction on the chain     confluence:space:<KEY>:user:<id> / confluence:space:<KEY>:group:<name>
                                   (compound tokens: the grantee AND the space, so naming someone
                                   who cannot see the space grants nothing, and a group's
                                   membership changes still need no re-index)
  several restrictions             confluence:user:<id> for each person who passes them all
A user holds confluence:space:<KEY> for each space they can see, plus the compound tokens for
themselves and each of their groups in those spaces.
"""

from __future__ import annotations

from ..models import Acl, Item, ItemRef, ReadCheck
from .base import EPOCH, AdapterError, HttpAdapter


class ConfluenceAdapter(HttpAdapter):
    platform = "confluence"

    async def changes_since(self, cursor: str | None) -> tuple[list[ItemRef], str]:
        since = cursor or EPOCH
        data = await self._get_json("/rest/api/content/search", params={"cql": f'type=page and lastmodified >= "{since}"', "limit": 500})
        refs: list[ItemRef] = []
        newest = since
        for result in data.get("results", []):
            when = result["version"]["when"]
            refs.append(
                ItemRef(
                    item_id=f"confluence:{result['id']}",
                    platform="confluence",
                    version=int(result["version"]["number"]),
                    last_modified=when,
                    deleted=result.get("status") == "trashed",
                )
            )
            newest = max(newest, when)
        return refs, newest

    async def get_item(self, item_id: str) -> Item:
        page_id = self.local_id(item_id)
        data = await self._get_json(f"/rest/api/content/{page_id}", params={"expand": "body.storage,version,space,ancestors,links"})
        body = data.get("body", {}).get("storage", {}).get("value", "")
        return Item(
            item_id=item_id,
            platform="confluence",
            title=data["title"],
            text=f"{data['title']}\n\n{body}",
            version=int(data["version"]["number"]),
            last_modified=data["version"]["when"],
            author=data["version"].get("by", {}).get("accountId"),
            url=f"{self.web_base_url}{data.get('_links', {}).get('webui', '')}" if self.web_base_url else None,
            container=f"confluence:space:{data['space']['key']}",
            container_label=data["space"]["key"],
            links=list(data.get("x_links", [])),
        )

    async def get_acl(self, item_id: str) -> Acl:
        page_id = self.local_id(item_id)
        data = await self._get_json(f"/rest/api/content/{page_id}/restriction")
        restrictions = data.get("restrictions", [])
        space = data["space"]["key"]
        if not restrictions:
            tokens = [f"confluence:space:{space}"]
        elif len(restrictions) == 1:
            read = restrictions[0]["read"]
            tokens = [f"confluence:space:{space}:user:{u['accountId']}" for u in read.get("users", [])]
            tokens += [f"confluence:space:{space}:group:{g['name']}" for g in read.get("groups", [])]
        else:
            tokens = [f"confluence:user:{u}" for u in data.get("effective_users", [])]
        return Acl(item_id=item_id, allowed_principals=tokens, version=int(data.get("version", 0)))

    async def principals_for(self, platform_user_id: str) -> list[str]:
        data = await self._get_json(f"/rest/api/user/{platform_user_id}/memberships")
        groups = [g["name"] for g in data.get("groups", [])]
        tokens = [f"confluence:user:{platform_user_id}"]
        for space in (s["key"] for s in data.get("spaces", [])):
            tokens.append(f"confluence:space:{space}")
            tokens.append(f"confluence:space:{space}:user:{platform_user_id}")
            tokens += [f"confluence:space:{space}:group:{g}" for g in groups]
        return tokens

    async def can_read(self, platform_user_id: str, item_id: str) -> ReadCheck:
        page_id = self.local_id(item_id)
        response = await self._get(f"/rest/api/content/{page_id}", params={"expand": "version"}, act_as=platform_user_id)
        if response.status_code == 200:
            data = response.json()
            return ReadCheck(allowed=True, reason="ok", version=int(data["version"]["number"]), last_modified=data["version"]["when"])
        if response.status_code == 403:
            return ReadCheck(allowed=False, reason="not_member")
        if response.status_code == 404:
            return ReadCheck(allowed=False, reason="deleted")
        raise AdapterError(f"confluence: can_read {item_id} -> {response.status_code}")
