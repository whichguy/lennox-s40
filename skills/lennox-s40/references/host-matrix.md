# Host matrix — lennox-s40

| Host | Discovery (skill card) | Execution |
|------|------------------------|-----------|
| Claude Code | yes (`~/.claude/skills/lennox-s40`) | yes if LAN + `scripts/lennox-s40 --setup` |
| Grok | yes | same |
| Codex | yes | same |
| Hermes | yes (`~/.hermes/skills/software-development/lennox-s40` via install.sh) | yes if container/host can reach home LAN (often needs host network / VPN) |

**Exit codes (0–5):** see `SKILL.md` (SoT). Summary: `0` ok · `1` not found · `2` deps · `3` bad request/config · `4` device · `5` timeout.

**Dep-free vs network:** `version`, `--version`, `--help`, and `config *` run without `lennoxs30api`. Control commands need `--setup` / importable deps.

Install layout is covered by `test/install.test.sh` (claude symlink, hermes dry-run, foreign-path skip, codex relink, uninstall) — not a full four-host live default-install matrix.

## Binding surfaces (do not collapse)

| Surface | Env / default |
|---------|----------------|
| Thermostat address | `--ip` → `LENNOX_IP` → **running config** → mDNS discover |
| Running config | `LENNOX_CONFIG` or `~/.config/lennox-s40/config.json` |
| Client queue id | `--app-id` / config `app_id` / `LENNOX_APP_ID` (unique per concurrent client) |
| Python + deps | `LENNOX_VENV` or `~/.local/share/lennox-s40/venv` via `scripts/lennox-s40 --setup` |
| Package root | skill leaf containing `SKILL.md` |

## Honesty

- **S40 is local-only** for the community API path (no cloud login in this skill).
- Execution requires **same LAN (or routed access)** to the thermostat HTTPS port 443.
- Hermes skill-dir install always materializes under `software-development/` (install.sh contract), even though the domain is smart-home.
