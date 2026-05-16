from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from backend.summarizer import DigestOverview


def build_digest_subject(cadence: str, job_count: int) -> str:
    label = "daily" if cadence == "daily" else "weekly"
    noun = "summary" if job_count == 1 else "summaries"
    return f"Your {label} audio digest: {job_count} new {noun}"


def _fmt_local(dt: datetime | None, timezone_name: str) -> str:
    if not dt:
        return "-"
    try:
        tz = ZoneInfo(timezone_name)
        formatted = dt.astimezone(tz).strftime("%b %d, %Y %I:%M %p")
        return formatted.replace(" 0", " ").lstrip("0")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def render_digest_text(
    *,
    recipient_name: str | None,
    cadence: str,
    overview: DigestOverview,
    profile_summary: str | None,
    jobs: Iterable[dict],
    app_url: str,
    timezone_name: str,
    remaining_job_count: int = 0,
) -> str:
    salutation = f"Hi {recipient_name}," if recipient_name else "Hi,"
    lines = [salutation, "", overview.intro, ""]
    if profile_summary:
        lines.extend(["Your profile lately:", profile_summary, ""])
    if overview.highlights:
        lines.append("Highlights:")
        for item in overview.highlights:
            lines.append(f"- {item}")
        lines.append("")
    if overview.profile_note:
        lines.extend([overview.profile_note, ""])
    lines.append("Completed jobs in this digest:")
    for job in jobs:
        lines.append(f"- {job['title']} ({_fmt_local(job.get('completed_at'), timezone_name)})")
        if job.get("summary_excerpt"):
            lines.append(f"  {job['summary_excerpt']}")
        lines.append(f"  Open in app: {job['app_link']}")
        if job.get("source_url"):
            lines.append(f"  Original source: {job['source_url']}")
    if remaining_job_count > 0:
        lines.append(f"- Plus {remaining_job_count} more completed jobs in this digest window.")
    lines.extend(["", f"Open the app: {app_url}", ""])
    if cadence == "weekly":
        lines.append("You are receiving this weekly because weekly digest delivery is enabled.")
    else:
        lines.append("You are receiving this daily because daily digest delivery is enabled.")
    return "\n".join(lines)


def render_digest_html(
    *,
    recipient_name: str | None,
    overview: DigestOverview,
    profile_summary: str | None,
    jobs: Iterable[dict],
    app_url: str,
    timezone_name: str,
    remaining_job_count: int = 0,
) -> str:
    greeting = escape(f"Hi {recipient_name}," if recipient_name else "Hi,")
    highlight_items = "".join(f"<li>{escape(item)}</li>" for item in overview.highlights)
    job_cards = []
    for job in jobs:
        title = escape(job["title"])
        excerpt = escape(job.get("summary_excerpt") or "")
        app_link = escape(job["app_link"])
        source_url = escape(job.get("source_url") or "")
        completed = escape(_fmt_local(job.get("completed_at"), timezone_name))
        source_html = (
            f'<div style="margin-top:6px;"><a href="{source_url}" '
            'style="color:#1d4ed8;text-decoration:none;">Original source</a></div>'
            if source_url
            else ""
        )
        excerpt_html = f"<p style='margin:8px 0 0;color:#334155;'>{excerpt}</p>" if excerpt else ""
        job_cards.append(
            "<div style=\"border:1px solid #d7e2f1;border-radius:14px;padding:16px;background:#ffffff;margin:0 0 12px;\">"
            f"<div style=\"font-size:18px;font-weight:700;color:#0f172a;\">{title}</div>"
            f"<div style=\"margin-top:4px;font-size:13px;color:#64748b;\">Completed {completed}</div>"
            f"{excerpt_html}"
            f"<div style=\"margin-top:10px;\"><a href=\"{app_link}\" "
            "style=\"display:inline-block;background:#dbeafe;color:#1e3a8a;padding:8px 12px;border-radius:999px;text-decoration:none;font-weight:600;\">"
            "Open in app</a></div>"
            f"{source_html}"
            "</div>"
        )
    profile_html = (
        "<div style=\"border:1px solid #d7e2f1;border-radius:14px;padding:16px;background:#f8fbff;margin-bottom:16px;\">"
        "<div style=\"font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:700;\">Your profile lately</div>"
        f"<p style=\"margin:8px 0 0;color:#334155;\">{escape(profile_summary)}</p>"
        "</div>"
        if profile_summary
        else ""
    )
    highlights_html = (
        "<div style=\"margin:0 0 16px;\">"
        "<div style=\"font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:700;\">Highlights</div>"
        f"<ul style=\"margin:8px 0 0 18px;color:#334155;\">{highlight_items}</ul>"
        "</div>"
        if highlight_items
        else ""
    )
    profile_note_html = (
        f"<p style=\"margin:0 0 16px;color:#475569;\">{escape(overview.profile_note)}</p>" if overview.profile_note else ""
    )
    remaining_jobs_html = (
        "<div style=\"margin:0 0 16px;color:#475569;font-size:14px;\">"
        f"Plus {remaining_job_count} more completed jobs in this digest window."
        "</div>"
        if remaining_job_count > 0
        else ""
    )
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#eef3fb;font-family:Arial,sans-serif;color:#0f172a;">
    <div style="max-width:760px;margin:0 auto;background:#f8fbff;border:1px solid #d7e2f1;border-radius:20px;padding:28px;">
      <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:700;">Audio Summarizer</div>
      <h1 style="margin:10px 0 8px;font-size:28px;line-height:1.15;color:#0f172a;">{greeting}</h1>
      <p style="margin:0 0 18px;color:#334155;">{escape(overview.intro)}</p>
      {profile_html}
      {highlights_html}
      {profile_note_html}
      <div style="margin:0 0 16px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:700;">Completed jobs in this digest</div>
      {remaining_jobs_html}
      {''.join(job_cards)}
      <div style="text-align:center;padding-top:8px;">
        <a href="{escape(app_url)}" style="display:inline-block;background:#1d4ed8;color:#ffffff;padding:12px 18px;border-radius:999px;text-decoration:none;font-weight:700;">Open Audio Summarizer</a>
      </div>
    </div>
  </body>
</html>
"""
