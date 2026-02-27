# I asked:

So will I keep getting the weird notifications about docker not working when codex runs the deploys scripts, and are the webshare proxies that i'm paying for used anywhere unecessarily

# GPT said:

Yes, but only for one reason now.

1. Docker/WSL notifications: probably still possible
- The Webshare proxy leakage issue is mostly fixed now:
  - `infra/gcp/load_python_env.py` no longer exports `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
  - deploy scripts wrap `gcloud` and unset proxy env vars (for example `infra/gcp/deploy_web.sh:8`)
- But deploy scripts still run `docker build/push` from WSL, so if Docker Desktop is not reachable from WSL, you can still get Docker-related failures/noisy messages.
  - Current environment check (this machine / deploy-script context): WSL Docker appears to be available right now.
  - I checked that `wsl.exe` is installed, your default WSL distro is `Ubuntu`, and `docker version` succeeds inside WSL (`wsl.exe -d Ubuntu -- ...`) with Docker server version `29.2.1`.
  - Plain English: for the environment you currently use to run the deploy `.sh` scripts, WSL can currently talk to Docker.
  - Caveat: this can still break later if Docker Desktop is closed, WSL integration is turned off, or the deploy runs from a different machine/session.
- That part is a WSL <-> Docker Desktop integration issue, not a Webshare proxy issue.

### Why your machine is building something (beginner version)

Short answer:

- Your machine does **not** have to do the build.
- In this project, the deploy scripts are currently set up so your machine does the build first.

What is being built?

- A **Docker image** (a packaged version of the app).
- Think of it like a ready-to-use software package that includes:
  - the app code
  - the libraries it needs
  - setup instructions for how it starts

What the steps mean:

- `docker build` = create the package
- `docker push` = upload the package
- cloud machine = download the package and run it

So the cloud machine usually runs the app, but your machine may still create the package first.

### Beginner Q&A: "So does the local machine run the Dockerfile and create the image?"

Yes, in the current setup for this repo:

- Your local machine (through WSL) runs `docker build`
- Docker reads the `Dockerfile`
- Docker creates a **Docker image**

And yes, a cloud-based build machine could do that instead.

So a more accurate version is:

- Local machine can run the `Dockerfile` and build the image
- Or a cloud build machine can run the `Dockerfile` and build the image
  - Plain English: "cloud build machine" means a temporary build server in the cloud (for example, a CI/CD runner) that does `docker build` for you instead of your laptop.
  - It builds the image in a cloud job, then pushes it to your container registry.
  - Yes: this is usually a **different machine/service** from the machine that actually runs your app image.
  - They can be the same machine in simpler setups, but best practice is to keep them separate (build role vs runtime role).
- Then the runtime cloud machine downloads that image and runs it

Why not just build on the cloud machine that runs the app?

- It is usually better to keep the "build/package" step separate from the "run the app" step.
- This makes deploys more predictable and easier to roll back.
- It also means the app machine can start faster because it is not installing/building everything during startup.

What makes building harder than it sounds?

- The build may need to download a lot of dependencies
- Some dependencies need extra tools to install/compile
- Frontend files may need to be bundled
- Network/auth issues can break the build
- Small config mistakes can break the build

In this repo specifically:

- The deploy scripts currently run `docker build` and `docker push` from WSL on your machine.
- That is why your local Docker/WSL setup matters.
- This could be changed later to use a cloud build service instead.

2. Are your paid Webshare proxies used unnecessarily right now?
- Mostly no, based on the current code/config shape.
- Runtime usage is scoped by `PROXY_CAPTIONS_ONLY=true` in `backend/proxy_egress.py:87`
  - Plain English: a setting is turned on that says "only use proxies for captions-related work."
  - This is the main rule limiting Webshare usage.
  - Current config-file check (`infra/gcp/deploy_config.py`): the `DeployConfig` class default is `PROXY_CAPTIONS_ONLY="false"` (`infra/gcp/deploy_config.py:89`), but the active `CONFIG = DeployConfig(...)` block currently overrides it to `PROXY_CAPTIONS_ONLY="true"` (`infra/gcp/deploy_config.py:136`).
  - Plain English: in the current deploy config variables file, it is currently set to **on/true** for the configured deployment.
  - `backend/downloader.py:104` asks for proxy settings for the audio-download step (`purpose="audio_download"`), but captions-only mode turns that proxy usage off.
    - Plain English: when the app is downloading audio, it checks "should I use the paid proxy for this?" and the current setting answers "no."
    - If you skip captions and go straight to Whisper: the app still uses `downloader.py` to get the audio, but with `PROXY_CAPTIONS_ONLY=true` it still will **not** use the Webshare proxy for that audio download.
    - Important caveat: if `PROXY_CAPTIONS_ONLY=false` (and proxies are enabled), then `downloader.py` can use the proxy for audio download.
  - `backend/summarizer.py:113` loads captions proxy with `purpose="captions"`, which means this is the part of the app where the proxy is intentionally used.
    - Plain English: this is the intended use of your paid proxy: fetching YouTube captions.
    - "Loads proxy config" means the app reads the proxy settings for that specific task before making the request.
  - `backend/summarizer.py:114` loads `http_summarizer` proxy config, and that proxy path is disabled in captions-only mode.
    - Plain English: another proxy option exists for summarizer web traffic, but it is also turned off in captions-only mode.
- OpenAI calls are direct:
  - `backend/summarizer.py:129` creates client with `trust_env=False` by default
    - Plain English: OpenAI requests are told to ignore proxy settings from the environment, so they should go out directly.
- Worker no longer uses global proxy env for everything (so GCS uploads shouldnâ€™t go through Webshare anymore)
  - Plain English: the background worker is no longer forcing all internet traffic through Webshare.
  - Result: Google Cloud Storage uploads should not use the paid proxy.

Practical takeaway:
- Your Webshare proxy should now be used mainly for YouTube caption-fetch attempts, which is what you wanted.
- The remaining annoying deploy failures are mostly Docker Desktop/WSL plumbing, not proxy overuse.

If you want, I can draft a follow-up plan to make deploy scripts use `docker.exe` fallback automatically when WSL Docker is unavailable, which would eliminate most of the remaining Docker noise.

---

# Plain-English Breakdown (Beginner-Friendly)

## Why Docker notifications can still happen

This part is about **your local deploy workflow**, not app logic.

- Deploy scripts still run commands like `docker build` and `docker push`
- Those commands run from **WSL** (Linux environment on Windows)
- If WSL cannot talk to Docker Desktop correctly, deploys can fail/noisily warn

So:

- **Not a proxy billing issue**
- **Mostly a Windows/WSL/Docker Desktop integration issue**


## What `trust_env=False` means (OpenAI calls are direct)

This is a Python client setting that tells the OpenAI client:

- "Do not automatically use proxy settings from environment variables"

So when the note says OpenAI calls are direct, it means:

- Requests to OpenAI should go straight out normally
- They should **not** route through your Webshare proxy just because proxy env vars exist somewhere

## PM-Friendly Version (No Docker Knowledge Needed)

What is being built:

- A **container image** (a packaged version of the app)
- Think of it as a bundle that includes:
  - your app code
  - its dependencies/libraries
  - runtime setup (how the app starts)
  - system packages needed by the app

What the cloud machine does vs what the build does:

- **`docker build` step**: creates the packaged app image
- **`docker push` step**: uploads that package to a registry (storage for images)
- **Cloud machine run step**: downloads the image and starts it
- **Environment/config step**: provides secrets, env vars, and machine settings at runtime

So your mental model ("the cloud machine runs commands to set its environment") is partly correct, but that is only the **runtime configuration** part.

There is also a separate **packaging step** that creates the app image first.

A helpful distinction:

- **Build** = package the software
- **Deploy/Run** = start the packaged software in the cloud with the right settings
