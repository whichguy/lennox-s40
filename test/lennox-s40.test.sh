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

# version
v=$(run_py version)
[[ -n "$v" ]] || fail "version empty"
pass "version $v"

# dead IP without rediscover / lan scan
set +e
out="$(
  env -u LENNOX_IP LENNOX_CONFIG="$tmpdir/nope.json" LENNOX_NO_LAN_SCAN=1 \
    run_py --ip 203.0.113.1 --no-rediscover status 2>&1
)"
rc=$?
set -e
[[ "$rc" -ne 0 ]] || fail "dead ip must fail: $out"
pass "fail-closed dead ip (rc=$rc)"

# python parse
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "$py" || fail "parse"
rm -rf "$(dirname "$py")/__pycache__"
pass "python syntax"

# version parity
py_ver=$(python3 -c "import re; t=open('$py').read(); print(re.search(r'VERSION = \"([^\"]+)\"', t).group(1))")
skill_ver=$(python3 -c "import re; t=open('$skill/SKILL.md').read(); print(re.search(r'^version:\s*(\S+)', t, re.M).group(1))")
plugin_ver=$(python3 -c "import json; print(json.load(open('$root/.claude-plugin/plugin.json'))['version'])")
[[ "$py_ver" == "$skill_ver" && "$py_ver" == "$plugin_ver" ]] || fail "version drift $py_ver $skill_ver $plugin_ver"
pass "version parity $py_ver"

printf 'lennox-s40.test.sh: PASS\n'
