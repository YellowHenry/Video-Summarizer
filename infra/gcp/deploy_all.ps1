$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "deploy_all.sh"

if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
  throw "bash is required in PATH (Git Bash or WSL)."
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "gcloud is required in PATH."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "docker is required in PATH."
}

bash $scriptPath
