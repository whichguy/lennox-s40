#!/usr/bin/env bash
# Hermetic checks for standalone lennox-s40 (no live thermostat required).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
skill="$root/skills/lennox-s40"
cli="$skill/scripts/lennox-s40"
py="$skill/scripts/lennox_s40.py"

fail() { printf 'lennox-s40.test.sh: FAIL %s\n' "$*" >&2; exit 1; }
pass() { printf '  ok %s\n' "$*"; }

[[ -f "$skill/SKILL.md" ]] || fail "missing SKILL.md"
[[ -f "$cli" ]] || fail "missing scripts/lennox-s40"
[[ -f "$py" ]] || fail "missing lennox_s40.py"
[[ -f "$root/.claude-plugin/plugin.json" ]] || fail "missing Claude plugin.json"
[[ -f "$root/install.sh" ]] || fail "missing install.sh"
grep -q '"name": "lennox-s40"' "$root/.claude-plugin/plugin.json" || fail "plugin name"
grep -q '^name: lennox-s40$' "$skill/SKILL.md" || fail "frontmatter name"
grep -q 'kind: script-backed' "$skill/SKILL.md" || fail "kind"
if grep -ERq '192\.168\.1\.148|BT23M53278|Sagewood' "$skill"; then
  fail "personal home identifiers must not ship in skill SoT"
fi
pass "package shape"

chmod +x "$cli" "$root/install.sh" 2>/dev/null || true
"$cli" --help >/dev/null || fail "--help"
pass "cli --help"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
export LENNOX_CONFIG="$tmpdir/config.json"
unset LENNOX_IP LENNOX_APP_ID || true

[[ "$("$cli" config path)" == "$LENNOX_CONFIG" ]] || fail "config path"
pass "config path"

"$cli" config show >/dev/null || fail "config show"
printf '%s\n' '{"version":1,"ip":"203.0.113.50"}' >"$LENNOX_CONFIG"
"$cli" config show | grep -q '203.0.113.50' || fail "seeded show"
"$cli" config clear >/dev/null
[[ ! -e "$LENNOX_CONFIG" ]] || fail "clear left file"
pass "config show/clear"

set +e
out="$(
  env -u LENNOX_IP LENNOX_CONFIG="$tmpdir/nope.json" LENNOX_NO_LAN_SCAN=1 \
    "$cli" --ip 203.0.113.1 --no-rediscover --no-lan-scan status 2>&1
)"
rc=$?
set -e
[[ "$rc" -ne 0 ]] || fail "dead ip must fail (out=$out)"
pass "fail-closed dead ip"

python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "$py" || fail "parse"
rm -rf "$(dirname "$py")/__pycache__"
pass "python syntax"

printf 'lennox-s40.test.sh: PASS\n'
