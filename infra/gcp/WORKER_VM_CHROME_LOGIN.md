# Worker VM Chrome Login Guide (for yt-dlp browser cookies)

This guide is the exact flow for:
- setting the VM user's password
- connecting over RDP (`port 3389`)
- signing into YouTube in Chrome once
- verifying the Chrome profile exists for `--cookies-from-browser`

It assumes:
- worker runtime is `compute_engine`
- VM name is `audio-summarizer-worker-vm`
- zone is `us-central1-a`
- VM user is `worker`

If your names are different, replace them in commands.

## How to verify these values in Cloud Console

Use this when you want to confirm the guide assumptions directly in the Google Cloud UI.

1. Open Google Cloud Console and select your project.
2. Go to `Compute Engine` -> `VM instances`.
3. In the VM table, find your worker VM.
- `Name` column = VM name (example: `audio-summarizer-worker-vm`).
4. Click the VM name to open details.
- `Location` row = zone (example: `us-central1-a`)
- Confirm it is `Running`
- Confirm `External IP` is present (used for RDP)
5. Verify worker Linux user from Cloud Console:
- Back on VM instances page, click `SSH` on that VM (opens browser SSH)
- In the SSH terminal, run:
```bash
id worker
```
- If you get a `uid=...` line, the `worker` user exists.

If the VM name or zone in Console is different from the guide examples, replace those values in all commands below.

## 1) Confirm VM and get public IP

Run in PowerShell:

```powershell
gcloud compute instances describe audio-summarizer-worker-vm `
  --zone us-central1-a `
  --format="get(networkInterfaces[0].accessConfigs[0].natIP)"
```

You should get an IP like `34.30.69.134`.

## 2) Set password for Linux user `worker`

Run:

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo passwd worker"
```

You will be prompted for:
- `New password:`
- `Retype new password:`

Expected success output:
- `passwd: password updated successfully`

## 3) Confirm RDP service is up on the VM

Run:

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "systemctl is-active xrdp && sudo ss -ltnp | grep 3389"
```

Expected:
- `active`
- a `LISTEN ... :3389` line

## 4) Connect from Windows Remote Desktop

1. Press `Win + R`
2. Type `mstsc` and press Enter
3. In `Computer`, enter: `34.30.69.134:3389` (use your actual VM IP)
4. Click `Connect`
5. Log in with:
- Username: `worker`
- Password: the one you set in step 2 (password)

If a certificate warning appears, continue.

## 5) In VM desktop, open Chrome and sign into YouTube

Inside the VM session:

1. Open Chrome (app menu or terminal command `google-chrome`)
2. Sign into your Google account
3. Open `https://www.youtube.com/`
4. Confirm you are signed in (profile avatar visible)

This creates the browser profile/cookies the worker will read.

## 6) Verify Chrome profile exists

Back on your local terminal:

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo -u worker ls -ld /home/worker/.config/google-chrome/Default"
```

If the directory exists, browser-cookie mode is ready.

## 7) Restart/redeploy VM worker so latest env/code is active

From repo root:

```powershell
bash infra/gcp/deploy_worker_vm.sh
```

How to read the output (like your screenshot):
- `VM worker deployed on audio-summarizer-worker-vm (us-central1-a).`
  - This line means the VM-side deploy steps finished successfully.
  - `/opt/capstone`:
    - your latest worker source code was copied onto the VM here.
  - `/etc/capstone/worker.env`:
    - the worker runtime settings were written here (DB/Redis/OpenAI/cookie env vars).
  - `systemd restart`:
    - services were restarted so changes are actually applied:
      - `cloud-sql-proxy`
      - `capstone-worker`
  - Important: this confirms deploy/restart only. It does **not** confirm a job has already run.
- `Deploying... Done.` with `Creating Revision` / `Routing traffic`
  - This is the Cloud Run worker service being updated by the script.
- `Service [audio-summarizer-worker] revision [...] has been deployed`
  - Normal and expected; it does not mean VM mode failed.
- `Scaled Cloud Run worker ... to min-instances=0 (VM worker is primary).`
  - Desired final state in VM mode: Cloud Run worker is idled, VM worker is primary.
- `To verify logs: ... journalctl -u capstone-worker -f`
  - Use that command to watch the VM worker process in real time.

If you do **not** see explicit `ERROR:` lines, this section succeeded.

## 8) Verify worker is healthy

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "systemctl is-active cloud-sql-proxy && systemctl is-active capstone-worker"
```

Expected:
- `active`
- `active`

## 9) If profile is not `Default`

List profiles:

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo -u worker ls -1 /home/worker/.config/google-chrome | grep '^Profile\|^Default' || true"
```

If you see `Profile 1` (or similar), set in `infra/gcp/deploy_config.py`:

```python
YTDLP_COOKIES_FROM_BROWSER_PROFILE = "Profile 1"
```

Then run:

```powershell
bash infra/gcp/deploy_worker_vm.sh
```

## Common issues

- RDP cannot connect:
  - Verify VM external IP again.
  - Verify firewall rule allows your IP on TCP 3389.
- Login works but YouTube jobs still fail:
  - Re-open Chrome in VM and confirm still signed in.
  - Confirm profile folder exists under `/home/worker/.config/google-chrome/`.
  - Check worker logs:

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo journalctl -u capstone-worker -f"
```
