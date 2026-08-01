# lennox-s40

Standalone **portable skill** for local LAN control of a Lennox **S40** smart thermostat
(also S30/E30 local API). No Lennox cloud. No baked-in home IP.

Uses the community reverse-engineered client
[lennoxs30api](https://github.com/PeteRager/lennoxs30api).

## Layout

```text
.claude-plugin/plugin.json   # Claude Code plugin root (marketplace install)
skills/lennox-s40/           # agentskills body (skill-dir install)
  SKILL.md
  scripts/lennox-s40         # CLI wrapper
  scripts/lennox_s40.py
  requirements.txt
  references/
install.sh                   # multi-host skill-dir install
test/
```

## Quick start

```sh
cd ~/src/lennox-s40
./install.sh                          # Claude + Grok + Codex + Hermes
bash skills/lennox-s40/scripts/lennox-s40 --setup
bash skills/lennox-s40/scripts/lennox-s40 discover   # session-verified save → config
bash skills/lennox-s40/scripts/lennox-s40 status
```

Running config: `~/.config/lennox-s40/config.json`  
If the last IP dies, the CLI rediscovers (mDNS / serial host / optional /24 scan) and rewrites config after a verified session.

## CLI

| Command | Effect |
|---------|--------|
| `discover` | Probe LAN; persist after verified session (`--probe-only` to skip save) |
| `status` | System + zones JSON |
| `mode` / `cool` / `heat` / `fan` / `away` / `hold` | Control |
| `config show\|path\|clear` | Running config |

Exit codes: `0` ok · `1` unreachable · `2` missing deps · `3` bad args/config · `4` device · `5` timeout.

Env: `LENNOX_IP`, `LENNOX_APP_ID`, `LENNOX_CONFIG`, `LENNOX_VENV`, `LENNOX_NO_LAN_SCAN`.

## Install tracks

| Track | How |
|-------|-----|
| **Skill-dir (all hosts)** | `./install.sh` from this repo |
| **Claude plugin** | After GitHub publish + marketplace pin: `claude plugin install lennox-s40@skill-craft-market` (or add this repo as a marketplace source) |
| **skill-craft monorepo** | Not required — this skill is **standalone** |

### skill-craft install (optional)

If you keep [skill-craft](https://github.com/whichguy/skill-craft) around:

```sh
/path/to/skill-craft/install.sh --from ~/src/lennox-s40/skills/lennox-s40
```

## Marketplace pin shape (skill-craft-market)

Claude pins the **plugin root** of this repo (directory that contains `.claude-plugin/plugin.json`):

```json
{
  "name": "lennox-s40",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/whichguy/lennox-s40.git",
    "path": ".",
    "ref": "main"
  }
}
```

Until the GitHub remote exists, use skill-dir `./install.sh` only.

## Threat model

Local unauthenticated control of HVAC on a trusted LAN. See [SECURITY.md](SECURITY.md) and `skills/lennox-s40/references/ops.md`.

## Tests

```sh
python3 -m venv .venv && .venv/bin/pip install pytest ruff
PYTHONPATH=skills/lennox-s40/scripts .venv/bin/pytest tests -q
bash test/lennox-s40.test.sh
bash test/install.test.sh
```

## License

MIT
