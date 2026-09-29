"""Identity directory: who is signed in, and who they are on each platform.

In production this is the IdP (OIDC claims plus a SCIM-synced account map). In the
demo it is the users section of the Company A fixture, and the API trusts an
`X-User-Id` header in place of a session cookie.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..models import Identity


class IdentityDirectory:
    def __init__(self, users: list[dict]):
        self.users: dict[str, Identity] = {}
        self.platform_ids: dict[str, dict[str, str]] = {}
        self.reverse: dict[tuple[str, str], str] = {}
        for u in users:
            identity = Identity(id=u["id"], name=u["name"], email=u["email"], roles=list(u.get("roles", ["employee"])), title=u.get("title", ""))
            self.users[identity.id] = identity
            pids = {platform: str(pid) for platform, pid in (u.get("platform_ids") or {}).items()}
            self.platform_ids[identity.id] = pids
            for platform, pid in pids.items():
                self.reverse[(platform, pid)] = identity.id

    @classmethod
    def from_fixture(cls, path: str | Path) -> "IdentityDirectory":
        with open(path, "r", encoding="utf-8") as fh:
            fixture = yaml.safe_load(fh)
        return cls(fixture["users"])

    def set_platform_ids(self, platform: str, mapping: dict[str, str]) -> None:
        """Replace every user's id on one platform (e.g. SLACK_USER_MAP for a real workspace).
        Users missing from the mapping have no identity on that platform, so no entitlements there."""
        for key in [k for k in self.reverse if k[0] == platform]:
            del self.reverse[key]
        for user_id, pids in self.platform_ids.items():
            pids.pop(platform, None)
            if user_id in mapping:
                pids[platform] = str(mapping[user_id])
                self.reverse[(platform, str(mapping[user_id]))] = user_id

    def get(self, user_id: str) -> Identity | None:
        return self.users.get(user_id)

    def user_for(self, platform: str, platform_user_id: str) -> str | None:
        return self.reverse.get((platform, platform_user_id))

    def platform_ids_for(self, user_id: str) -> dict[str, str]:
        return dict(self.platform_ids.get(user_id, {}))

    def list(self) -> list[Identity]:
        return list(self.users.values())
