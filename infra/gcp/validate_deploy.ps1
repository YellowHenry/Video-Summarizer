$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "validate_deploy.sh"

if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
  throw "bash is required in PATH (Git Bash or WSL)."
}

bash $scriptPath
