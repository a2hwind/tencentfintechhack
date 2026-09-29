"""Google Drive adapter.

Native rule: a file's readers are its direct grantees plus grantees inherited from the
folder chain and the shared drive. Projection: user, group and domain grantees become
tokens; "anyone with the link" is not treated as a grant (conservative, stated trade-off).
"""

from __future__ import annotations

from ..models import Acl, Item, ItemRef, ReadCheck
from .base import AdapterError, HttpAdapter


class GoogleDriveAdapter(HttpAdapter):
    platform = "gdrive"

    async def changes_since(self, cursor: str | None) -> tuple[list[ItemRef], str]:
        token = cursor or "0"
        data = await self._get_json("/drive/v3/changes", params={"pageToken": token})
        refs: list[ItemRef] = []
        for change in data.get("changes", []):
            if change.get("removed"):
                refs.append(ItemRef(item_id=f"gdrive:{change['fileId']}", platform="gdrive", version=0, last_modified=change["time"], deleted=True))
            else:
                f = change["file"]
                refs.append(ItemRef(item_id=f"gdrive:{f['id']}", platform="gdrive", version=int(f.get("version", 1)), last_modified=f["modifiedTime"]))
        return refs, data.get("newStartPageToken", token)

    async def get_item(self, item_id: str) -> Item:
        file_id = self.local_id(item_id)
        meta = await self._get_json(f"/drive/v3/files/{file_id}", params={"fields": "id,name,modifiedTime,version,mimeType,owners,parents,driveId,webViewLink"})
        body = (await self._get_json(f"/drive/v3/files/{file_id}/export", params={"mimeType": "text/plain"})).get("text", "")
        owners = meta.get("owners") or [{}]
        return Item(
            item_id=item_id,
            platform="gdrive",
            title=meta["name"],
            text=f"{meta['name']}\n\n{body}",
            version=int(meta.get("version", 1)),
            last_modified=meta["modifiedTime"],
            author=owners[0].get("emailAddress"),
            url=meta.get("webViewLink"),
            container=f"gdrive:drive:{meta.get('driveId', 'my-drive')}",
            container_label=meta.get("x_drive_name") or meta.get("driveId", "My Drive"),
            links=list(meta.get("x_links", [])),
        )

    async def get_acl(self, item_id: str) -> Acl:
        file_id = self.local_id(item_id)
        data = await self._get_json(f"/drive/v3/files/{file_id}/permissions")
        tokens: list[str] = []
        for perm in data.get("permissions", []):
            kind = perm.get("type")
            if kind == "user":
                tokens.append(f"gdrive:user:{perm['emailAddress']}")
            elif kind == "group":
                group = perm.get("x_group") or perm["emailAddress"].split("@")[0]
                tokens.append(f"gdrive:group:{group}")
            elif kind == "domain":
                tokens.append(f"gdrive:domain:{perm['domain']}")
            # type == "anyone" (link sharing) is deliberately ignored
        return Acl(item_id=item_id, allowed_principals=list(dict.fromkeys(tokens)), version=int(data.get("version", 1)))

    async def principals_for(self, platform_user_id: str) -> list[str]:
        data = await self._get_json("/admin/directory/v1/groups", params={"userKey": platform_user_id})
        tokens = [f"gdrive:user:{platform_user_id}"]
        tokens += [f"gdrive:group:{g.get('name') or g['email'].split('@')[0]}" for g in data.get("groups", [])]
        domain = data.get("domain")
        if domain and platform_user_id.endswith("@" + domain):
            tokens.append(f"gdrive:domain:{domain}")
        return tokens

    async def can_read(self, platform_user_id: str, item_id: str) -> ReadCheck:
        file_id = self.local_id(item_id)
        response = await self._get(f"/drive/v3/files/{file_id}", params={"fields": "version,modifiedTime"}, act_as=platform_user_id)
        if response.status_code == 200:
            meta = response.json()
            return ReadCheck(allowed=True, reason="ok", version=int(meta.get("version", 1)), last_modified=meta["modifiedTime"])
        if response.status_code == 403:
            return ReadCheck(allowed=False, reason="not_member")
        if response.status_code == 404:
            return ReadCheck(allowed=False, reason="deleted")
        raise AdapterError(f"gdrive: can_read {item_id} -> {response.status_code}")
