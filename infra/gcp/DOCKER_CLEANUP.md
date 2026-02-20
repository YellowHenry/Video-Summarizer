# Docker Local Cleanup (After Cloud Deploy)

This note explains why Docker can consume a lot of local disk on Windows/WSL and how to clean it safely.

## Why Docker Uses So Much Space

Docker stores local data in its WSL disk (commonly `docker_data.vhdx`), including:
- image layers (`api`, `worker`, `web`)
- build cache (BuildKit / legacy builder)
- stopped containers
- unused volumes

Even if your app is deployed to Cloud Run, these local layers can remain and take many GB.

## Important

Cloud Run does **not** need your local images after push.  
Deleting local images does not break deployed Cloud Run services.

## Cleanup Steps (PowerShell)

1. If Docker is stuck/hanging, restart it first:

```powershell
wsl --shutdown
Get-Process | Where-Object { $_.ProcessName -match 'docker|com.docker' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

2. Remove this project's local images:

```powershell
docker rmi us-central1-docker.pkg.dev/tribal-primer-438802-n0/capstone-repo/audio-summarizer-api:latest
docker rmi us-central1-docker.pkg.dev/tribal-primer-438802-n0/capstone-repo/audio-summarizer-worker:latest
docker rmi us-central1-docker.pkg.dev/tribal-primer-438802-n0/capstone-repo/audio-summarizer-web:latest
```

3. Clear build cache:

```powershell
docker builder prune -af
```

4. Optional full cleanup of local Docker data:

```powershell
docker system prune -af --volumes
```

5. Check space usage:

```powershell
docker system df
```

## If Space Still Does Not Drop

Use Docker Desktop:
- `Troubleshoot` -> `Clean / Purge data`

This wipes local Docker data and usually reclaims the most space.
