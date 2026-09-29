"""Jira adapter.

Native rule: project permission scheme (browse through a project role) AND issue
security level. Projection: an issue without a level carries one token per browsing
role; an issue with a level carries only that level's token, which a user holds only when
they are in the level AND can browse the project (a level narrows, it never grants).
"""

from __future__ import annotations

import re

from ..models import Acl, Item, ItemRef, ReadCheck
from .base import EPOCH, AdapterError, HttpAdapter


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


class JiraAdapter(HttpAdapter):
    platform = "jira"

    async def changes_since(self, cursor: str | None) -> tuple[list[ItemRef], str]:
        since = cursor or EPOCH
        data = await self._get_json("/rest/api/3/search", params={"jql": f'updated >= "{since}" order by updated asc', "maxResults": 500})
        refs: list[ItemRef] = []
        newest = since
        for issue in data.get("issues", []):
            when = issue["fields"]["updated"]
            refs.append(ItemRef(item_id=f"jira:{issue['key']}", platform="jira", version=int(issue.get("x_version", 1)), last_modified=when))
            newest = max(newest, when)
        for gone in data.get("x_deleted", []):
            refs.append(ItemRef(item_id=f"jira:{gone['key']}", platform="jira", version=0, last_modified=gone["deleted"], deleted=True))
            newest = max(newest, gone["deleted"])
        return refs, newest

    @staticmethod
    def _render(issue: dict) -> str:
        f = issue["fields"]
        assignee = (f.get("assignee") or {}).get("accountId") or "unassigned"
        lines = [
            f"{issue['key']}: {f['summary']}",
            f"Status: {f['status']['name']}. Assignee: {assignee}. Project: {f['project']['key']}.",
            "",
            f.get("description") or "",
        ]
        for c in (f.get("comment") or {}).get("comments", []):
            lines += ["", f"Comment by {c['author']['accountId']} on {c['created'][:10]}: {c['body']}"]
        return "\n".join(lines)

    async def get_item(self, item_id: str) -> Item:
        key = self.local_id(item_id)
        issue = await self._get_json(f"/rest/api/3/issue/{key}")
        f = issue["fields"]
        return Item(
            item_id=item_id,
            platform="jira",
            title=f"{issue['key']}: {f['summary']}",
            text=self._render(issue),
            version=int(issue.get("x_version", 1)),
            last_modified=f["updated"],
            author=(f.get("reporter") or {}).get("accountId"),
            url=f"{self.web_base_url}/browse/{issue['key']}" if self.web_base_url else None,
            container=f"jira:project:{f['project']['key']}",
            container_label=f["project"]["key"],
            links=list(issue.get("x_links", [])),
        )

    async def get_acl(self, item_id: str) -> Acl:
        key = self.local_id(item_id)
        issue = await self._get_json(f"/rest/api/3/issue/{key}")
        f = issue["fields"]
        project_key = f["project"]["key"]
        level = (f.get("security") or {}).get("name")
        if level:
            tokens = [f"jira:project:{project_key}:level:{slug(level)}"]
        else:
            roles = await self._get_json(f"/rest/api/3/project/{project_key}/roles")
            tokens = [f"jira:project:{project_key}:role:{r['name']}" for r in roles.get("roles", []) if r.get("browse", True)]
        return Acl(item_id=item_id, allowed_principals=tokens, version=int(issue.get("x_version", 1)))

    async def principals_for(self, platform_user_id: str) -> list[str]:
        data = await self._get_json(f"/rest/api/3/user/{platform_user_id}/entitlements")
        tokens = [f"jira:user:{platform_user_id}"]
        for project in data.get("projects", []):
            roles = project.get("roles", [])
            tokens += [f"jira:project:{project['key']}:role:{role}" for role in roles]
            if roles:  # a security level narrows who among the project's browsers can see an issue: no browse, no level
                tokens += [f"jira:project:{project['key']}:level:{slug(level)}" for level in project.get("securityLevels", [])]
        return tokens

    async def can_read(self, platform_user_id: str, item_id: str) -> ReadCheck:
        key = self.local_id(item_id)
        response = await self._get(f"/rest/api/3/issue/{key}", params={"fields": "updated"}, act_as=platform_user_id)
        if response.status_code == 200:
            issue = response.json()
            return ReadCheck(allowed=True, reason="ok", version=int(issue.get("x_version", 1)), last_modified=issue["fields"]["updated"])
        if response.status_code == 404:
            # Jira answers 404 for both "missing" and "not permitted"; a second look under the
            # connector credential tells them apart.
            exists = await self._get(f"/rest/api/3/issue/{key}", params={"fields": "updated"})
            if exists.status_code == 404:
                return ReadCheck(allowed=False, reason="deleted")
            if exists.status_code == 200:
                return ReadCheck(allowed=False, reason="not_member")
        raise AdapterError(f"jira: can_read {item_id} -> {response.status_code}")
