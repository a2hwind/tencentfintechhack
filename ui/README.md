# Internal Brain — UI

Next.js 15 front end for the Internal Brain API: a chat for employees (`/`), the compliance console over the hash-chained audit log (`/audit`) and a demo-only admin panel that mutates the mock platforms (`/admin`).

For the demo there are two more pages:

- `/presenter` — the "glass box": what the asker sees on the left, what compliance sees on the right (trust-boundary diagram, pipeline stepper with measured stage timings, and the live audit chain over `GET /audit/stream`), admin quick actions and a guided demo. Beat narration lives in `lib/demoBeats.ts`.
- `/compare` — two identities or questions asked at once, with a byte-for-byte comparison of the raw response bodies (`/compare?preset=restricted-vs-missing`, add `&run=1` to ask immediately).

Citation numbers in answers open an evidence drawer: the source is fetched with `GET /sources/{doc}` as the asker, so it is re-checked live and logged on every open. `/audit?nl=<question>` prefills and runs a natural-language audit query (`&verify=1` also runs Verify chain).

## Run

```bash
cd ui
npm install
npm run dev        # http://localhost:3000
```

The backend must be running (from the repository root: `uvicorn internal_brain.api.app:app --port 8000`) and must allow the UI's origin (`CORS_ORIGINS=http://localhost:3000`).

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Base URL of the Internal Brain API. Read at build time (`npm run build`) and by `next dev`. |

## Identity

There is no login in the demo. The header's user switcher lists `GET /users`; the selection is kept in `localStorage` and sent on every request as `X-User-Id`. The compliance console needs a user with the `compliance` role (fixture user `compliance`); the admin panel needs the `admin` role (fixture user `admin`).

## Production build

```bash
npm run build      # standalone output in .next/standalone (Docker friendly)
npm run start
```

No UI libraries: the styling is `app/globals.css` (light and dark via `prefers-color-scheme`). `lib/api.ts` holds the typed client for every endpoint; `lib/user.tsx` the identity context.
