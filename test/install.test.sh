#!/usr/bin/env bash
# Installer contract tests in a sandbox HOME.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
install="$root/install.sh"
chmod +x "$install"

fail() { printf 'install.test FAIL %s\n' "$*" >&2; exit 1; }
pass() { printf '  ok %s\n' "$*"; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
export HOME="$tmpdir"

# --claude-only must exit 0
"$install" --claude-only
rc=$?
[[ $rc -eq 0 ]] || fail "claude-only exit $rc"
[[ -L "$HOME/.claude/skills/lennox-s40" ]] || fail "claude symlink missing"
pass "claude-only install exit 0"

# dry-run must not create hermes dirs when only hermes selected with dry-run from clean
rm -rf "$HOME/.hermes"
HOME2="$(mktemp -d)"
HOME="$HOME2" "$install" --hermes-only --dry-run
[[ ! -e "$HOME2/.hermes" ]] || fail "dry-run created hermes tree"
pass "dry-run no hermes mutation"
rm -rf "$HOME2"
export HOME="$tmpdir"

# foreign real dest skipped
rm -f "$HOME/.grok/skills/lennox-s40"
mkdir -p "$HOME/.grok/skills/lennox-s40"
echo foreign >"$HOME/.grok/skills/lennox-s40/SKILL.md"
"$install" --grok-only
[[ -f "$HOME/.grok/skills/lennox-s40/SKILL.md" ]] || fail "foreign wiped"
grep -q foreign "$HOME/.grok/skills/lennox-s40/SKILL.md" || fail "foreign changed"
pass "foreign real skipped"

# wrong symlink + relink
rm -rf "$HOME/.codex/skills"
mkdir -p "$HOME/.codex/skills"
ln -s /nonexistent/path "$HOME/.codex/skills/lennox-s40"
"$install" --codex-only --relink
cur=$(readlink "$HOME/.codex/skills/lennox-s40")
[[ "$cur" == "$root/skills/lennox-s40" ]] || fail "relink failed: $cur"
pass "relink"

# uninstall owned symlink
"$install" --claude-only --uninstall
[[ ! -e "$HOME/.claude/skills/lennox-s40" ]] || fail "uninstall left claude link"
pass "uninstall"

printf 'install.test.sh: PASS\n'
