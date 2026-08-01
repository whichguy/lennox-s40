---
name: lennox-s40
description: >-
  Control a Lennox S40 (and S30/E30 LAN) smart thermostat over the local network:
  read status, set mode/setpoints/fan/away. Use when the user mentions home AC,
  heat, thermostat, Lennox Home, S40 zones, or HVAC setpoints. Local HTTPS only
  (no Lennox cloud). Requires LAN reachability and one-time scripts/lennox-s40 --setup.
version: 0.2.0
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

1. **One-time deps:** `bash scripts/lennox-s40 --setup`
2. **Find unit once:** `bash scripts/lennox-s40 discover` (writes running config)
3. **Read/write** without re-passing IP — config + auto-rediscover if IP dies
4. Prefer re-read `status` after writes if the user wants confirmation

## CLI contract

```sh
# From skill package root (skills/lennox-s40) or via installed symlink
bash scripts/lennox-s40 --setup
bash scripts/lennox-s40 discover          # saves ~/.config/lennox-s40/config.json
bash scripts/lennox-s40 status            # uses config; rediscovers if IP stale
bash scripts/lennox-s40 mode cool --zone Downstairs
bash scripts/lennox-s40 cool 76 --zone Downstairs
bash scripts/lennox-s40 config show
```

| Command | Effect |
|---------|--------|
| `discover` | mDNS + Connect probe; **persist** first good IP to config |
| `status` | JSON system + zones; updates config on success |
| `mode` / `cool` / `heat` / `fan` / `away` | control (same resolve path) |
| `hold on\|off\|status` | schedule hold (report/clear) |
| `config show\|path\|clear` | inspect or wipe running config |

**Resolve order:** `--ip` → `LENNOX_IP` → config → discover.  
**Stale IP:** Connect/session fail → rediscover (exclude bad IP) → retry (`--no-rediscover` to disable).
**LAN /24 scan:** opt-in via `--lan-scan` (default off).
**App id:** unique install-scoped id auto-generated (not shared library default).

Exit codes: `0` ok · `1` soft failure (discover miss) · `2` missing deps.

Env: `LENNOX_IP`, `LENNOX_APP_ID`, `LENNOX_CONFIG`, `LENNOX_VENV`, `LENNOX_PYTHON`, `LENNOX_TIMEOUT`.

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
