#!/usr/bin/env bash
# Install lennox-s40 skill-dir into Claude / Grok / Codex / Hermes.
# Repo root is a Claude plugin package; skill body is skills/lennox-s40.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source_dir="$root/skills/lennox-s40"
leaf="lennox-s40"

usage() {
  cat <<EOF
Usage: ./install.sh [--claude-only|--grok-only|--codex-only|--hermes-only]
                    [--status] [--uninstall] [--dry-run] [--relink]

Installs skills/lennox-s40 into host skill homes:
  Claude  ~/.claude/skills/lennox-s40   (symlink)
  Grok    ~/.grok/skills/lennox-s40     (symlink)
  Codex   ~/.codex/skills/lennox-s40    (symlink)
  Hermes  ~/.hermes/skills/software-development/lennox-s40  (copy)

Also: bash skills/lennox-s40/scripts/lennox-s40 --setup
EOF
}

dry_run=0
relink=0
do_status=0
do_uninstall=0
install_claude=1
install_grok=1
install_codex=1
install_hermes=1

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --dry-run) dry_run=1 ;;
    --relink) relink=1 ;;
    --status) do_status=1 ;;
    --uninstall) do_uninstall=1 ;;
    --claude-only) install_grok=0; install_codex=0; install_hermes=0 ;;
    --grok-only) install_claude=0; install_codex=0; install_hermes=0 ;;
    --codex-only) install_claude=0; install_grok=0; install_hermes=0 ;;
    --hermes-only) install_claude=0; install_grok=0; install_codex=0 ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; usage; exit 2 ;;
  esac
done

[[ -f "$source_dir/SKILL.md" ]] || {
  printf 'missing %s/SKILL.md\n' "$source_dir" >&2
  exit 1
}

run() {
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'DRY %s\n' "$*"
  else
    "$@"
  fi
}

install_symlink() {
  local label="$1" dest_parent="$2"
  local dest="$dest_parent/$leaf"
  mkdir -p "$dest_parent"
  if [[ -L "$dest" ]]; then
    local cur
    cur="$(readlink "$dest" || true)"
    if [[ "$cur" == "$source_dir" ]]; then
      printf 'Already installed (%s): %s\n' "$label" "$dest"
      return 0
    fi
    if [[ "$relink" -eq 1 ]]; then
      run rm -f "$dest"
    else
      printf 'Skipped (wrong symlink, use --relink) (%s): %s -> %s\n' "$label" "$dest" "$cur"
      return 0
    fi
  elif [[ -e "$dest" ]]; then
    printf 'Skipped (foreign real path) (%s): %s\n' "$label" "$dest"
    return 0
  fi
  run ln -s "$source_dir" "$dest"
  printf 'Installed (%s): %s -> %s\n' "$label" "$dest" "$source_dir"
}

install_hermes_copy() {
  local dest_parent="$HOME/.hermes/skills/software-development"
  local dest="$dest_parent/$leaf"
  local marker_dir="$dest_parent/.skill-craft"
  local marker="$marker_dir/${leaf}.json"
  mkdir -p "$dest_parent" "$marker_dir"
  if [[ -e "$dest" && ! -f "$marker" && ! -L "$dest" ]]; then
    printf 'Skipped (foreign Hermes tree): %s\n' "$dest"
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'DRY rsync hermes copy %s\n' "$dest"
    return 0
  fi
  rm -rf "$dest"
  mkdir -p "$dest"
  # portable copy
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' --exclude '.git' "$source_dir/" "$dest/"
  else
    cp -a "$source_dir/." "$dest/"
  fi
  MARKER_PATH="$marker" SOURCE_DIR="$source_dir" REPO_ROOT="$root" LEAF_NAME="$leaf" \
    python3 - <<'PY'
import json, os, time
from pathlib import Path
marker = Path(os.environ["MARKER_PATH"])
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text(json.dumps({
  "schema": 2,
  "leaf": os.environ["LEAF_NAME"],
  "source": os.environ["SOURCE_DIR"],
  "repo": os.environ["REPO_ROOT"],
  "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
  "managed_by": "lennox-s40/install.sh",
}, indent=2) + "\n")
PY
  printf 'Installed (Hermes skillhub / %s): %s (copy)\n' "$leaf" "$dest"
}

status_one() {
  local label="$1" path="$2"
  if [[ -L "$path" ]]; then
    printf 'status (%s): symlink -> %s\n' "$label" "$(readlink "$path")"
  elif [[ -d "$path" ]]; then
    printf 'status (%s): directory %s\n' "$label" "$path"
  else
    printf 'status (%s): absent\n' "$label"
  fi
}

uninstall_one() {
  local label="$1" path="$2"
  if [[ -L "$path" ]]; then
    local cur
    cur="$(readlink "$path" || true)"
    if [[ "$cur" == "$source_dir" ]]; then
      run rm -f "$path"
      printf 'Uninstalled (%s): %s\n' "$label" "$path"
    else
      printf 'Skipped uninstall (not ours) (%s): %s\n' "$label" "$path"
    fi
  elif [[ -d "$path" ]]; then
    # hermes managed only if marker
    local marker="$HOME/.hermes/skills/software-development/.skill-craft/${leaf}.json"
    if [[ "$path" == "$HOME/.hermes/skills/software-development/$leaf" && -f "$marker" ]]; then
      run rm -rf "$path" "$marker"
      printf 'Uninstalled (%s): %s\n' "$label" "$path"
    else
      printf 'Skipped uninstall (foreign) (%s): %s\n' "$label" "$path"
    fi
  fi
}

if [[ "$do_status" -eq 1 ]]; then
  [[ "$install_claude" -eq 1 ]] && status_one "Claude" "$HOME/.claude/skills/$leaf"
  [[ "$install_grok" -eq 1 ]] && status_one "Grok" "$HOME/.grok/skills/$leaf"
  [[ "$install_codex" -eq 1 ]] && status_one "Codex" "$HOME/.codex/skills/$leaf"
  [[ "$install_hermes" -eq 1 ]] && status_one "Hermes" "$HOME/.hermes/skills/software-development/$leaf"
  exit 0
fi

if [[ "$do_uninstall" -eq 1 ]]; then
  [[ "$install_claude" -eq 1 ]] && uninstall_one "Claude" "$HOME/.claude/skills/$leaf"
  [[ "$install_grok" -eq 1 ]] && uninstall_one "Grok" "$HOME/.grok/skills/$leaf"
  [[ "$install_codex" -eq 1 ]] && uninstall_one "Codex" "$HOME/.codex/skills/$leaf"
  [[ "$install_hermes" -eq 1 ]] && uninstall_one "Hermes" "$HOME/.hermes/skills/software-development/$leaf"
  exit 0
fi

[[ "$install_claude" -eq 1 ]] && install_symlink "Claude Code / $leaf" "$HOME/.claude/skills"
[[ "$install_grok" -eq 1 ]] && install_symlink "Grok / $leaf" "$HOME/.grok/skills"
[[ "$install_codex" -eq 1 ]] && install_symlink "Codex / $leaf" "$HOME/.codex/skills"
[[ "$install_hermes" -eq 1 ]] && install_hermes_copy
