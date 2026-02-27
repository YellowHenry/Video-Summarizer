import { expect, test } from "@playwright/test";

test("submit job flow + persisted job chat rendering", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("audio_summarizer_auth_token", "test-token");
  });

  const jobs: Array<{
    id: string;
    created_at: string;
    updated_at: string;
    status: string;
    source_type: string;
    source_url: string | null;
    title: string | null;
  }> = [];
  const chats: Record<string, Array<{ id: number; role: "user" | "assistant"; content: string; created_at: string }>> = {};

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
      const body = route.request().postDataJSON() as Record<string, unknown>;
      const now = new Date().toISOString();
      const id = "job-e2e-1";
      jobs.unshift({
        id,
        created_at: now,
        updated_at: now,
        status: "complete",
        source_type: body.youtube_url ? "youtube" : "upload",
        source_url: (body.youtube_url as string) || null,
        title: "Mock Video Title"
      });
      chats[id] = [];
      await route.fulfill({
        json: {
          job_id: id,
          status: "queued",
          created_at: now,
          updated_at: now
        }
      });
      return;
    }
    await route.continue();
  });

  await page.route("**/api/jobs/*", async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/").pop() as string;
    if (!jobs.find((job) => job.id === id)) {
      await route.fulfill({ status: 404, body: "Not found" });
      return;
    }
    const job = jobs.find((item) => item.id === id)!;
    await route.fulfill({
      json: {
        ...job,
        prefer_youtube_captions: true,
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
    await route.fulfill({ json: { text: "Mock summary", object_key: "jobs/job-e2e-1/summary.txt", file_link: "#" } });
  });

  await page.route("**/api/jobs/*/transcript", async (route) => {
    await route.fulfill({
      json: { text: "Mock transcript text", object_key: "jobs/job-e2e-1/transcript.txt", file_link: "#" }
    });
  });

  await page.route("**/api/jobs/*/chat", async (route) => {
    const method = route.request().method();
    const url = new URL(route.request().url());
    const segments = url.pathname.split("/");
    const id = segments[3];
    chats[id] ||= [];

    if (method === "GET") {
      await route.fulfill({ json: { job_id: id, messages: chats[id] } });
      return;
    }

    if (method === "POST") {
      const body = route.request().postDataJSON() as { message: string };
      const now = new Date().toISOString();
      chats[id].push({ id: chats[id].length + 1, role: "user", content: body.message, created_at: now });
      chats[id].push({
        id: chats[id].length + 1,
        role: "assistant",
        content: "Mock assistant answer",
        created_at: now
      });
      await route.fulfill({ json: { answer: "Mock assistant answer", context_stats: ["full_transcript_chars=20"] } });
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

  await page.goto("/");
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/watch?v=e2e123");
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText("Mock Video Title")).toBeVisible();
  await expect(page.getByText("Mock summary")).toBeVisible();

  await page.getByPlaceholder("Ask about this transcript...").fill("What happened?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Mock assistant answer")).toBeVisible();
});
