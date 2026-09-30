# Put the demo online

Two ways, same app:

- **Render, free.** No server to manage: Render builds `deploy/render/Dockerfile` from the repository and runs the API, the UI and Caddy in one container. A free instance sleeps after 15 minutes without visitors and takes about a minute to wake, and every wake starts a fresh demo (new index, new audit chain).
- **Your own server** (Tencent Cloud Lighthouse, or any Ubuntu machine). Always on, keeps its audit log, real HTTPS on your own name. About 20 minutes.

## Render (free)

1. Push the repository to GitHub; `render.yaml` at its root describes the service.
2. At [render.com](https://render.com), sign in with GitHub, then **New**, **Blueprint**, and pick the repository (for a private repository, give Render access to it when GitHub asks).
3. Render shows one service, `internal-brain` (free, Singapore), and asks for two values:
   - `DEMO_PASSWORD`: the password the site asks for, with the user name `judge`. Leave it empty and the demo is open to anyone.
   - `LLM_API_KEY`: the TokenHub key. Leave it empty to run the offline answerer (no model calls, nothing to pay).
4. **Deploy Blueprint.** The first build takes about 5 to 10 minutes. The address is at the top of the service's page (`https://internal-brain-….onrender.com`); add `/presenter`.

Every push to `main` redeploys. A free instance has 0.1 CPU and 512 MB of memory; the three processes use about 230 MB. Open the link a minute before you show it to anyone, so it is awake. Keep Slack on the mock here: a sleeping service misses Slack's events.

## Your own server

One Tencent Cloud Lighthouse server in Singapore runs the same three containers as a laptop: Caddy in front (HTTPS, optional password), the UI, and the API. Any Ubuntu machine with ports 80 and 443 open works the same way from step 2.

## 1. Create the server

Tencent Cloud console, Lighthouse, create an instance:

- **Region: Singapore.** Outside the Chinese mainland, so no ICP filing is needed to serve a website.
- **Image:** Ubuntu 24.04 LTS, or the Docker CE application image.
- **Plan:** at least 2 vCPU and 4 GB of memory (building the UI image needs about 2 GB).
- Once it is running, open the instance's **Firewall** tab and check that TCP **80** and **443** are allowed from anywhere; add them if not.

## 2. Log in and install Docker

The **Log in** button on the instance opens a terminal in the browser. Skip the install if `docker compose version` already prints a version (Docker CE image):

```bash
curl -fsSL https://get.docker.com | sudo sh
```

`git` ships with the Ubuntu image; if it is missing: `sudo apt-get install -y git`.

## 3. Get the code

While the repository is private, `git clone` asks for a login: your GitHub user name, and as the password a fine-grained personal access token limited to this repository with read-only Contents access (GitHub, Settings, Developer settings, Personal access tokens, Fine-grained tokens). A public repository needs no token.

```bash
git clone https://github.com/<you>/<repo>.git internal-brain
cd internal-brain
```

## 4. Settings

```bash
cp .env.example .env
nano .env        # save with Ctrl+O, Enter; exit with Ctrl+X
```

Fill in:

```bash
LLM_BASE_URL=https://tokenhub-intl.tencentcloudmaas.com/v1
LLM_API_KEY=...            # the TokenHub key; leave empty to run the offline stubs
DEMO_PASSWORD=...          # letters, digits and dashes (a $ needs 'single quotes'); user name: judge
SLACK_MODE=mock
```

Keep `SLACK_MODE=mock` on the judges' URL: the guided demo's revocation beats (4 and 5) use the admin panel's Slack controls, which are switched off while a real workspace is connected. `.env` stays on the server and is never committed.

## 5. Start

```bash
sudo docker compose up -d --build
```

The first build takes a few minutes. Open `http://<the server's public IP>/presenter`, sign in as `judge` with your password, and run beat 0 (Reset).

## 6. HTTPS with a name (recommended)

Over plain HTTP the browser sends the password unencrypted, and Slack only delivers events to HTTPS. A free name is enough:

1. At [duckdns.org](https://www.duckdns.org), sign in, add a subdomain (for example `internal-brain`) and set its IP to the server's public IP. A domain you own works the same way with an A record.
2. In `.env`: `SITE_ADDRESS=internal-brain.duckdns.org`
3. `sudo docker compose up -d`

Caddy obtains the certificate by itself within a minute: `https://internal-brain.duckdns.org/presenter`. Plain `http://` requests are redirected.

## Updating

```bash
cd internal-brain && git pull && sudo docker compose up -d --build
```

After editing `.env`, `sudo docker compose up -d` applies it. The index, the audit chain and its signing key live in a Docker volume and survive rebuilds.

## On the server during the demo

```bash
sudo docker compose exec api python -m internal_brain.audit.verify --db /data/brain.db     # the chain verifies
sudo docker compose exec api python scripts/tamper_demo.py --db /data/brain.db edit         # then verify: broken at seq N
sudo docker compose exec api python scripts/tamper_demo.py --db /data/brain.db restore
sudo docker compose exec api python scripts/smoke_llm.py                                    # the TokenHub model answers
```

## Real Slack on the server (optional)

Set `SLACK_MODE=real`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` and `SLACK_USER_MAP` in `.env`, and in the Slack app set Event Subscriptions, Request URL, to `https://<your name>/api/webhooks/slack` (step 6 first). That one path is outside the password: Slack cannot answer a password prompt, and the API rejects any request Slack did not sign or that is older than five minutes. With a real workspace, beats 4 and 5 happen in Slack itself: remove Jane's account from the channel, then ask again.

## What the password does and does not do

- The demo has no login of its own: identity is the user switcher, so anyone past the password can act as any demo user, admin and compliance included. The password is what keeps the public internet out; share it only in the submission form (URL, user `judge`, password).
- The mock platforms' own APIs (`/api/mock/...`) are not served publicly; the adapters reach them in-process.
- The interactive API docs are at `/api/docs` (`/docs` redirects there); their "Try it out" requests go through `/api` and carry the `X-User-Id` header you type.
- `/api/health` also stays open, for the hosting platform's health check; it reports the running modes, nothing from the documents.
- When judging is over, delete the instance (or, on Render, clear the password and the model key to leave an open demo that costs nothing).

## If something is off

- `sudo docker compose logs caddy`: the first line says whether the password prompt is on. Certificate errors land here too; usually the name does not point at the server yet, or port 80 or 443 is closed in the Lighthouse firewall.
- `sudo docker compose logs api`: model and Slack errors.
- The build stops while building the UI: the plan has too little memory. Add swap, then build again:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```
