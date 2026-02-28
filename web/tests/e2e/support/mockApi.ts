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
  let jobCounter = 0;

  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      json: { email: "e2e@example.com", name: "E2E User", picture: null }
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
      jobs.unshift({
        id,
        created_at: FIXED_NOW,
        updated_at: FIXED_NOW,
        status: "complete",
        source_type: body.youtube_url ? "youtube" : "upload",
        source_url: body.youtube_url || null,
        title: "Mock Video Title"
      });
      chats[id] = [];
      await route.fulfill({
        json: {
          job_id: id,
          status: "queued",
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
        error: null
      }
    });
  });

  await page.route("**/api/jobs/*/summary", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[3];
    await route.fulfill({
      json: { text: "Mock summary", object_key: `jobs/${id}/summary.txt`, file_link: "#" }
    });
  });

  await page.route("**/api/jobs/*/transcript", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/")[3];
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
