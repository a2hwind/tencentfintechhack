"""Mock Google Drive v3 (plus the one Directory API call the adapter needs).

Permission semantics: a file's readers are its direct grantees plus the grantees
inherited from its folder chain and shared drive; "anyone with the link" is not
treated as a grant (see CompanyStore.gdrive_can_read). `X-Act-As: <email>` evaluates
the request as that user (403 / 404 as Drive would).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .store import CompanyStore, DriveFile, iso, parse_iso, utc_now


def create_gdrive_app(store: CompanyStore) -> FastAPI:
    app = FastAPI(title="Mock Google Drive", docs_url=None, redoc_url=None)

    def file_or_404(file_id: str) -> DriveFile:
        f = store.files.get(file_id)
        if f is None or f.deleted_at is not None:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "File not found"}})
        return f

    def render(f: DriveFile) -> dict:
        return {
            "id": f.id,
            "name": f.name,
            "mimeType": f.mime,
            "modifiedTime": iso(f.modified),
            "version": str(f.version),
            "driveId": f.drive,
            "x_drive_name": store.drives[f.drive].name if f.drive in store.drives else f.drive,
            "parents": [f.folder or f.drive],
            "owners": [{"emailAddress": store.platform_id(f.owner, "gdrive") or f.owner}],
            "webViewLink": f"{store.base_urls['gdrive']}/file/d/{f.id}/view",
            "x_links": f.links,
        }

    @app.get("/drive/v3/changes/startPageToken")
    def start_page_token():
        return {"startPageToken": "0"}

    @app.get("/drive/v3/changes")
    def changes(pageToken: str = "0"):
        since = None if pageToken in ("", "0") else parse_iso(pageToken)
        out = []
        for f in store.files.values():
            when = f.deleted_at or f.modified
            if since is not None and when <= since:
                continue
            change = {"fileId": f.id, "time": iso(when), "removed": f.deleted_at is not None}
            if f.deleted_at is None:
                change["file"] = render(f)
            out.append(change)
        out.sort(key=lambda c: c["time"])
        if out:
            new_token = out[-1]["time"]
        else:
            new_token = pageToken if pageToken not in ("", "0") else iso(utc_now())
        return {"changes": out, "newStartPageToken": new_token}

    @app.get("/drive/v3/files/{file_id}")
    def get_file(file_id: str, request: Request, fields: str = ""):
        f = file_or_404(file_id)
        act_as = request.headers.get("x-act-as")
        if act_as is not None and not store.gdrive_can_read(act_as, f):
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "The user does not have sufficient permissions for this file."}})
        return render(f)

    @app.get("/drive/v3/files/{file_id}/export")
    def export_file(file_id: str, request: Request, mimeType: str = "text/plain"):
        f = file_or_404(file_id)
        act_as = request.headers.get("x-act-as")
        if act_as is not None and not store.gdrive_can_read(act_as, f):
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "The user does not have sufficient permissions for this file."}})
        return {"text": f.body}

    @app.get("/drive/v3/files/{file_id}/permissions")
    def permissions(file_id: str):
        """Direct and inherited grantees. Real Drive marks inherited ones in permissionDetails."""
        f = file_or_404(file_id)
        grant = store.gdrive_grantees(f)
        perms = [{"type": "user", "emailAddress": store.platform_id(u, "gdrive") or u, "role": "reader"} for u in grant.users]
        perms += [{"type": "group", "emailAddress": f"{g}@{store.domain}", "x_group": g, "role": "reader"} for g in grant.groups]
        if f.anyone_with_link:
            perms.append({"type": "anyone", "allowFileDiscovery": False, "role": "reader"})
        return {"permissions": perms, "version": f.version}

    @app.get("/admin/directory/v1/groups")
    def directory_groups(userKey: str):
        user = store.user_by_platform_id("gdrive", userKey)
        if user is None:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Resource Not Found: userKey"}})
        return {"groups": [{"email": f"{g}@{store.domain}", "name": g} for g in store.groups_of(user.id)], "domain": store.domain}

    return app
