#!/usr/bin/env bash
# install.sh: install the handoff skill once, for Claude Code and Codex together.
#   bash install.sh                  every project: ~/.claude/skills/handoff, and a link to it in ~/.agents/skills
#   bash install.sh --project DIR    one project:   DIR/.claude/skills/handoff, and a link in DIR/.agents/skills
# Claude Code reads .claude/skills, Codex reads .agents/skills; the link makes both read the same folder.
# Run it again to update. Then start a new chat and say /handoff in Claude Code or $handoff in Codex.
set -euo pipefail
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*)   # Git Bash on Windows: no real links here, and no python3
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && (pwd -W 2>/dev/null || pwd))"
  echo "install.sh: on Windows, run install.ps1 in PowerShell instead:"
  echo "  powershell -ExecutionPolicy Bypass -File \"$dir/install.ps1\""
  exit 1;;
esac
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/handoff"
if [ "${1:-}" = "--project" ]; then
  root="$(cd "${2:?usage: bash install.sh --project FOLDER}" && pwd)"
  claude_dir="$root/.claude/skills"; codex_dir="$root/.agents/skills"; link="../../.claude/skills/handoff"
else
  claude_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"; codex_dir="$HOME/.agents/skills"; link="$claude_dir/handoff"
fi
command -v python3 >/dev/null || { echo "install.sh: python3 is missing"; exit 1; }
command -v codex >/dev/null || echo "install.sh: note: the codex command is not on the PATH; the handoff needs it"
if [ -e "$codex_dir/handoff" ] && [ ! -L "$codex_dir/handoff" ]; then
  echo "install.sh: $codex_dir/handoff is a folder, not a link (a copy from an import?)."
  echo "            Move it away and run this again."
  exit 1
fi
mkdir -p "$claude_dir" "$codex_dir"
rm -rf "$claude_dir/handoff.new"
cp -R "$src" "$claude_dir/handoff.new"
rm -rf "$claude_dir/handoff.new/scripts/__pycache__"
rm -rf "$claude_dir/handoff"
mv "$claude_dir/handoff.new" "$claude_dir/handoff"
ln -sfn "$link" "$codex_dir/handoff"
short() { case "$1" in "$HOME"/*) echo "~${1#"$HOME"}";; *) echo "$1";; esac; }
echo "Installed handoff"
echo "  Claude Code reads  $(short "$claude_dir/handoff")"
echo "  Codex reads        $(short "$codex_dir/handoff")  (a link to the same folder)"
echo "Start a new chat. Say /handoff in Claude Code, or \$handoff in Codex."
