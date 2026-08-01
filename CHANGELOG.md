# Changelog

## 0.2.0 — 2026-08-01

### Security / correctness
- Lazy-import `lennoxs30api` so `config` works without venv
- Unique install-scoped `app_id` (migrate off shared library default)
- CLI `--app-id` takes precedence over config
- Config save: unique temp file, `0600` at create, fsync, flock
- Probe prefers Lennox TLS CN; LAN /24 scan opt-in only (`--lan-scan`)
- Rediscover on session/connect failure; exclude failed IP
- Clear/replace stale host when identity changes
- Schedule **hold** command; report `schedule_hold_created` on setpoints
- Write validation: finite temps, away on|off only, multi-zone requires `--zone`
- Single-setpoint mode uses `r_sp`
- Verify-after-write; readiness deadline (exit 5 if not ready)
- Status redaction by default (`--full` for diagnostics)

### Install / SDLC
- `install.sh`: exit 0 for `--*-only`; dry-run creates no dirs; Hermes marker provenance
- Unit tests (`tests/`), install contract tests, package smoke tests
- GitHub Actions CI
- Pinned `requirements.txt`, `pyproject.toml`, `SECURITY.md`, `ops.md`

## 0.1.0 — 2026-08-01

- Initial standalone skill: discover, status, mode/cool/heat/fan/away, running config, multi-host install, Claude plugin root
