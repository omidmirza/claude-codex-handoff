# install.ps1: install the handoff skill once, for Claude Code and Codex together (Windows).
#   powershell -ExecutionPolicy Bypass -File install.ps1                   every project
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Project FOLDER   one project (run it again after moving
#                                                                          the folder: a junction holds a full path)
# Claude Code reads .claude\skills, Codex reads .agents\skills; a junction (a folder link, no admin rights needed)
# makes both read the same folder. Run it again to update. Then start a new chat and say /handoff in Claude Code
# or $handoff in Codex. The same steps as install.sh.
param([string]$Project)
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "handoff"
if ($Project) {
  $root = (Resolve-Path -LiteralPath $Project).Path
  $claudeDir = Join-Path $root ".claude\skills"
  $codexDir = Join-Path $root ".agents\skills"
} else {
  $claudeHome = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME ".claude" }
  $claudeDir = Join-Path $claudeHome "skills"
  $codexDir = Join-Path $HOME ".agents\skills"
}
$py = $null
foreach ($c in "py", "python") {   # not python3: Windows has it only as a Store shortcut
  try { & $c --version *> $null; if ($LASTEXITCODE -eq 0) { $py = $c; break } } catch {}
}
if (-not $py) {
  Write-Host "install.ps1: Python is missing. Install it from python.org, then run this again."
  exit 1
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
  Write-Host "install.ps1: note: the codex command is not on the PATH; the handoff needs it"
}
$dest = Join-Path $claudeDir "handoff"
$link = Join-Path $codexDir "handoff"
$old = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue   # does not follow the link
if ($old -and -not $old.LinkType) {
  Write-Host "install.ps1: $link is a folder, not a link (a copy from an import?)."
  Write-Host "             Move it away and run this again."
  exit 1
}
New-Item -ItemType Directory -Force -Path $claudeDir, $codexDir | Out-Null
$new = "$dest.new"
if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force }
Copy-Item -LiteralPath $src -Destination $new -Recurse
$cache = Join-Path $new "scripts\__pycache__"
if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache -Recurse -Force }
if ($old) { $old.Delete() }   # removes the old link only, never the folder it points to
if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
Move-Item -LiteralPath $new -Destination $dest
New-Item -ItemType Junction -Path $link -Target $dest | Out-Null
function Short($p) { if ($p.StartsWith($HOME)) { "~" + $p.Substring($HOME.Length) } else { $p } }
Write-Host "Installed handoff"
Write-Host "  Claude Code reads  $(Short $dest)"
Write-Host "  Codex reads        $(Short $link)  (a link to the same folder)"
Write-Host "Start a new chat. Say /handoff in Claude Code, or `$handoff in Codex."
