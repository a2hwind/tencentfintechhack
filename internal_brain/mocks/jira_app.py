"""Mock Jira Cloud: the subset of the v3 REST API the adapter calls.

Permission semantics: project permission scheme (browse via a project role) AND issue
security level (see CompanyStore.jira_can_read). `X-Act-As: <accountId>` evaluates the
request as that user (403 / 404 as Jira would).
"""

from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Query, Request

from .store import CompanyStore, Issue, iso, parse_iso

JQL_UPDATED_RE = re.compile(r'updated\s*>=\s*"([^"]+)"')


def create_jira_app(store: CompanyStore) -> FastAPI:
    app = FastAPI(title="Mock Jira", docs_url=None, redoc_url=None)

    def issue_or_404(key: str) -> Issue:
        issue = store.issues.get(key)
        if issue is None or issue.deleted_at is not None:
            raise HTTPException(status_code=404, detail={"errorMessages": ["Issue does not exist or you do not have permission to see it."]})
        return issue

    def render(issue: Issue) -> dict:
        return {
            "key": issue.key,
            "id": issue.key,
            "self": f"{store.base_urls['jira']}/rest/api/3/issue/{issue.key}",
            "fields": {
                "summary": issue.summary,
                "status": {"name": issue.status},
                "project": {"key": issue.project, "name": store.projects[issue.project].name},
                "assignee": {"accountId": issue.assignee} if issue.assignee else None,
                "reporter": {"accountId": issue.reporter} if issue.reporter else None,
                "created": iso(issue.created),
                "updated": iso(issue.updated),
                "description": issue.description,
                "security": {"name": issue.security_level} if issue.security_level else None,
                "comment": {
                    "comments": [
                        {"author": {"accountId": c.author}, "created": iso(c.created), "body": c.text} for c in issue.comments
                    ]
                },
            },
            "x_version": issue.version,
            "x_links": issue.links,
        }

    @app.get("/rest/api/3/search")
    def search(jql: str = "", maxResults: int = Query(200, le=1000)):
        since = None
        m = JQL_UPDATED_RE.search(jql)
        if m:
            since = parse_iso(m.group(1))
        issues = []
        deleted = []
        for issue in store.issues.values():
            when = issue.deleted_at or issue.updated
            if since is not None and when <= since:
                continue
            if issue.deleted_at:
                deleted.append({"key": issue.key, "deleted": iso(issue.deleted_at)})
            else:
                issues.append({"key": issue.key, "fields": {"updated": iso(issue.updated), "project": {"key": issue.project}}, "x_version": issue.version})
        issues.sort(key=lambda i: i["fields"]["updated"])
        # Real Jira reports deletions through webhooks, not search; the mock adds them here so
        # cursor sync can write tombstones without a second channel.
        return {"issues": issues[:maxResults], "total": len(issues), "x_deleted": deleted}

    @app.get("/rest/api/3/issue/{key}")
    def get_issue(key: str, request: Request):
        issue = issue_or_404(key)
        act_as = request.headers.get("x-act-as")
        if act_as is not None and not store.jira_can_read(act_as, issue):
            raise HTTPException(status_code=404, detail={"errorMessages": ["Issue does not exist or you do not have permission to see it."]})
        return render(issue)

    @app.get("/rest/api/3/project/{key}/roles")
    def project_roles(key: str):
        project = store.projects.get(key)
        if project is None:
            raise HTTPException(status_code=404, detail={"errorMessages": ["No project could be found with key"]})
        return {
            "key": project.key,
            "roles": [{"name": role, "browse": True, "actors": [{"accountId": store.platform_id(u, "jira")} for u in members]} for role, members in project.roles.items()],
            "securityLevels": [{"name": level, "members": [{"accountId": store.platform_id(u, "jira")} for u in members]} for level, members in project.security_levels.items()],
        }

    @app.get("/rest/api/3/user/{account_id}/entitlements")
    def entitlements(account_id: str):
        """Project roles and security levels for a user (real: per-project role actors + level members)."""
        user = store.user_by_platform_id("jira", account_id)
        if user is None:
            raise HTTPException(status_code=404, detail={"errorMessages": ["The user does not exist."]})
        projects = []
        for project in store.projects.values():
            roles = [role for role, members in project.roles.items() if user.id in members]
            levels = [level for level, members in project.security_levels.items() if user.id in members]
            if roles or levels:
                projects.append({"key": project.key, "roles": roles, "securityLevels": levels})
        return {"accountId": account_id, "projects": projects}

    return app
