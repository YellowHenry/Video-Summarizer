from __future__ import annotations

import subprocess
from pathlib import Path

from backend import downloader as downloader_module


def test_should_retry_cookieless_media_on_youtube_format_failures():
    assert downloader_module._should_retry_cookieless_media(
        "WARNING: Only images are available for download."
    )
    assert downloader_module._should_retry_cookieless_media(
        "ERROR: [youtube] abc123: Requested format is not available."
    )
    assert downloader_module._should_retry_cookieless_media(
        'WARNING: [youtube] [jsc] Error solving n challenge request using "node" provider'
    )
    assert not downloader_module._should_retry_cookieless_media(
        "ERROR: authentication is required"
    )


def test_download_youtube_retries_without_cookies_when_cookie_clients_return_only_images(
    tmp_path: Path,
    monkeypatch,
):
    downloader = downloader_module.AudioDownloader(download_root=tmp_path)
    monkeypatch.setattr(downloader, "_get_title", lambda _url: "Recovered title")
    monkeypatch.setattr(
        downloader,
        "_cookie_attempts",
        lambda: [(["--cookies-from-browser", "chrome:Default"], "browser:chrome profile:Default")],
    )
    monkeypatch.setattr(downloader_module, "_ensure_runtime_path", lambda: None)
    monkeypatch.setattr(
        downloader_module.shutil,
        "which",
        lambda name: "/usr/bin/yt-dlp" if name == "yt-dlp" else None,
    )

    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text, env):  # noqa: ANN001
        calls.append(cmd)
        output_path = Path(cmd[cmd.index("-o") + 1])
        extractor_arg = cmd[cmd.index("--extractor-args") + 1]

        if "--cookies-from-browser" in cmd:
            raise subprocess.CalledProcessError(
                1,
                cmd,
                stderr=(
                    'WARNING: [youtube] [jsc] Error solving n challenge request using "node" provider\n'
                    "WARNING: Only images are available for download.\n"
                    "ERROR: [youtube] abc123: Requested format is not available."
                ),
            )

        if extractor_arg == "youtube:player_client=android_sdkless":
            output_path.write_bytes(b"audio")
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        raise AssertionError(f"Unexpected yt-dlp invocation: {cmd}")

    monkeypatch.setattr(downloader_module.subprocess, "run", fake_run)

    media_path, title = downloader.download_youtube("https://www.youtube.com/watch?v=v1ChnmB4KPg")

    assert media_path.exists()
    assert title == "Recovered title"
    assert any("--cookies-from-browser" in cmd for cmd in calls)
    assert any(
        "--cookies-from-browser" not in cmd
        and "youtube:player_client=android_sdkless" in cmd
        for cmd in calls
    )
