#!/usr/bin/env bash
# Hermetic package smoke (no live thermostat, no venv required for config/*).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
skill="$root/skills/lennox-s40"
cli="$skill/scripts/lennox-s40"
py="$skill/scripts/lennox_s40.py"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }
pass() { printf '  ok %s\n' "$*"; }

[[ -f "$skill/SKILL.md" ]] || fail "SKILL.md"
[[ -f "$root/.claude-plugin/plugin.json" ]] || fail "plugin.json"
[[ -f "$root/install.sh" ]] || fail "install.sh"
grep -q '"name": "lennox-s40"' "$root/.claude-plugin/plugin.json" || fail "plugin name"
grep -q '^name: lennox-s40$' "$skill/SKILL.md" || fail "frontmatter"
if grep -ERq '192\.168\.1\.148|BT23M53278|Sagewood' "$skill"; then
  fail "personal identifiers in SoT"
fi
pass "package shape"

chmod +x "$cli" "$root/install.sh" 2>/dev/null || true
"$cli" --help >/dev/null || fail "help"
pass "cli --help"

# config without venv (lazy import)
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
export LENNOX_CONFIG="$tmpdir/config.json"
unset LENNOX_IP LENNOX_APP_ID || true

# Use system python for config commands — wrapper may pick empty venv
export LENNOX_PYTHON="${LENNOX_PYTHON:-$(command -v python3)}"
# Force direct python invocation for hermetic config tests
run_py() { "$LENNOX_PYTHON" "$py" "$@"; }

[[ "$(run_py config path)" == "$LENNOX_CONFIG" ]] || fail "config path"
pass "config path"

run_py config show >/dev/null || fail "config show empty"
printf '%s\n' '{"version":1,"ip":"203.0.113.50"}' >"$LENNOX_CONFIG"
run_py config show | grep -q '203.0.113.50' || fail "seeded show"
run_py config clear >/dev/null
[[ ! -e "$LENNOX_CONFIG" ]] || fail "clear left file"
pass "config show/clear"

# version (module path)
v=$(run_py version)
[[ -n "$v" ]] || fail "version empty"
pass "version $v"

# --- Wrapper dep-free path: empty LENNOX_VENV must still run version/config ---
empty_venv="$tmpdir/empty-venv-dir"
mkdir -p "$empty_venv"
# Isolate from ambient LENNOX_PYTHON so bootstrap must use system python3
set +e
wrap_ver="$(
  env -u LENNOX_PYTHON -u LENNOX_IP \
    LENNOX_VENV="$empty_venv" \
    LENNOX_CONFIG="$tmpdir/wrap-config.json" \
    "$cli" version 2>&1
)"
wrap_ver_rc=$?
wrap_path="$(
  env -u LENNOX_PYTHON -u LENNOX_IP \
    LENNOX_VENV="$empty_venv" \
    LENNOX_CONFIG="$tmpdir/wrap-config.json" \
    "$cli" config path 2>&1
)"
wrap_path_rc=$?
wrap_help="$(
  env -u LENNOX_PYTHON \
    LENNOX_VENV="$empty_venv" \
    "$cli" --help 2>&1
)"
wrap_help_rc=$?
set -e
[[ "$wrap_ver_rc" -eq 0 ]] || fail "wrapper version empty-VENV rc=$wrap_ver_rc: $wrap_ver"
[[ -n "$wrap_ver" ]] || fail "wrapper version empty-VENV empty output"
[[ "$wrap_path_rc" -eq 0 ]] || fail "wrapper config path empty-VENV rc=$wrap_path_rc: $wrap_path"
[[ "$wrap_path" == "$tmpdir/wrap-config.json" ]] || fail "wrapper config path mismatch: $wrap_path"
[[ "$wrap_help_rc" -eq 0 ]] || fail "wrapper --help empty-VENV rc=$wrap_help_rc"
pass "wrapper dep-free empty LENNOX_VENV (version/config/help)"

# Network path still needs deps — empty venv without library → EX_DEPS=2
set +e
wrap_status_out="$(
  env -u LENNOX_PYTHON -u LENNOX_IP \
    LENNOX_VENV="$empty_venv" \
    LENNOX_CONFIG="$tmpdir/nope.json" \
    "$cli" --ip 203.0.113.1 --no-rediscover status 2>&1
)"
wrap_status_rc=$?
set -e
[[ "$wrap_status_rc" -eq 2 ]] || fail "wrapper status empty-VENV must be EX_DEPS=2, got $wrap_status_rc: $wrap_status_out"
pass "wrapper empty-VENV network path EX_DEPS (rc=2)"

# dead IP without rediscover / lan scan — must drive real CLI and assert EX_NOT_FOUND=1
# (do not use `env run_py`: env cannot invoke shell functions → false 127)
set +e
out="$(
  env -u LENNOX_IP LENNOX_NO_LAN_SCAN=1 LENNOX_CONFIG="$tmpdir/nope.json" \
    "$LENNOX_PYTHON" "$py" --ip 203.0.113.1 --no-rediscover status 2>&1
)"
rc=$?
set -e
[[ "$rc" -eq 1 ]] || fail "dead ip must exit 1 (EX_NOT_FOUND), got $rc: $out"
# Exact diagnostic (AC3) — not a loose non-zero / alternate message
printf '%s\n' "$out" | grep -F 'Connect failed for 203.0.113.1' \
  || fail "dead ip exact message missing: $out"
pass "fail-closed dead ip (rc=$rc, Connect failed for 203.0.113.1)"

# malformed config: EX_BAD_REQ=3, path on stderr, file preserved (sha256 stable)
bad_cfg="$tmpdir/bad.json"
printf '%s' '{not-json' >"$bad_cfg"
sha_before=$(shasum -a 256 "$bad_cfg" | awk '{print $1}')
set +e
bad_out="$(
  env LENNOX_CONFIG="$bad_cfg" "$LENNOX_PYTHON" "$py" config show 2>&1
)"
bad_rc=$?
set -e
sha_after=$(shasum -a 256 "$bad_cfg" | awk '{print $1}')
[[ "$bad_rc" -eq 3 ]] || fail "malformed config must exit 3 (EX_BAD_REQ), got $bad_rc: $bad_out"
[[ "$sha_before" == "$sha_after" ]] || fail "malformed config file mutated ($sha_before -> $sha_after)"
printf '%s\n' "$bad_out" | grep -F "$bad_cfg" \
  || fail "malformed config stderr must name path $bad_cfg: $bad_out"
pass "malformed config fail-closed (rc=3, path on stderr, sha256 preserved)"

# python parse
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "$py" || fail "parse"
rm -rf "$(dirname "$py")/__pycache__"
pass "python syntax"

# version parity (four surfaces)
py_ver=$(python3 -c "import re; t=open('$py').read(); print(re.search(r'VERSION = \"([^\"]+)\"', t).group(1))")
skill_ver=$(python3 -c "import re; t=open('$skill/SKILL.md').read(); print(re.search(r'^version:\s*(\S+)', t, re.M).group(1))")
plugin_ver=$(python3 -c "import json; print(json.load(open('$root/.claude-plugin/plugin.json'))['version'])")
pyproject_ver=$(python3 -c "import re; t=open('$root/pyproject.toml').read(); print(re.search(r'^version = \"([^\"]+)\"', t, re.M).group(1))")
[[ "$py_ver" == "$skill_ver" && "$py_ver" == "$plugin_ver" && "$py_ver" == "$pyproject_ver" ]] \
  || fail "version drift $py_ver $skill_ver $plugin_ver $pyproject_ver"
pass "version parity $py_ver"

printf 'lennox-s40.test.sh: PASS\n'
