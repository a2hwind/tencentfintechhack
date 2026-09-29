"""Mock Confluence Cloud: the subset of the REST API the adapter calls.

Permission semantics: space permission AND page restriction, restrictions inherited
down the page tree (see CompanyStore.confluence_can_read). Requests carrying an
`X-Act-As: <accountId>` header are evaluated as that user: 403 when the user cannot
read the page, 404 when the page is gone. Without the header the call runs as the
connector's app credential (read scope) that the sync worker uses.
"""

from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Query, Request

from .store import CompanyStore, Page, iso, parse_iso

CQL_LASTMOD_RE = re.compile(r'lastmodified\s*>=\s*"([^"]+)"')


def create_confluence_app(store: CompanyStore) -> FastAPI:
    app = FastAPI(title="Mock Confluence", docs_url=None, redoc_url=None)

    def page_or_404(page_id: str) -> Page:
        page = store.pages.get(page_id)
        if page is None or page.deleted_at is not None:
            raise HTTPException(status_code=404, detail={"message": "No content found with id"})
        return page

    def render(page: Page, expand: str) -> dict:
        body = {
            "id": page.id,
            "type": "page",
            "status": "current",
            "title": page.title,
            "space": {"key": page.space, "name": store.spaces[page.space].name},
            "version": {"number": page.version, "when": iso(page.last_modified), "by": {"accountId": page.author}},
            "_links": {"webui": f"/pages/{page.id}", "base": store.base_urls["confluence"]},
        }
        if "body" in expand:
            body["body"] = {"storage": {"value": page.body, "representation": "storage"}}
        if "ancestors" in expand:
            body["ancestors"] = [{"id": p.id, "title": p.title} for p in store.confluence_chain(page)[1:]]
        if "links" in expand:
            body["x_links"] = page.links
        return body

    @app.get("/rest/api/content/search")
    def search(cql: str = "", limit: int = Query(200, le=1000)):
        since = None
        m = CQL_LASTMOD_RE.search(cql)
        if m:
            since = parse_iso(m.group(1))
        results = []
        for page in store.pages.values():
            when = page.deleted_at or page.last_modified
            if since is not None and when <= since:
                continue
            results.append(
                {
                    "id": page.id,
                    "type": "page",
                    "status": "trashed" if page.deleted_at else "current",
                    "title": page.title,
                    "space": {"key": page.space},
                    "version": {"number": page.version, "when": iso(when)},
                }
            )
        results.sort(key=lambda r: r["version"]["when"])
        return {"results": results[:limit], "size": len(results[:limit])}

    @app.get("/rest/api/content/{page_id}")
    def get_content(page_id: str, request: Request, expand: str = ""):
        page = page_or_404(page_id)
        act_as = request.headers.get("x-act-as")
        if act_as is not None and not store.confluence_can_read(_user_id(store, act_as), page):
            raise HTTPException(status_code=403, detail={"message": "You do not have permission to view this content"})
        return render(page, expand)

    @app.get("/rest/api/content/{page_id}/restriction")
    def get_restrictions(page_id: str):
        """Space read permission plus the restriction chain, in one call.

        Real Confluence needs /space/{key}/permission and /content/{id}/restriction per
        ancestor; the mock folds them so the adapter's projection logic is the only logic.
        """
        page = page_or_404(page_id)
        space = store.spaces[page.space]
        chain = store.confluence_chain(page)
        restrictions = [
            {
                "page_id": p.id,
                "read": {
                    "users": [{"accountId": store.platform_id(u, "confluence") or u} for u in p.restrictions.users],
                    "groups": [{"name": g} for g in p.restrictions.groups],
                },
            }
            for p in chain
            if p.restrictions is not None and not p.restrictions.empty()
        ]
        # Only several restrictions on the chain need resolving to people (the adapter intersects them).
        effective_users = [
            store.platform_id(u.id, "confluence")
            for u in store.users.values()
            if "confluence" in u.platform_ids and store.confluence_can_read(u.id, page)
        ] if len(restrictions) > 1 else []
        return {
            "space": {"key": space.key, "read": {"users": [{"accountId": u} for u in space.read.users], "groups": [{"name": g} for g in space.read.groups]}},
            "restrictions": restrictions,
            "effective_users": effective_users,
            "version": page.version,
        }

    @app.get("/rest/api/user/{account_id}/memberships")
    def memberships(account_id: str):
        """Groups and readable spaces for a user (real: /user/memberof + /space?permission=read)."""
        user = store.user_by_platform_id("confluence", account_id)
        if user is None:
            raise HTTPException(status_code=404, detail={"message": "No user found"})
        return {
            "accountId": account_id,
            "groups": [{"name": g} for g in store.groups_of(user.id)],
            "spaces": [{"key": s.key} for s in store.spaces.values() if store.user_in(user.id, s.read)],
        }

    return app


def _user_id(store: CompanyStore, account_id: str) -> str:
    user = store.user_by_platform_id("confluence", account_id)
    return user.id if user else f"unknown:{account_id}"
