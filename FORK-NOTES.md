# Hermes WebUI — skyeyesec333 fork

Personal fork of [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui)
(MIT licensed). This repo is the backup + development home for my self-hosted
Hermes Web UI instance.

## Why this fork exists
- Backs up my local modifications to Hermes WebUI.
- Base for custom features I plan to add:
  - **Business card ingestion** (scan/photo → contact capture → CRM/vault).
  - **Kanban board for agent tasking** (note: upstream already has active
    kanban work on branch `gate-rebase/5421-kanban-wake` — I'll track or
    rebase onto that rather than duplicate).
- Upstream (nesquena/hermes-webui) is added as `upstream` remote so I can pull
  fixes forward.

## Deployment (my home server)
- Runs as a systemd **user** service: `hermes-webui.service`.
- Binds `0.0.0.0:8787`. Auth via `HERMES_WEBUI_PASSWORD` (in repo `.env`,
  which is gitignored — never committed).
- Reachable locally at http://127.0.0.1:8787 and over Tailscale at
  http://100.120.12.98:8787 (macbook).
- Reads Hermes config from `~/.hermes/config.yaml` (model: tencent/hy3:free
  via nous; no fallback providers).
- State dir: `~/.hermes/webui`.

## Keeping in sync
- Pull upstream: `git fetch upstream && git merge upstream/master`
- Push my changes: `git push origin master`

## Dev notes
- Honor the repo's `AGENTS.md` contracts (one logical change per PR, read
  `ARCHITECTURE.md`/`docs/CONTRACTS.md` before editing, use `./scripts/test.sh`
  for tests).
- Prefer vanilla JS + Python, no build step / no new frameworks without
  justification.
