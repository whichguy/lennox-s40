---
name: lennox-s40
description: >-
  Control a Lennox S40 (and S30/E30 LAN) smart thermostat over the local network:
  read status, set mode/setpoints/fan/away. Use when the user mentions home AC,
  heat, thermostat, Lennox Home, S40 zones, or HVAC setpoints. Local HTTPS only
  (no Lennox cloud). Requires LAN reachability and one-time scripts/lennox-s40 --setup.
version: 0.2.1
license: MIT
platforms:
  - linux
  - macos
metadata:
  skill_craft:
    kind: script-backed
  hermes:
    category: smart-home
    tags:
      - lennox
      - s40
      - thermostat
      - hvac
      - smart-home
      - local-api
---

# lennox-s40

**Script-backed** skill for local LAN control of Lennox **S40** (also S30/E30 local).
Uses the community reverse-engineered API ([lennoxs30api](https://github.com/PeteRager/lennoxs30api)).
No cloud password. No baked-in home IP — set `LENNOX_IP` after `discover`.

## When to use

- Read or change home HVAC temperature, mode, fan, or away on a Lennox S40
- “What’s the thermostat at?” / “Set downstairs cool to 76”
- Debug local connectivity to the S40

## When not to use

- Other HVAC brands (use their integrations)
- Controlling devices you do not own / non-local networks
- Expecting Lennox cloud-only M30 path without local API

## Procedure

1. **One-time deps:** `bash scripts/lennox-s40 --setup` (or installed path below)
2. **Find unit once:** `lennox-s40 discover` (session-verified save to running config)
3. **Read/write** without re-passing IP — config + auto-rediscover if IP dies
4. Prefer re-read `status` after writes if the user wants confirmation

**Installed path (cwd-independent):** after `./install.sh`, call  
`~/.claude/skills/lennox-s40/scripts/lennox-s40` (or Grok/Codex/Hermes skill leaf) — works from any working directory.  
`bash scripts/lennox-s40 …` is for repo/skill-leaf cwd only.

## CLI contract

```sh
# From skill package root (skills/lennox-s40) or via installed symlink
bash scripts/lennox-s40 --setup
bash scripts/lennox-s40 discover          # session-verified save → config
bash scripts/lennox-s40 status            # uses config; rediscovers if IP stale
bash scripts/lennox-s40 mode cool --zone Downstairs
bash scripts/lennox-s40 cool 76 --zone Downstairs
bash scripts/lennox-s40 config show
bash scripts/lennox-s40 --version
bash scripts/lennox-s40 --app-id mapp… status   # optional client queue id
```

| Command | Effect |
|---------|--------|
| `discover` | mDNS + Connect probe; **persist only after a verified session** (identity proven). Use `discover --probe-only` to print matches without writing config |
| `status` | JSON system + zones; updates config on success. When not ready within deadline, still prints JSON with `"ready": false` and exits **5** |
| `mode` / `cool` / `heat` / `fan` / `away` | control (same resolve path) |
| `hold on\|off\|status` | schedule hold (report/clear) |
| `config show\|path\|clear` | inspect or wipe running config |
| `version` / `--version` | print CLI version and exit 0 |

**Resolve order:** `--ip` → `LENNOX_IP` → config → discover.  
**Stale IP:** Connect/session fail → rediscover (exclude bad IP) → retry (`--no-rediscover` to disable).  
**LAN /24 scan:** opt-in via `--lan-scan` (default off); suppressed when `LENNOX_NO_LAN_SCAN=1`.  
**Multi-zone writes:** pass `--zone NAME` or `--first-zone`.  
**Diagnostics:** `--full` on status (less redaction).  
**App id:** unique install-scoped id auto-generated; override with `--app-id` or `LENNOX_APP_ID`.  
**Malformed config:** existing but invalid JSON → exit **3**; file left byte-identical (no silent reset).

Exit codes: `0` ok · `1` not found / unreachable · `2` missing deps · `3` bad request (args/config) · `4` device/protocol · `5` timeout / not ready.

Env: `LENNOX_IP`, `LENNOX_APP_ID`, `LENNOX_CONFIG`, `LENNOX_VENV`, `LENNOX_PYTHON`, `LENNOX_TIMEOUT`, `LENNOX_NO_LAN_SCAN`.

## Protocol (local)

```text
POST https://<ip>/Endpoints/<app_id>/Connect     → 204
POST https://<ip>/Messages/RequestData
GET  https://<ip>/Messages/<app_id>/Retrieve     (long-poll)
POST https://<ip>/Messages/Publish
```

Self-signed TLS (`CN=Lennox`). No Authorization header on LAN.

## Install (standalone repo)

```sh
# From repo root (~/src/lennox-s40)
./install.sh
# optional: --claude-only | --grok-only | --codex-only | --hermes-only

# Claude plugin (after GitHub publish + marketplace pin)
# claude plugin install lennox-s40@skill-craft-market
```

See [references/host-matrix.md](references/host-matrix.md) and [references/setup.md](references/setup.md).

## Upstream

- Python API: https://github.com/PeteRager/lennoxs30api  
- Home Assistant: https://github.com/PeteRager/lennoxs30  
- TypeScript: https://github.com/lukealonso/lennoxapi  
