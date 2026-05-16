import type { Page } from "@playwright/test";

type JobSummary = {
  id: string;
  created_at: string;
  updated_at: string;
  status: string;
  source_type: string;
  source_url: string | null;
  title: string | null;
};

type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

const AUTH_TOKEN_STORAGE_KEY = "audio_summarizer_auth_token";
const TEST_AUTH_TOKEN = "test-token";
const FIXED_NOW = "2026-02-28T12:00:00.000Z";

export async function setupMockApi(page: Page): Promise<void> {
  await page.addInitScript(
    ({ storageKey, token }) => {
      window.localStorage.setItem(storageKey, token);
    },
    { storageKey: AUTH_TOKEN_STORAGE_KEY, token: TEST_AUTH_TOKEN }
  );

  const jobs: JobSummary[] = [];
  const chats: Record<string, ChatMessage[]> = {};
  const jobErrors: Record<string, string | null> = {};
  const retryableUrlAttempts: Record<string, number> = {};
  let jobCounter = 0;
  let digestSettings = {
    enabled: false,
    cadence: "daily",
    timezone: "America/New_York",
    send_hour_local: 8,
    weekly_weekday: 0,
    recipient_email: "e2e@example.com",
    delivery_available: true,
    delivery_reason: null,
    next_send_at: null,
    last_run_at: null,
    last_run_status: null,
    last_sent_at: null,
    profile_summary: null,
    profile_updated_at: null,
    historical_backfill_pending: false
  };
  const digestHistory = [
    {
      id: 1,
      status: "sent",
      cadence: "daily",
      job_count: 2,
      subject: "Your daily audio digest: 2 new summaries",
      window_start_at: "2026-02-27T13:00:00.000Z",
      window_end_at: "2026-02-28T13:00:00.000Z",
      created_at: FIXED_NOW,
      sent_at: FIXED_NOW
    }
  ];

  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      json: { email: "e2e@example.com", name: "E2E User", picture: null }
    });
  });

  await page.route("**/api/digests/settings", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: digestSettings });
      return;
    }
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as {
        enabled: boolean;
        cadence: "daily" | "weekly";
        timezone: string;
      };
      digestSettings = {
        ...digestSettings,
        enabled: body.enabled,
        cadence: body.cadence,
        timezone: body.timezone,
        next_send_at: "2026-03-01T13:00:00.000Z",
        last_run_status: body.enabled ? "sent" : digestSettings.last_run_status,
        historical_backfill_pending: body.enabled
      };
      await route.fulfill({ json: digestSettings });
      return;
    }
    await route.continue();
  });

  await page.route("**/api/digests/history", async (route) => {
    await route.fulfill({ json: digestHistory });
  });

  await page.route("**/oembed**", async (route) => {
    await route.fulfill({
      json: { title: "Mock Video Title", author_name: "Mock Channel", provider_name: "YouTube" }
    });
  });

  await page.route("**/api/jobs", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill({ json: jobs });
      return;
    }

    if (method === "POST") {
      const body = route.request().postDataJSON() as { youtube_url?: string };
      const id = `job-e2e-${++jobCounter}`;
      const sourceUrl = body.youtube_url || null;
      const isRetryableFailure = Boolean(sourceUrl?.includes("retryable-failure"));
      retryableUrlAttempts[sourceUrl || "upload"] ||= 0;
      retryableUrlAttempts[sourceUrl || "upload"] += 1;
      const shouldFail = isRetryableFailure && retryableUrlAttempts[sourceUrl || "upload"] === 1;
      jobs.unshift({
        id,
        created_at: FIXED_NOW,
        updated_at: FIXED_NOW,
        status: shouldFail ? "failed" : "complete",
        source_type: sourceUrl ? "youtube" : "upload",
        source_url: sourceUrl,
        title: sourceUrl ? (shouldFail ? "Blocked YouTube Video" : "Mock Video Title") : null
      });
      jobErrors[id] = shouldFail
        ? "yt-dlp failed to download the requested YouTube URL. Requested format is not available."
        : null;
      chats[id] = [];
      await route.fulfill({
        json: {
          job_id: id,
          status: shouldFail ? "failed" : "queued",
          created_at: FIXED_NOW,
          updated_at: FIXED_NOW
        }
      });
      return;
    }

    await route.continue();
  });

  await page.route("**/api/jobs/*", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/").pop() as string;
    const job = jobs.find((item) => item.id === id);
    if (!job) {
      await route.fulfill({ status: 404, body: "Not found" });
      return;
    }

    await route.fulfill({
      json: {
        ...job,
        prefer_youtube_captions: true,
        allow_whisper_fallback: true,
        transcript_source: "youtube_auto_captions",
        captions_attempted: true,
        captions_status: "success",
        captions_detail: null,
        summary_object_key: `jobs/${id}/summary.txt`,
        transcript_object_key: `jobs/${id}/transcript.txt`,
        error: jobErrors[id] || null
      }
    });
  });

  await page.route("**/api/jobs/*/summary", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[3];
    const job = jobs.find((item) => item.id === id);
    if (job?.status === "failed") {
      await route.fulfill({ status: 404, body: "No summary yet" });
      return;
    }
    await route.fulfill({
      json: { text: "Mock summary", object_key: `jobs/${id}/summary.txt`, file_link: "#" }
    });
  });

  await page.route("**/api/jobs/*/transcript", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[3];
    const job = jobs.find((item) => item.id === id);
    if (job?.status === "failed") {
      await route.fulfill({ status: 404, body: "No transcript yet" });
      return;
    }
    await route.fulfill({
      json: { text: "Mock transcript text", object_key: `jobs/${id}/transcript.txt`, file_link: "#" }
    });
  });

  await page.route("**/api/jobs/*/chat", async (route) => {
    const method = route.request().method();
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[3];
    chats[id] ||= [];

    if (method === "GET") {
      await route.fulfill({ json: { job_id: id, messages: chats[id] } });
      return;
    }

    if (method === "POST") {
      const body = route.request().postDataJSON() as { message: string };
      chats[id].push({
        id: chats[id].length + 1,
        role: "user",
        content: body.message,
        created_at: FIXED_NOW
      });
      chats[id].push({
        id: chats[id].length + 1,
        role: "assistant",
        content: "Mock assistant answer",
        created_at: FIXED_NOW
      });
      await route.fulfill({
        json: { answer: "Mock assistant answer", context_stats: ["full_transcript_chars=20"] }
      });
      return;
    }

    await route.continue();
  });

  await page.route("**/api/search", async (route) => {
    await route.fulfill({
      json: {
        answer: "Mock global search answer",
        hits: [
          {
            job_id: "job-e2e-1",
            kind: "transcript",
            chunk_index: 0,
            file_link: "#",
            snippet: "Mock hit"
          }
        ]
      }
    });
  });
}
