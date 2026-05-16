# Email Digest Next Steps

This guide is the shortest path to getting the new per-user email digest feature actually sending mail.

## Current state

Already done:

- digest feature code is implemented in backend + frontend
- web UI for `Email Digests` is deployed
- API endpoints are deployed
- Cloud Scheduler API is enabled
- Cloud Scheduler job exists:
  - `audio-summarizer-digest-sweep`
- digest sweep secret is already set in `infra/gcp/deploy_config.py`

Not done yet:

- SMTP delivery is not configured

That means:

- users can see the digest UI
- users can save digest settings
- scheduler can trigger the sweep endpoint
- but no email will actually send until SMTP is configured and redeployed

## What you need to do

## 1) Put your SMTP settings into `infra/gcp/deploy_config.py`

Open:

- `infra/gcp/deploy_config.py`

Set these fields in the active `CONFIG = DeployConfig(...)` block:

```python
SMTP_HOST="your-smtp-host",
SMTP_PORT="587",
SMTP_USER="your-smtp-username",
SMTP_PASSWORD="your-smtp-password",
SMTP_FROM="Audio Summarizer <noreply@your-domain.com>",
```

Beginner note on `SMTP_PORT`:

- A `port` is like a numbered door on a server.
- `SMTP_HOST` tells your app which server to go to.
- `SMTP_PORT` tells your app which door on that server is handling email.
- For SMTP, `587` usually means "send email using a secure connection with STARTTLS".
- You normally do not invent this number yourself. You copy it from your email provider's docs.
- If the host is right but the port is wrong, the app usually cannot connect to send mail.

If you want to use Gmail, use this as the starting point:

```python
SMTP_HOST="smtp.gmail.com",
SMTP_PORT="587",
SMTP_USER="your-full-gmail-address@gmail.com",
SMTP_PASSWORD="replace-me",
SMTP_FROM="Audio Summarizer <your-full-gmail-address@gmail.com>",
```

How to find the Gmail values:

1. Use your full Gmail address for `SMTP_USER`.
2. Turn on Google 2-Step Verification for that account.
3. Generate an App Password at `https://myaccount.google.com/apppasswords`.
4. Paste that 16-digit App Password into `SMTP_PASSWORD`.

Why Google says that:

- Google is warning you that an App Password is a fallback for apps that log in with just a username and password instead of using Google's newer OAuth sign-in flow.
- This project sends mail through regular SMTP login, so it falls into that older-style category.
- In this repo, the mailer connects to Gmail over SMTP, starts TLS, and then calls `smtp.login(...)`, which is exactly the kind of flow Google is talking about.
- That does not mean you are doing something "wrong". It just means Gmail treats this as a compatibility path, not the most modern authentication method.
- For this project, using a Google App Password is the normal way to make Gmail SMTP work.
- The more modern alternative would be rewriting the mail-sending setup to use Gmail's API or OAuth2 instead of `SMTP_USER` + `SMTP_PASSWORD`.

In short: Google shows that message because this app is acting like a traditional SMTP client, and App Passwords exist specifically to let that kind of app keep working with a Google account.

5. Keep `SMTP_FROM` on the same Gmail address unless you already have a verified Gmail alias.

What to put in `SMTP_FROM` if your Gmail address is `danmcneary8@gmail.com`:

```python
SMTP_FROM="Audio Summarizer <danmcneary8@gmail.com>",
```

`Audio Summarizer` is just the display name people see in their inbox. If you want, you can change that part to something else, for example:

```python
SMTP_FROM="Dan McNeary <danmcneary8@gmail.com>",
```

If `App passwords` is missing, Google usually has one of these restrictions in place:

- the account is managed by Google Workspace and the admin has blocked it
- the account uses Advanced Protection
- 2-Step Verification is set up with security keys only

Google references:

- App passwords: `https://support.google.com/mail/answer/1173270`
- Gmail SMTP server settings: `https://support.google.com/a/answer/176600`

Notes:

- `SMTP_PORT="587"` is the normal STARTTLS port and is usually the right default.
- `SMTP_FROM` should be an address your provider allows you to send from.
- You do not need to change `DIGEST_SWEEP_SECRET` unless you want to rotate it.

## 2) Redeploy API and VM worker

From repo root:

```powershell
bash infra/gcp/deploy_api.sh
bash infra/gcp/deploy_worker_vm.sh
```

Why both:

- `deploy_api.sh` updates Cloud Run API env vars and the Cloud Scheduler target config
- `deploy_worker_vm.sh` writes the SMTP settings into the VM worker env file, and the worker is what actually sends the digest email

## 3) Verify the scheduler job is still configured

Run:

```powershell
gcloud scheduler jobs describe audio-summarizer-digest-sweep `
  --location us-central1
```

What you want to see:

- job exists
  - here, `the job` means the saved Cloud Scheduler task named `audio-summarizer-digest-sweep`
  - it is not an email digest itself and it is not one of your users' audio jobs
  - its only purpose is to wake up on a schedule and call the digest sweep API endpoint
- target URI ends with:
  - `/internal/digests/sweep`
  - this is the backend URL the scheduler calls every time the timer goes off
  - `API endpoint` just means a specific URL in your app that is meant to be called by code, not opened by a person in the browser
  - `digest sweep` means "check which users are due for a digest right now"
  - this endpoint does not mean "send every email immediately no matter what"
  - instead, it starts the digest sweep process, which checks saved digest settings, finds users who are due, and queues the work to build and send those digest emails
- schedule is every 15 minutes unless you changed it
- state is `ENABLED`

Beginner breakdown:

- This command does not run the job. It only shows you the current scheduler configuration.
- Think of Cloud Scheduler as the timer or alarm clock for the digest system.
- In this section, `job` means a Google Cloud Scheduler job, which is basically a saved recurring task.
- `job exists` means Google Cloud still has the scheduled task saved.
- the target URI is the API endpoint Cloud Scheduler will call when the timer fires.
- if the URI ends with `/internal/digests/sweep`, it means the scheduler is pointing at the correct digest sweep endpoint.
- You can think of `/internal/digests/sweep` as the "start checking for due digests now" button for the backend.
- `schedule is every 15 minutes` means Google Cloud will try the sweep 4 times per hour.
- `state is ENABLED` means the timer is turned on. If you see `PAUSED` or `DISABLED`, the job will not run automatically.

In the command output, the main fields to look at are usually:

- `name`
- `schedule`
- `state`
- `httpTarget.uri`

If any of those look wrong, the scheduler may still exist, but it may be pointing to the wrong endpoint or not running on a schedule.

## 4) Verify the VM worker received the SMTP settings

Run:

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo grep -E '^(SMTP_|WEB_APP_BASE_URL|DIGEST_)' /etc/capstone/worker.env"
```

Beginner breakdown:

- `gcloud compute ssh` means "log into a Google Cloud VM from your terminal".
- `audio-summarizer-worker-vm` is the name of the worker machine that runs background jobs.
- `--zone us-central1-a` tells Google Cloud which data-center location that VM is in.
- `--command "..."` means "do not open an interactive shell; just run this one command on the VM and print the result".
- `sudo` means run the command with admin permissions.
- `/etc/capstone/worker.env` is the worker's environment file, which is where deploy puts the config values the worker service uses.
- `grep -E '^(SMTP_|WEB_APP_BASE_URL|DIGEST_)'` means "show only the lines in that file that start with `SMTP_`, `WEB_APP_BASE_URL`, or `DIGEST_`".
- This is basically a quick spot-check that the worker VM actually received the settings you just deployed.

Why this matters:

- the VM worker is the part that actually builds and sends the digest email
- if the API was deployed correctly but the worker VM did not get the SMTP settings, digest sending can still fail
- this check helps you confirm the worker has the email config before you test delivery

Security note:

- this command will print `SMTP_PASSWORD` to your terminal
- do not paste that output into screenshots, chat messages, or docs

You should see:

- `SMTP_HOST=...`
- `SMTP_PORT=...`
- `SMTP_USER=...`
- `SMTP_PASSWORD=...`
- `SMTP_FROM=...`
- `WEB_APP_BASE_URL=...`

If the SMTP lines are missing, the worker deploy did not pick up your config.

## 5) Turn digests on in the web app

In the signed-in web app:

1. Open the `Email Digests` card
2. Enable digests
3. Choose:
   - `daily`, or
   - `weekly`
4. Save

Important behavior:

- the first real digest can include older completed jobs from before enable time
- the rolling profile already uses historical completed jobs
- after the first successful digest, later digests are incremental from the last successful digest window

## 6) Create a real test case

After enabling digests:

1. Submit a new job
2. Wait for it to reach `complete`

You can use older completed jobs for the first test. If historical backfill is still pending, the next real digest can include jobs from before enable time.

## 7) Trigger the scheduler manually for a fast test

Instead of waiting 15 minutes, run:

```powershell
gcloud scheduler jobs run audio-summarizer-digest-sweep --location us-central1
```

This forces a sweep immediately.

## 8) Check logs if the email does not arrive

### API logs

```powershell
gcloud run services logs read audio-summarizer-api --region us-central1 --limit 100
```

### VM worker logs

```powershell
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo journalctl -u capstone-worker -n 200 --no-pager"
```

The worker log is the more important one for delivery failures.

## Expected behavior

If everything is working:

- the scheduler calls the API sweep endpoint
- the API enqueues the digest work
- the VM worker builds the digest email
- the email goes to the signed-in Google account email

Each digest includes:

- completed jobs since the last digest window
- an AI recap of that window
- a rolling taste/profile summary
- links back into the app for each job

## Common reasons it still will not send

### 1) SMTP is still not configured

Symptom:

- the UI shows digest settings, but delivery is unavailable or nothing sends

Fix:

- fill in the `SMTP_*` fields
- redeploy API and worker

### 2) No completed jobs in the current digest window

Symptom:

- sweep runs, but no email arrives

Reason:

- empty digest windows do not send an email
- after the first successful digest, the window becomes incremental

Fix:

- submit and complete a new job after enabling digests

### 3) SMTP provider rejected the sender

Symptom:

- worker logs show SMTP auth or sender rejection errors

Fix:

- make sure `SMTP_FROM` is allowed by your SMTP provider
- make sure `SMTP_USER` / `SMTP_PASSWORD` are correct

### 4) Worker did not get updated env

Symptom:

- API looks correct, but sends still fail immediately

Fix:

- rerun:

```powershell
bash infra/gcp/deploy_worker_vm.sh
```

## Minimal checklist

If you just want the exact checklist:

1. set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` in `infra/gcp/deploy_config.py`
2. run:

```powershell
bash infra/gcp/deploy_api.sh
bash infra/gcp/deploy_worker_vm.sh
```

3. enable digests in the web UI
4. submit a new job and wait for `complete`
5. run:

```powershell
gcloud scheduler jobs run audio-summarizer-digest-sweep --location us-central1
```

6. check your inbox

## If you want me to finish it for you

If you give me the SMTP provider values, the remaining work is mechanical:

- put them into `infra/gcp/deploy_config.py`
- redeploy API
- redeploy worker VM
- run a manual scheduler trigger
- verify delivery
