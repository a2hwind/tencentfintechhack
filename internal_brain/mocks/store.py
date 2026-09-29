"""Company A in memory: users, groups and the four platforms with their native permission rules.

The store owns the semantics. The mock apps only expose them over HTTP; the adapters
only project them to principal tokens. Nothing here knows about the index or the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,5}-\d+)\b")
CONFLUENCE_URL_RE = re.compile(r"wiki\.company-a\.com/pages/(\d+)")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def slack_ts(dt: datetime, ordinal: int) -> str:
    return f"{int(dt.timestamp())}.{ordinal:06d}"


# ---------------------------------------------------------------------------
# Events: what a real platform would deliver as a webhook
# ---------------------------------------------------------------------------
@dataclass
class Event:
    kind: str  # membership_changed | content_changed | item_deleted
    platform: str
    payload: dict[str, Any]
    ts: str = field(default_factory=lambda: iso(utc_now()))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
@dataclass
class User:
    id: str
    name: str
    email: str
    title: str
    roles: list[str]
    platform_ids: dict[str, str]
    slack_guest: bool = False


@dataclass
class Grant:
    users: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.users and not self.groups


@dataclass
class Space:
    key: str
    name: str
    read: Grant


@dataclass
class Page:
    id: str
    space: str
    title: str
    author: str
    version: int
    body: str
    last_modified: datetime
    parent: str | None = None
    restrictions: Grant | None = None
    links: list[str] = field(default_factory=list)
    deleted_at: datetime | None = None


@dataclass
class Project:
    key: str
    name: str
    roles: dict[str, list[str]]
    security_levels: dict[str, list[str]]


@dataclass
class Comment:
    author: str
    text: str
    created: datetime


@dataclass
class Issue:
    key: str
    project: str
    summary: str
    status: str
    assignee: str | None
    reporter: str | None
    description: str
    created: datetime
    updated: datetime
    version: int = 1
    security_level: str | None = None
    comments: list[Comment] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    deleted_at: datetime | None = None


@dataclass
class Channel:
    id: str
    name: str
    private: bool
    members: list[str]  # internal user ids
    is_dm: bool = False


@dataclass
class Message:
    ts: str
    user: str  # internal user id
    text: str
    created: datetime


@dataclass
class Thread:
    channel: str
    ts: str  # root ts
    messages: list[Message]
    ref: str | None = None
    links: list[str] = field(default_factory=list)
    deleted_at: datetime | None = None

    @property
    def item_key(self) -> str:
        return f"{self.channel}:{self.ts}"

    @property
    def last_modified(self) -> datetime:
        return max(m.created for m in self.messages)

    @property
    def version(self) -> int:
        return len(self.messages)


@dataclass
class Drive:
    id: str
    name: str
    members: Grant


@dataclass
class Folder:
    id: str
    name: str
    drive: str
    parent: str | None = None
    permissions: Grant = field(default_factory=Grant)


@dataclass
class DriveFile:
    id: str
    name: str
    drive: str
    owner: str
    mime: str
    body: str
    modified: datetime
    folder: str | None = None
    permissions: Grant = field(default_factory=Grant)
    anyone_with_link: bool = False
    version: int = 1
    links: list[str] = field(default_factory=list)
    deleted_at: datetime | None = None


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
class CompanyStore:
    def __init__(self, fixture: dict[str, Any], now: datetime | None = None):
        self.fixture = fixture
        self.now = now or utc_now()
        self.company = fixture["company"]
        self.domain: str = self.company["domain"]
        self.workspace: str = self.company["slack_workspace"]
        self.users: dict[str, User] = {}
        self.groups: dict[str, list[str]] = {k: list(v) for k, v in fixture.get("groups", {}).items()}
        self.spaces: dict[str, Space] = {}
        self.pages: dict[str, Page] = {}
        self.projects: dict[str, Project] = {}
        self.issues: dict[str, Issue] = {}
        self.channels: dict[str, Channel] = {}
        self.threads: dict[str, Thread] = {}  # key: "<channel>:<ts>"
        self.drives: dict[str, Drive] = {}
        self.folders: dict[str, Folder] = {}
        self.files: dict[str, DriveFile] = {}
        self.base_urls: dict[str, str] = {}
        self._subscribers: list[Callable[[Event], None]] = []
        self.events: list[Event] = []
        self._load(fixture)

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_yaml(cls, path: str | Path, now: datetime | None = None) -> "CompanyStore":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh), now=now)

    def _when(self, spec: dict[str, Any], default_days: float = 0) -> datetime:
        days = float(spec.get("days_ago", default_days))
        hours = float(spec.get("hours_ago", 0))
        return self.now - timedelta(days=days, hours=hours)

    @staticmethod
    def _grant(spec: dict[str, Any] | None) -> Grant:
        if not spec:
            return Grant()
        return Grant(users=list(spec.get("users", [])), groups=list(spec.get("groups", [])))

    def _load(self, fx: dict[str, Any]) -> None:
        for u in fx["users"]:
            self.users[u["id"]] = User(
                id=u["id"],
                name=u["name"],
                email=u["email"],
                title=u.get("title", ""),
                roles=list(u.get("roles", ["employee"])),
                platform_ids=dict(u.get("platform_ids", {})),
                slack_guest=bool(u.get("slack_guest", False)),
            )

        cf = fx.get("confluence", {})
        self.base_urls["confluence"] = cf.get("base_url", "https://confluence.example")
        for s in cf.get("spaces", []):
            self.spaces[s["key"]] = Space(key=s["key"], name=s["name"], read=self._grant(s.get("read")))
        for p in cf.get("pages", []):
            self.pages[str(p["id"])] = Page(
                id=str(p["id"]),
                space=p["space"],
                title=p["title"],
                author=p.get("author", "unknown"),
                version=int(p.get("version", 1)),
                body=p["body"].strip(),
                last_modified=self._when(p),
                parent=str(p["parent"]) if p.get("parent") else None,
                restrictions=self._grant(p["restrictions"]) if p.get("restrictions") else None,
                links=list(p.get("links", [])),
            )

        jr = fx.get("jira", {})
        self.base_urls["jira"] = jr.get("base_url", "https://jira.example")
        for pr in jr.get("projects", []):
            self.projects[pr["key"]] = Project(
                key=pr["key"],
                name=pr["name"],
                roles={k: list(v) for k, v in pr.get("roles", {}).items()},
                security_levels={k: list(v) for k, v in pr.get("security_levels", {}).items()},
            )
        for it in jr.get("issues", []):
            created = self._when(it)
            comments = [
                Comment(author=c["author"], text=c["text"], created=self._when(c, default_days=it.get("days_ago", 0)))
                for c in it.get("comments", [])
            ]
            updated = max([created] + [c.created for c in comments])
            self.issues[it["key"]] = Issue(
                key=it["key"],
                project=it["project"],
                summary=it["summary"],
                status=it.get("status", "Open"),
                assignee=it.get("assignee"),
                reporter=it.get("reporter"),
                description=it["description"].strip(),
                created=created,
                updated=updated,
                version=1 + len(comments),
                security_level=it.get("security_level"),
                comments=comments,
                links=list(it.get("links", [])),
            )

        sl = fx.get("slack", {})
        self.base_urls["slack"] = sl.get("base_url", "https://slack.example")
        for c in sl.get("channels", []):
            self.channels[c["id"]] = Channel(id=c["id"], name=c["name"], private=bool(c["private"]), members=list(c["members"]))
        for d in sl.get("dms", []):
            self.channels[d["id"]] = Channel(id=d["id"], name=d["id"], private=True, members=list(d["members"]), is_dm=True)
        refs: dict[str, str] = {}
        for ordinal, t in enumerate(sl.get("threads", []), start=1):
            root_time = self._when(t)
            messages: list[Message] = []
            for i, m in enumerate(t["messages"]):
                created = root_time + timedelta(minutes=float(m.get("minutes", 0)))
                messages.append(Message(ts=slack_ts(created, ordinal * 100 + i), user=m["user"], text=m["text"], created=created))
            thread = Thread(channel=t["channel"], ts=messages[0].ts, messages=messages, ref=t.get("ref"), links=list(t.get("links", [])))
            self.threads[thread.item_key] = thread
            if thread.ref:
                refs[thread.ref] = thread.item_key

        gd = fx.get("gdrive", {})
        self.base_urls["gdrive"] = gd.get("base_url", "https://drive.example")
        for d in gd.get("drives", []):
            self.drives[d["id"]] = Drive(id=d["id"], name=d["name"], members=self._grant(d.get("members")))
        for f in gd.get("folders", []):
            self.folders[f["id"]] = Folder(id=f["id"], name=f["name"], drive=f["drive"], parent=f.get("parent"), permissions=self._grant(f.get("permissions")))
        for f in gd.get("files", []):
            self.files[f["id"]] = DriveFile(
                id=f["id"],
                name=f["name"],
                drive=f["drive"],
                owner=f.get("owner", "unknown"),
                mime=f.get("mime", "text/plain"),
                body=f["body"].strip(),
                modified=self._when(f),
                folder=f.get("folder"),
                permissions=self._grant(f.get("permissions")),
                anyone_with_link=bool(f.get("anyone_with_link", False)),
                links=list(f.get("links", [])),
            )

        # Resolve symbolic Slack refs ("slack:C0AUTH:auth-design") to real item ids and
        # auto-link Jira keys and Confluence URLs mentioned in text.
        def resolve(links: list[str], text: str) -> list[str]:
            out: list[str] = []
            for link in links:
                parts = link.split(":")
                if parts[0] == "slack" and len(parts) == 3 and parts[2] in refs:
                    out.append(f"slack:{refs[parts[2]]}")
                else:
                    out.append(link)
            for key in JIRA_KEY_RE.findall(text):
                if key in self.issues:
                    out.append(f"jira:{key}")
            for pid in CONFLUENCE_URL_RE.findall(text):
                if pid in self.pages:
                    out.append(f"confluence:{pid}")
            seen: list[str] = []
            for link in out:
                if link not in seen:
                    seen.append(link)
            return seen

        for p in self.pages.values():
            p.links = [l for l in resolve(p.links, p.body) if l != f"confluence:{p.id}"]
        for it in self.issues.values():
            it.links = [l for l in resolve(it.links, it.description + " " + " ".join(c.text for c in it.comments)) if l != f"jira:{it.key}"]
        for t in self.threads.values():
            t.links = resolve(t.links, " ".join(m.text for m in t.messages))
        for f in self.files.values():
            f.links = resolve(f.links, f.body)

    # A reset keeps the fixture's time anchor while it is younger than this, so Slack thread ids
    # (which are timestamps) stay the same across rehearsal resets: citations from before a reset
    # still open, and data-at-rest alerts are not raised again. An older anchor moves to now, so
    # "last week" keeps meaning last week on a long-running deployment.
    REANCHOR_AFTER = timedelta(hours=6)

    def reload(self, now: datetime | None = None) -> None:
        """Back to the fixture (demo reset). Subscribers survive; the event history is cleared."""
        if now is not None:
            self.now = now
        elif utc_now() - self.now > self.REANCHOR_AFTER:
            self.now = utc_now()
        for attr in ("users", "spaces", "pages", "projects", "issues", "channels", "threads", "drives", "folders", "files"):
            getattr(self, attr).clear()
        self._by_pid = None
        self.groups = {k: list(v) for k, v in self.fixture.get("groups", {}).items()}
        self.events.clear()
        self._load(self.fixture)

    # ------------------------------------------------------------------ events
    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subscribers.append(fn)

    def emit(self, event: Event, notify: bool = True) -> None:
        self.events.append(event)
        if not notify:
            return
        for fn in list(self._subscribers):
            fn(event)

    # ------------------------------------------------------------------ identity helpers
    def user_in(self, user_id: str, grant: Grant | None) -> bool:
        if grant is None:
            return False
        if user_id in grant.users:
            return True
        return any(user_id in self.groups.get(g, []) for g in grant.groups)

    def groups_of(self, user_id: str) -> list[str]:
        return [g for g, members in self.groups.items() if user_id in members]

    def user_by_platform_id(self, platform: str, pid: str) -> User | None:
        index = getattr(self, "_by_pid", None)
        if index is None or index[0] != len(self.users):
            index = (len(self.users), {(p, str(v)): u for u in self.users.values() for p, v in u.platform_ids.items()})
            self._by_pid = index
        return index[1].get((platform, str(pid)))

    def platform_id(self, user_id: str, platform: str) -> str | None:
        u = self.users.get(user_id)
        return u.platform_ids.get(platform) if u else None

    # ================================================================== Confluence
    def confluence_chain(self, page: Page) -> list[Page]:
        chain = [page]
        seen = {page.id}
        while chain[-1].parent and chain[-1].parent in self.pages and chain[-1].parent not in seen:
            parent = self.pages[chain[-1].parent]
            chain.append(parent)
            seen.add(parent.id)
        return chain

    def confluence_can_read(self, user_id: str, page: Page) -> bool:
        space = self.spaces.get(page.space)
        if space is None or not self.user_in(user_id, space.read):
            return False
        for p in self.confluence_chain(page):
            if p.restrictions is not None and not p.restrictions.empty() and not self.user_in(user_id, p.restrictions):
                return False
        return True

    def confluence_page_text(self, page: Page) -> str:
        return f"{page.title}\n\n{page.body}"

    def confluence_edit_page(self, page_id: str, body: str | None = None, append: str | None = None, author: str = "admin", notify: bool = True) -> Page:
        page = self.pages.get(page_id)
        if page is None or page.deleted_at:
            raise NotFound(page_id)
        if body is not None:
            page.body = body.strip()
        if append:
            page.body = page.body.rstrip() + "\n\n" + append.strip()
        page.version += 1
        page.author = author
        page.last_modified = utc_now()
        self.emit(Event("content_changed", "confluence", {"item_id": f"confluence:{page.id}", "version": page.version}), notify)
        return page

    def confluence_set_restrictions(self, page_id: str, users: list[str] | None, groups: list[str] | None, notify: bool = True) -> Page:
        page = self.pages.get(page_id)
        if page is None or page.deleted_at:
            raise NotFound(page_id)
        page.restrictions = Grant(users=list(users or []), groups=list(groups or [])) if (users or groups) else None
        page.version += 1
        page.last_modified = utc_now()
        self.emit(Event("content_changed", "confluence", {"item_id": f"confluence:{page.id}", "version": page.version, "acl": True}), notify)
        return page

    # ================================================================== Jira
    def jira_can_read(self, account_id: str, issue: Issue) -> bool:
        user = self.user_by_platform_id("jira", account_id)
        if user is None:
            return False
        project = self.projects.get(issue.project)
        if project is None:
            return False
        if not any(user.id in members for members in project.roles.values()):
            return False
        if issue.security_level:
            return user.id in project.security_levels.get(issue.security_level, [])
        return True

    def jira_issue_text(self, issue: Issue) -> str:
        lines = [f"{issue.key}: {issue.summary}", f"Status: {issue.status}. Assignee: {issue.assignee or 'unassigned'}. Project: {issue.project}.", "", issue.description]
        for c in issue.comments:
            lines += ["", f"Comment by {c.author} on {c.created.date().isoformat()}: {c.text}"]
        return "\n".join(lines)

    def jira_add_comment(self, key: str, author: str, text: str, notify: bool = True) -> Issue:
        issue = self.issues.get(key)
        if issue is None or issue.deleted_at:
            raise NotFound(key)
        now = utc_now()
        issue.comments.append(Comment(author=author, text=text, created=now))
        issue.updated = now
        issue.version += 1
        self.emit(Event("content_changed", "jira", {"item_id": f"jira:{issue.key}", "version": issue.version}), notify)
        return issue

    def jira_set_security_level(self, key: str, level: str | None, notify: bool = True) -> Issue:
        issue = self.issues.get(key)
        if issue is None or issue.deleted_at:
            raise NotFound(key)
        issue.security_level = level
        issue.updated = utc_now()
        issue.version += 1
        self.emit(Event("content_changed", "jira", {"item_id": f"jira:{issue.key}", "version": issue.version, "acl": True}), notify)
        return issue

    # ================================================================== Slack
    def slack_can_read(self, slack_uid: str, thread: Thread) -> bool:
        user = self.user_by_platform_id("slack", slack_uid)
        if user is None:
            return False
        channel = self.channels.get(thread.channel)
        if channel is None:
            return False
        if channel.is_dm or channel.private:
            return user.id in channel.members
        return (not user.slack_guest) or user.id in channel.members

    def slack_thread_title(self, thread: Thread) -> str:
        channel = self.channels[thread.channel]
        label = f"DM {', '.join(self.users[u].name for u in channel.members)}" if channel.is_dm else f"#{channel.name}"
        first = thread.messages[0].text
        return f"{label}: {first[:70]}{'...' if len(first) > 70 else ''}"

    def slack_thread_text(self, thread: Thread) -> str:
        channel = self.channels[thread.channel]
        head = f"Slack thread in {'a direct message' if channel.is_dm else '#' + channel.name}, started {thread.messages[0].created.date().isoformat()}."
        lines = [head, ""]
        for m in thread.messages:
            name = self.users[m.user].name if m.user in self.users else m.user
            lines.append(f"{name}: {m.text}")
        return "\n".join(lines)

    def slack_set_membership(self, channel_id: str, user_id: str, member: bool, notify: bool = True) -> Channel:
        channel = self.channels.get(channel_id)
        if channel is None:
            raise NotFound(channel_id)
        if user_id not in self.users:
            raise NotFound(user_id)
        if member and user_id not in channel.members:
            channel.members.append(user_id)
        if not member and user_id in channel.members:
            channel.members.remove(user_id)
        self.emit(
            Event(
                "membership_changed",
                "slack",
                {"user": self.platform_id(user_id, "slack"), "channel": channel.id, "action": "member_joined_channel" if member else "member_left_channel"},
            ),
            notify,
        )
        return channel

    def slack_post(self, channel_id: str, user_id: str, text: str, thread_ts: str | None = None, notify: bool = True) -> Thread:
        if channel_id not in self.channels:
            raise NotFound(channel_id)
        now = utc_now()
        if thread_ts:
            thread = self.threads.get(f"{channel_id}:{thread_ts}")
            if thread is None:
                raise NotFound(thread_ts)
            thread.messages.append(Message(ts=slack_ts(now, len(thread.messages)), user=user_id, text=text, created=now))
        else:
            ordinal = len(self.threads) + 1
            msg = Message(ts=slack_ts(now, ordinal * 100), user=user_id, text=text, created=now)
            thread = Thread(channel=channel_id, ts=msg.ts, messages=[msg])
            self.threads[thread.item_key] = thread
        self.emit(Event("content_changed", "slack", {"item_id": f"slack:{thread.item_key}", "version": thread.version}), notify)
        return thread

    # ================================================================== Google Drive
    def gdrive_folder_chain(self, file: DriveFile) -> list[Folder]:
        chain: list[Folder] = []
        fid = file.folder
        seen: set[str] = set()
        while fid and fid in self.folders and fid not in seen:
            folder = self.folders[fid]
            chain.append(folder)
            seen.add(fid)
            fid = folder.parent
        return chain

    def gdrive_grantees(self, file: DriveFile) -> Grant:
        users: list[str] = list(file.permissions.users) + [file.owner]
        groups: list[str] = list(file.permissions.groups)
        for folder in self.gdrive_folder_chain(file):
            users += folder.permissions.users
            groups += folder.permissions.groups
        drive = self.drives.get(file.drive)
        if drive:
            users += drive.members.users
            groups += drive.members.groups
        return Grant(users=list(dict.fromkeys(users)), groups=list(dict.fromkeys(groups)))

    def gdrive_can_read(self, email: str, file: DriveFile) -> bool:
        user = self.user_by_platform_id("gdrive", email)
        if user is None:
            return False
        # "Anyone with the link" is deliberately not treated as a grant (conservative; stated trade-off).
        return self.user_in(user.id, self.gdrive_grantees(file))

    def gdrive_file_text(self, file: DriveFile) -> str:
        return f"{file.name}\n\n{file.body}"

    def gdrive_update(self, file_id: str, body: str | None = None, append: str | None = None, notify: bool = True) -> DriveFile:
        file = self.files.get(file_id)
        if file is None or file.deleted_at:
            raise NotFound(file_id)
        if body is not None:
            file.body = body.strip()
        if append:
            file.body = file.body.rstrip() + "\n\n" + append.strip()
        file.version += 1
        file.modified = utc_now()
        self.emit(Event("content_changed", "gdrive", {"item_id": f"gdrive:{file.id}", "version": file.version}), notify)
        return file

    def gdrive_share(self, file_id: str, user_id: str, share: bool, notify: bool = True) -> DriveFile:
        file = self.files.get(file_id)
        if file is None or file.deleted_at:
            raise NotFound(file_id)
        if share and user_id not in file.permissions.users:
            file.permissions.users.append(user_id)
        if not share and user_id in file.permissions.users:
            file.permissions.users.remove(user_id)
        file.version += 1
        file.modified = utc_now()
        self.emit(Event("content_changed", "gdrive", {"item_id": f"gdrive:{file.id}", "version": file.version, "acl": True}), notify)
        return file

    # ================================================================== groups (company directory)
    def group_set(self, group: str, user_id: str, member: bool, notify: bool = True) -> list[str]:
        members = self.groups.setdefault(group, [])
        if member and user_id not in members:
            members.append(user_id)
        if not member and user_id in members:
            members.remove(user_id)
        user = self.users[user_id]
        for platform in ("confluence", "jira", "gdrive"):
            pid = user.platform_ids.get(platform)
            if pid:
                self.emit(Event("membership_changed", platform, {"user": pid, "group": group, "action": "added" if member else "removed"}), notify)
        return members

    # ================================================================== deletion (tombstones)
    def delete_item(self, item_id: str, notify: bool = True) -> None:
        platform, _, rest = item_id.partition(":")
        now = utc_now()
        if platform == "confluence" and rest in self.pages:
            self.pages[rest].deleted_at = now
        elif platform == "jira" and rest in self.issues:
            self.issues[rest].deleted_at = now
        elif platform == "slack" and rest in self.threads:
            self.threads[rest].deleted_at = now
        elif platform == "gdrive" and rest in self.files:
            self.files[rest].deleted_at = now
        else:
            raise NotFound(item_id)
        self.emit(Event("item_deleted", platform, {"item_id": item_id}), notify)  # type: ignore[arg-type]

    # ================================================================== admin snapshot
    def snapshot(self) -> dict[str, Any]:
        return {
            "users": [
                {"id": u.id, "name": u.name, "title": u.title, "roles": u.roles, "platform_ids": u.platform_ids, "slack_guest": u.slack_guest}
                for u in self.users.values()
            ],
            "groups": self.groups,
            "confluence": {
                "spaces": [{"key": s.key, "name": s.name, "read": {"users": s.read.users, "groups": s.read.groups}} for s in self.spaces.values()],
                "pages": [
                    {
                        "id": p.id,
                        "space": p.space,
                        "title": p.title,
                        "version": p.version,
                        "last_modified": iso(p.last_modified),
                        "restrictions": {"users": p.restrictions.users, "groups": p.restrictions.groups} if p.restrictions else None,
                        "deleted": bool(p.deleted_at),
                    }
                    for p in self.pages.values()
                ],
            },
            "jira": {
                "projects": [{"key": p.key, "name": p.name, "roles": p.roles, "security_levels": p.security_levels} for p in self.projects.values()],
                "issues": [
                    {"key": i.key, "project": i.project, "summary": i.summary, "status": i.status, "security_level": i.security_level, "version": i.version, "updated": iso(i.updated), "deleted": bool(i.deleted_at)}
                    for i in self.issues.values()
                ],
            },
            "slack": {
                "channels": [{"id": c.id, "name": c.name, "private": c.private, "is_dm": c.is_dm, "members": c.members} for c in self.channels.values()],
                "threads": [
                    {"item_id": f"slack:{t.item_key}", "channel": t.channel, "ref": t.ref, "title": self.slack_thread_title(t), "version": t.version, "last_modified": iso(t.last_modified), "deleted": bool(t.deleted_at)}
                    for t in self.threads.values()
                ],
            },
            "gdrive": {
                "drives": [{"id": d.id, "name": d.name, "members": {"users": d.members.users, "groups": d.members.groups}} for d in self.drives.values()],
                "folders": [{"id": f.id, "name": f.name, "drive": f.drive, "permissions": {"users": f.permissions.users, "groups": f.permissions.groups}} for f in self.folders.values()],
                "files": [
                    {"id": f.id, "name": f.name, "drive": f.drive, "folder": f.folder, "version": f.version, "modified": iso(f.modified), "shared_with": f.permissions.users, "anyone_with_link": f.anyone_with_link, "deleted": bool(f.deleted_at)}
                    for f in self.files.values()
                ],
            },
        }
