# Set `GOOGLE_OAUTH_CLIENT_ID` in `deploy_config.py` (production-first, allow all users)

This guide is for your current project:
- Project ID: `tribal-primer-438802-n0`
- Repo path: `infra/gcp/deploy_config.py`

Goal:
- Configure Google OAuth so the app is `External` and `In production` (not `Testing`), which allows sign-in for all Google users.

## 1) Open the correct GCP project

1. Go to Google Cloud Console.
2. Select project **`tribal-primer-438802-n0`** (top project picker).

## 2) Configure OAuth consent screen for production (one-time)

If you are on `Google Auth Platform` -> `OAuth Overview` (the screen with left menu items like `Branding`, `Audience`, and `Clients`), do this:

1. Click `Branding` in the left menu.
2. Fill required app fields (app name, support email, developer contact) and save.
3. Click `Audience` in the left menu.
4. Set user type to `External`.
5. Click `Publish app` / `Push to production`.
6. Confirm the status now shows `In production`.
7. Go to `Clients` and create or use your OAuth Web Client ID.

How this repo is currently set up:
- Frontend uses Google Identity (`google.accounts.id`) for sign-in and receives an ID token.
- Backend verifies that ID token against `WEBAPP_GOOGLE_CLIENT_ID`.
- There is no app flow here requesting extra Google API scopes (for example Gmail/Drive/Calendar scopes).
- For this implementation, clicking `Confirm` is normally the correct path.

Important:
- In `In production`, you do not need to maintain a `Test users` allowlist.
- If the status is still `Testing`, only listed test users can sign in.
- On many projects, `Make internal` is disabled; this is normal and not a blocker.

## 3) Create the OAuth Web client

1. Go to `APIs & Services` -> `Credentials`.
2. Click `+ Create Credentials` -> `OAuth client ID`.
3. Application type: `Web application`.
4. Name it (example: `audio-summarizer-web`).

### Authorized JavaScript origins
Add at least:
- `http://localhost:5173` (local dev)
- your Cloud Run web origin (from Step 4)
  This means the URL of your deployed **web frontend** Cloud Run service.
  Example: `https://audio-summarizer-web-tedb4icw5q-uc.a.run.app`
  Copy that exact URL from Step 4 and paste it here.
  Do not use the API service URL and do not add paths like `/login`.

What this is for:
- Google blocks sign-in requests from unknown websites by default.
- This list is your allowlist: "Google Sign-In is allowed only from these web app addresses."

What you should add right now:
- `http://localhost:5173` so sign-in works during local development.
- Your deployed web app URL from Step 4 so sign-in works in Cloud Run.
- Later, if you move to a custom domain (for example `https://app.example.com`), add that too.

How to type each entry correctly:
- Use only the origin: `protocol + hostname + optional port`.
- Do not add paths like `/login`, query params, or trailing route fragments.
- It must match exactly. If it does not, Google sign-in will fail with an origin mismatch error.

Valid examples:
- `http://localhost:5173`
- `https://audio-summarizer-web-tedb4icw5q-uc.a.run.app`

Invalid examples:
- `https://audio-summarizer-web-tedb4icw5q-uc.a.run.app/login` (path included)
- `https://audio-summarizer-api-xxxxx-uc.a.run.app` (API service, not web app origin)
- `http://localhost:3000` (wrong local port if your app runs on `5173`)

Important:
- Origin only (scheme + host + optional port), no path.
- Do not include trailing route paths.

## 4) Get your Cloud Run web origin

From repo root in PowerShell:

```powershell
gcloud run services describe audio-summarizer-web --region us-central1 --project tribal-primer-438802-n0 --format="value(status.url)"
```

If output is:

```text
https://audio-summarizer-web-tedb4icw5q-uc.a.run.app
```

Use exactly that value as an Authorized JavaScript origin.

## 5) Copy the Client ID

From the created OAuth credential, copy the **Client ID**  
(looks like `1234567890-abc...apps.googleusercontent.com`).

## 6) Set it in `deploy_config.py`

Edit `infra/gcp/deploy_config.py` inside the active `CONFIG = DeployConfig(...)` block and add:

```python
GOOGLE_OAUTH_CLIENT_ID="1234567890-abc...apps.googleusercontent.com",
```

Example (partial):

```python
CONFIG = DeployConfig(
    PROJECT_ID="tribal-primer-438802-n0",
    REPO="capstone-repo",
    DB_PASSWORD="...",
    BUCKET_NAME="tribal-primer-438802-n0-capstone-artifacts",
    GOOGLE_OAUTH_CLIENT_ID="1234567890-abc...apps.googleusercontent.com",
    # ...
)
```

## 7) Verify the value is loaded by scripts

Run:

```powershell
python infra/gcp/load_python_env.py | Select-String GOOGLE_OAUTH_CLIENT_ID
```

You should see an export line containing your client ID.

## 8) Redeploy API + Web

From repo root:

```powershell
bash infra/gcp/deploy_api.sh
bash infra/gcp/deploy_web.sh
```

Why both:
- API needs `WEBAPP_GOOGLE_CLIENT_ID` to verify tokens.
- Web needs `VITE_GOOGLE_CLIENT_ID` to show Google Sign-In.

## 9) Quick validation

1. Open the web app URL.
2. You should see the sign-in splash.
3. Sign in with a Google account that is not listed as a test user.
4. Confirm jobs load after sign-in.

## Troubleshooting

If sign-in fails:
- Recheck project selection (`tribal-primer-438802-n0`).
- Recheck OAuth consent status is `In production`.
- Recheck Authorized JavaScript origins (must match exact web origin).
- Recheck that `GOOGLE_OAUTH_CLIENT_ID` is set in `deploy_config.py` and redeployed.

If users see an "unverified app" warning:
- Review requested scopes in OAuth consent screen.
- Keep scopes minimal unless you intentionally need sensitive/restricted scopes.
