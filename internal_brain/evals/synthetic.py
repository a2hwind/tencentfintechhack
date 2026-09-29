"""Synthetic companies at any size, with every ACL shape the four platforms have.

    company = generate_company(users=1000, items=20_000, seed=7)   # a fixture dict, like company_a.yaml
    store = CompanyStore(company)

Shapes covered (the property tests assert each one occurs):
  Confluence  space read grants (groups and users); page restrictions (users and/or groups);
              restrictions inherited down page trees; two restrictions on one chain (intersection);
              people named in a restriction who lack space access
  Jira        project roles; issue security levels; level members who are not in any project role
  Slack       public and private channels; direct messages; guests (no workspace access)
  Drive       shared-drive members; nested folder grants; per-file shares; the owner;
              "anyone with the link" (deliberately not a grant)
  People      employees on every platform; contractors on Slack (as guests) and Drive only

Text is built from a pseudo-word vocabulary: each container has its own topic words, so a query
made of topic words matches documents in containers the asker can and cannot see. SyntheticEmbedder
turns that text into vectors quickly (a dense random projection of the bag of words), for scale runs
where the hash embedder's per-token hashing would dominate the wall clock.
"""

from __future__ import annotations

import asyncio
import random
import zlib
from dataclasses import dataclass, field

import httpx
import numpy as np

from ..core.embeddings import TOKEN_RE

CONSONANTS = "bdfgklmnprstvz"
VOWELS = "aeiou"


def pseudo_words(n: int, rng: random.Random, min_syllables: int = 2, max_syllables: int = 4) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    while len(words) < n:
        word = "".join(rng.choice(CONSONANTS) + rng.choice(VOWELS) for _ in range(rng.randint(min_syllables, max_syllables)))
        if len(word) >= 5 and word not in seen:
            seen.add(word)
            words.append(word)
    return words


@dataclass
class Vocabulary:
    filler: list[str]
    topics: dict[str, list[str]]  # container key -> its topic words

    def sentence(self, rng: random.Random, topic: list[str], words: int = 14) -> str:
        picks = []
        for _ in range(words):
            if rng.random() < 0.18:
                picks.append(rng.choice(topic))
            else:
                # Zipf-ish: early filler words are much more common
                picks.append(self.filler[min(int(rng.paretovariate(1.2)) - 1, len(self.filler) - 1)])
        return " ".join(picks).capitalize() + "."

    def body(self, rng: random.Random, topic: list[str], paragraphs: int, sentences: int = 6) -> str:
        return "\n\n".join(" ".join(self.sentence(rng, topic) for _ in range(sentences)) for _ in range(paragraphs))


def generate_company(users: int = 60, items: int = 240, seed: int = 0, paragraphs: tuple[int, int] = (1, 2)) -> dict:
    """A fixture dict (the company_a.yaml shape). `paragraphs` sets the size of each document:
    one paragraph is roughly one chunk."""
    rng = random.Random(seed)
    n_contractors = max(1, users // 20)
    n_employees = max(3, users - n_contractors)
    employees = [f"u{i:04d}" for i in range(n_employees)]
    contractors = [f"c{i:04d}" for i in range(n_contractors)]
    people = []
    for uid in employees:
        people.append({
            "id": uid, "name": f"Employee {uid[1:]}", "email": f"{uid}@synth.example", "title": "Engineer", "roles": ["employee"],
            "platform_ids": {"confluence": uid, "jira": uid, "slack": f"U{uid.upper()}", "gdrive": f"{uid}@synth.example"},
        })
    for uid in contractors:
        people.append({
            "id": uid, "name": f"Contractor {uid[1:]}", "email": f"{uid}@vendor.example", "title": "Contractor", "roles": ["employee"],
            "slack_guest": True, "platform_ids": {"slack": f"U{uid.upper()}", "gdrive": f"{uid}@vendor.example"},
        })
    everyone = employees + contractors

    def some(pool: list[str], lo: int, hi: int) -> list[str]:
        return rng.sample(pool, min(len(pool), rng.randint(lo, hi)))

    n_groups = max(3, users // 15)
    groups = {f"g{i:03d}": some(employees, 3, max(4, min(40, n_employees // 3))) for i in range(n_groups)}
    groups["staff"] = list(employees)
    group_names = [g for g in groups if g != "staff"]
    for c in contractors:  # contractors sit in a few groups too (Drive shares reach them through groups)
        if rng.random() < 0.5:
            groups[rng.choice(group_names)].append(c)

    def grant(n_groups_: tuple[int, int], n_users_: tuple[int, int], pool: list[str] | None = None) -> dict:
        return {"groups": some(group_names, *n_groups_), "users": some(pool or employees, *n_users_)}

    budget = {"confluence": int(items * 0.3), "jira": int(items * 0.25), "slack": int(items * 0.25)}
    budget["gdrive"] = items - sum(budget.values())

    n_spaces = max(2, users // 20)
    n_projects = max(2, users // 30)
    n_channels = max(3, users // 8)
    n_dms = max(1, users // 10)
    n_drives = max(2, users // 25)
    containers = [f"space:S{i:03d}" for i in range(n_spaces)] + [f"project:P{i:03d}" for i in range(n_projects)]
    containers += [f"channel:C{i:04d}" for i in range(n_channels)] + [f"dm:D{i:04d}" for i in range(n_dms)] + [f"drive:DR{i:03d}" for i in range(n_drives)]
    vocab_words = pseudo_words(2000 + 3 * len(containers), rng)
    vocab = Vocabulary(filler=vocab_words[:2000], topics={c: vocab_words[2000 + 3 * i : 2003 + 3 * i] for i, c in enumerate(containers)})

    def body(container: str) -> str:
        return vocab.body(rng, vocab.topics[container], rng.randint(*paragraphs))

    # ---------------- Confluence
    spaces = []
    for i in range(n_spaces):
        read = grant((1, 2), (0, 2))
        if rng.random() < 0.15:
            read["groups"].append("staff")
        spaces.append({"key": f"S{i:03d}", "name": f"Space {i}", "read": read})
    pages = []
    for n in range(budget["confluence"]):
        space = rng.choice(spaces)
        siblings = [p for p in pages if p["space"] == space["key"]]
        page = {"id": str(100000 + n), "space": space["key"], "title": f"Page {n} " + " ".join(vocab.topics[f"space:{space['key']}"][:2]),
                "author": rng.choice(employees), "version": rng.randint(1, 9), "days_ago": rng.randint(0, 400), "body": body(f"space:{space['key']}")}
        if siblings and rng.random() < 0.5:
            page["parent"] = rng.choice(siblings)["id"]
        if rng.random() < 0.18:
            # grantees may lack space access: restrictions only ever narrow
            page["restrictions"] = {"users": some(employees, 0, 3), "groups": some(group_names, 0, 1)} if rng.random() < 0.8 else {"users": some(employees, 1, 2)}
            if not page["restrictions"]["users"] and not page["restrictions"].get("groups"):
                page["restrictions"]["users"] = [rng.choice(employees)]
        pages.append(page)

    # ---------------- Jira
    projects = []
    for i in range(n_projects):
        devs = some(employees, 3, max(4, min(25, n_employees // 2)))
        roles = {"developers": devs}
        if rng.random() < 0.6:
            roles["viewers"] = some(employees, 1, 8)
        levels = {"Security only": some(devs, 1, 3)}
        if rng.random() < 0.5:
            outsider = [u for u in employees if not any(u in m for m in roles.values())]
            levels["Security only"] += some(outsider, 0, 1)  # in the level but cannot browse the project
        projects.append({"key": f"P{i:03d}", "name": f"Project {i}", "roles": roles, "security_levels": levels})
    issues = []
    for n in range(budget["jira"]):
        project = rng.choice(projects)
        issue = {"key": f"{project['key']}-{n + 1}", "project": project["key"], "summary": "Issue " + " ".join(vocab.topics[f"project:{project['key']}"][:2]),
                 "status": rng.choice(["Open", "In Progress", "Done", "Blocked"]), "assignee": rng.choice(project["roles"]["developers"]),
                 "reporter": rng.choice(project["roles"]["developers"]), "days_ago": rng.randint(0, 400), "description": body(f"project:{project['key']}")}
        if rng.random() < 0.12:
            issue["security_level"] = "Security only"
        issues.append(issue)

    # ---------------- Slack
    channels = []
    for i in range(n_channels):
        private = rng.random() < 0.45
        members = some(employees, 2, max(3, min(30, n_employees // 2)))
        members += some(contractors, 0, 1) if rng.random() < 0.3 else []
        channels.append({"id": f"C{i:04d}", "name": f"channel-{i}", "private": private, "members": members})
    dms = [{"id": f"D{i:04d}", "members": rng.sample(everyone, 2)} for i in range(n_dms)]
    threads = []
    for n in range(budget["slack"]):
        if rng.random() < 0.1:
            dm = rng.choice(dms)
            channel, members, topic = dm["id"], dm["members"], f"dm:{dm['id']}"
        else:
            ch = rng.choice(channels)
            channel, members, topic = ch["id"], ch["members"], f"channel:{ch['id']}"
        messages = [{"user": rng.choice(members), "minutes": 7 * m, "text": " ".join(vocab.sentence(rng, vocab.topics[topic]) for _ in range(6))}
                    for m in range(rng.randint(*paragraphs))]
        threads.append({"channel": channel, "days_ago": rng.randint(0, 120), "messages": messages})

    # ---------------- Drive
    drives = [{"id": f"DR{i:03d}", "name": f"Drive {i}", "members": grant((1, 2), (0, 2))} for i in range(n_drives)]
    folders = []
    for i in range(n_drives * 2):
        drive = rng.choice(drives)
        parents = [f for f in folders if f["drive"] == drive["id"]]
        folder = {"id": f"F{i:04d}", "name": f"Folder {i}", "drive": drive["id"], "permissions": {"users": some(everyone, 0, 2), "groups": some(group_names, 0, 1)}}
        if parents and rng.random() < 0.5:
            folder["parent"] = rng.choice(parents)["id"]
        folders.append(folder)
    files = []
    for n in range(budget["gdrive"]):
        drive = rng.choice(drives)
        in_drive = [f for f in folders if f["drive"] == drive["id"]]
        file = {"id": f"f{n:06d}", "name": f"File {n} " + " ".join(vocab.topics[f"drive:{drive['id']}"][:2]), "drive": drive["id"],
                "owner": rng.choice(employees), "days_ago": rng.randint(0, 400), "body": body(f"drive:{drive['id']}")}
        if in_drive and rng.random() < 0.6:
            file["folder"] = rng.choice(in_drive)["id"]
        if rng.random() < 0.2:
            file["permissions"] = {"users": some(everyone, 1, 2), "groups": []}
        if rng.random() < 0.1:
            file["anyone_with_link"] = True
        files.append(file)

    return {
        "company": {"name": f"Synthetic {seed}", "domain": "synth.example", "slack_workspace": "TSYNTH"},
        "users": people,
        "groups": groups,
        "confluence": {"base_url": "https://wiki.synth.example", "spaces": spaces, "pages": pages},
        "jira": {"base_url": "https://jira.synth.example", "projects": projects, "issues": issues},
        "slack": {"base_url": "https://synth.slack.com", "channels": channels, "dms": dms, "threads": threads},
        "gdrive": {"base_url": "https://drive.synth.example", "drives": drives, "folders": folders, "files": files},
        "_vocabulary": {"filler": vocab.filler, "topics": vocab.topics},
    }


def topic_queries(company: dict, rng: random.Random, n: int) -> list[str]:
    """Questions made of one container's topic words plus a common word."""
    topics = company["_vocabulary"]["topics"]
    filler = company["_vocabulary"]["filler"]
    keys = sorted(topics)
    out = []
    for _ in range(n):
        words = rng.sample(topics[rng.choice(keys)], 2)
        out.append(f"{words[0]} {words[1]} {filler[rng.randint(0, 50)]}")
    return out


class SyntheticEmbedder:
    """Bag-of-words through a fixed random projection, unit-normalised: fast, deterministic,
    and topic-aware (documents that share topic words point the same way)."""

    name = "synthetic-256"
    min_similarity = 0.12

    def __init__(self, dim: int = 256, seed: int = 0, buckets: int = 1 << 15):
        rng = np.random.default_rng(seed)
        self.dim = dim
        self.buckets = buckets
        self._table = rng.standard_normal((buckets, dim)).astype(np.float32)

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            ids = [zlib.crc32(w.encode()) % self.buckets for w in TOKEN_RE.findall(text.lower())]
            if ids:
                vec = self._table[ids].sum(axis=0)
                norm = float(np.linalg.norm(vec))
                out[row] = vec / norm if norm > 0 else vec
        return out



# ---------------------------------------------------------------------------
# Projection exactness: the adapters' tokens against each platform's native rule
# ---------------------------------------------------------------------------
@dataclass
class ProjectionReport:
    pairs: int = 0
    allowed: int = 0
    over_grants: list[tuple[str, str, str]] = field(default_factory=list)  # (user, item, token that matched)
    under_grants: list[tuple[str, str]] = field(default_factory=list)
    acls: dict[str, list[str]] = field(default_factory=dict)
    principals: dict[str, list[str]] = field(default_factory=dict)

    @property
    def exact(self) -> bool:
        return not self.over_grants and not self.under_grants


def adapters_for(store) -> tuple[dict, list[httpx.AsyncClient]]:
    """The production adapters, each over its mock platform through an in-process transport."""
    from ..adapters import ConfluenceAdapter, GoogleDriveAdapter, JiraAdapter, SlackAdapter
    from ..mocks.confluence_app import create_confluence_app
    from ..mocks.gdrive_app import create_gdrive_app
    from ..mocks.jira_app import create_jira_app
    from ..mocks.slack_app import create_slack_app

    apps = {"confluence": create_confluence_app(store), "jira": create_jira_app(store), "slack": create_slack_app(store), "gdrive": create_gdrive_app(store)}
    clients = {name: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://mock-{name}") for name, app in apps.items()}
    adapters = {
        "confluence": ConfluenceAdapter(clients["confluence"]),
        "jira": JiraAdapter(clients["jira"]),
        "slack": SlackAdapter(clients["slack"]),
        "gdrive": GoogleDriveAdapter(clients["gdrive"]),
    }
    return adapters, list(clients.values())


def native_items(store) -> list[tuple[str, str, object]]:
    """(item_id, platform, native object) for every live item in the store."""
    out: list[tuple[str, str, object]] = []
    out += [(f"confluence:{p.id}", "confluence", p) for p in store.pages.values() if p.deleted_at is None]
    out += [(f"jira:{i.key}", "jira", i) for i in store.issues.values() if i.deleted_at is None]
    out += [(f"slack:{t.item_key}", "slack", t) for t in store.threads.values() if t.deleted_at is None]
    out += [(f"gdrive:{f.id}", "gdrive", f) for f in store.files.values() if f.deleted_at is None]
    return out


def native_can_read(store, user, platform: str, obj) -> bool:
    """The platform's own rule, for a user who must hold an account there."""
    pid = user.platform_ids.get(platform)
    if pid is None:
        return False
    if platform == "confluence":
        return store.confluence_can_read(user.id, obj)
    if platform == "jira":
        return store.jira_can_read(pid, obj)
    if platform == "slack":
        return store.slack_can_read(pid, obj)
    return store.gdrive_can_read(pid, obj)


async def check_projection(store) -> ProjectionReport:
    """For every (user, item): native rule == (user's tokens intersect the item's ACL tokens)."""
    adapters, clients = adapters_for(store)
    report = ProjectionReport()
    try:
        items = native_items(store)
        acls = await asyncio.gather(*(adapters[platform].get_acl(item_id) for item_id, platform, _ in items))
        report.acls = {item_id: sorted(acl.allowed_principals) for (item_id, _, _), acl in zip(items, acls)}
        for user in store.users.values():
            tokens: set[str] = set()
            for platform, pid in user.platform_ids.items():
                tokens.update(await adapters[platform].principals_for(pid))
            report.principals[user.id] = sorted(tokens)
            for item_id, platform, obj in items:
                granted = tokens.intersection(report.acls[item_id])
                native = native_can_read(store, user, platform, obj)
                report.pairs += 1
                report.allowed += int(native)
                if granted and not native:
                    report.over_grants.append((user.id, item_id, sorted(granted)[0]))
                elif native and not granted:
                    report.under_grants.append((user.id, item_id))
    finally:
        for client in clients:
            await client.aclose()
    return report
