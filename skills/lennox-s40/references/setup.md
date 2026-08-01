# Setup — lennox-s40

## One-time

```sh
# From skills/lennox-s40 (repo clone or installed leaf)
bash scripts/lennox-s40 --setup

# Discover S40 on LAN (mDNS + Connect probe).
# Default: open a full session, then save identity to config.
# Probe only (no write): bash scripts/lennox-s40 discover --probe-only
bash scripts/lennox-s40 discover

# Later commands use config automatically (no LENNOX_IP required)
bash scripts/lennox-s40 status
```

## Running config

Persisted JSON (mode `0600`):

| Path | When |
|------|------|
| `$LENNOX_CONFIG` | if set |
| `$XDG_CONFIG_HOME/lennox-s40/config.json` | else if XDG set |
| `~/.config/lennox-s40/config.json` | default |

Typical fields: `ip`, `host` (mDNS), `app_id`, `serial`, `last_ok_at`, `updated_at`.

Missing config is fine (empty defaults). **Existing but malformed JSON fails closed** (exit 3; file preserved).

```sh
bash scripts/lennox-s40 config show
bash scripts/lennox-s40 config path
bash scripts/lennox-s40 config clear
```

### Address resolution

1. `--ip` (explicit)
2. `LENNOX_IP` env
3. config `ip` / `host`
4. mDNS + Connect probe

If the chosen address **fails Connect**, the CLI **rediscovers** (unless `--no-rediscover`) and continues with the new IP. Config is rewritten only after a **verified session** (or successful control command), not from Connect probe alone.

Optional LAN /24 Connect sweep: `--lan-scan` (default off; honor `LENNOX_NO_LAN_SCAN=1`).

Optional: reserve DHCP for the thermostat MAC on your router so IP churn is rare.

## Requirements

- macOS or Linux on the **same network** as the S40 (guest/IoT isolation breaks local API)
- Python 3.10+
- Outbound HTTPS to thermostat IP port **443** (self-signed cert; client disables verify)
- Package: [lennoxs30api](https://github.com/PeteRager/lennoxs30api) (installed by `--setup`)

## S40 notes

| Item | Detail |
|------|--------|
| Local API | HTTPS `/Endpoints/<app_id>/Connect` → 204 |
| Cloud | Not used (S40 community cloud path unsupported) |
| Firmware | Prefer ≥ 04.25.0070 if Connect fails |
| app_id | Unique per client; colliding ids steal the message queue |
| Multi-zone | `--zone Name` or `--zone 0` |

## Security

- Prefer a unique `LENNOX_APP_ID` for this skill vs the phone app.
- Do not log raw `interfaces` payloads (may include network credentials).
- Keep control on a trusted LAN; this is unauthenticated local control by design.
