import { FormEvent, useEffect, useMemo, useState } from "react";

type JobSummary = {
  id: string;
  created_at: string;
  updated_at: string;
  status: string;
  source_type: string;
  source_url?: string | null;
  title?: string | null;
};

type JobDetail = JobSummary & {
  prefer_youtube_captions: boolean;
  transcript_source?: string | null;
  captions_attempted?: boolean | null;
  captions_status?: string | null;
  captions_detail?: string | null;
  summary_object_key?: string | null;
  transcript_object_key?: string | null;
  error?: string | null;
};

type ArtifactResponse = {
  text: string;
  object_key?: string | null;
  file_link?: string | null;
};

type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

type ChatResponse = {
  job_id: string;
  messages: ChatMessage[];
};

type ChatAnswerResponse = {
  answer: string;
  context_stats: string[];
};

type SearchHit = {
  job_id: string;
  kind: string;
  chunk_index: number;
  file_path?: string | null;
  object_key?: string | null;
  file_link?: string | null;
  snippet: string;
};

type SearchResponse = {
  answer: string;
  hits: SearchHit[];
};

type PresignResponse = {
  object_key: string;
  upload_url: string;
  method: string;
  headers: Record<string, string>;
};

type CreateJobResponse = {
  job_id: string;
  status: string;
  created_at: string;
  updated_at: string;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {})
    }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text}`);
  }
  return (await response.json()) as T;
}

function fmtDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<"jobs" | "search">("jobs");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preferCaptions, setPreferCaptions] = useState(true);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string>("");
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const [summaryArtifact, setSummaryArtifact] = useState<ArtifactResponse | null>(null);
  const [transcriptArtifact, setTranscriptArtifact] = useState<ArtifactResponse | null>(null);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatContextStats, setChatContextStats] = useState<string[]>([]);
  const [searchQ, setSearchQ] = useState("");
  const [searchYoutubeUrl, setSearchYoutubeUrl] = useState("");
  const [searchCreatedAfter, setSearchCreatedAfter] = useState("");
  const [searchCreatedBefore, setSearchCreatedBefore] = useState("");
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [busySubmit, setBusySubmit] = useState(false);
  const [errorText, setErrorText] = useState("");

  const selectedJobRow = useMemo(() => jobs.find((job) => job.id === selectedJobId), [jobs, selectedJobId]);

  const loadJobs = async () => {
    setJobsLoading(true);
    try {
      const items = await api<JobSummary[]>("/api/jobs");
      setJobs(items);
      setSelectedJobId((currentSelected) => {
        if (!items.length) {
          return "";
        }
        if (currentSelected && items.some((job) => job.id === currentSelected)) {
          return currentSelected;
        }
        return items[0].id;
      });
    } finally {
      setJobsLoading(false);
    }
  };

  const loadJobData = async (jobId: string) => {
    const [detail, chatResponse] = await Promise.all([
      api<JobDetail>(`/api/jobs/${jobId}`),
      api<ChatResponse>(`/api/jobs/${jobId}/chat`).catch(() => ({ job_id: jobId, messages: [] as ChatMessage[] }))
    ]);
    setSelectedJob(detail);
    setChat(chatResponse.messages);
    setChatContextStats([]);

    try {
      const artifact = await api<ArtifactResponse>(`/api/jobs/${jobId}/summary`);
      setSummaryArtifact(artifact);
    } catch {
      setSummaryArtifact(null);
    }
    try {
      const artifact = await api<ArtifactResponse>(`/api/jobs/${jobId}/transcript`);
      setTranscriptArtifact(artifact);
    } catch {
      setTranscriptArtifact(null);
    }
  };

  useEffect(() => {
    loadJobs().catch((error) => setErrorText(String(error)));
    const timer = window.setInterval(() => {
      loadJobs().catch((error) => setErrorText(String(error)));
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!selectedJobId) {
      setSelectedJob(null);
      setSummaryArtifact(null);
      setTranscriptArtifact(null);
      setChat([]);
      return;
    }
    loadJobData(selectedJobId).catch((error) => setErrorText(String(error)));
  }, [selectedJobId]);

  useEffect(() => {
    if (!selectedJobId) {
      return;
    }
    const timer = window.setInterval(() => {
      loadJobData(selectedJobId).catch((error) => setErrorText(String(error)));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [selectedJobId]);

  const submitJob = async (event: FormEvent) => {
    event.preventDefault();
    setErrorText("");
    setBusySubmit(true);
    try {
      const trimmedYoutubeUrl = youtubeUrl.trim();
      if (!trimmedYoutubeUrl && !file) {
        throw new Error("Provide either a YouTube URL or a file upload.");
      }
      if (trimmedYoutubeUrl && file) {
        throw new Error("Provide only one source: YouTube URL or file upload.");
      }

      let uploadedObjectKey: string | undefined = undefined;
      if (file) {
        const presign = await api<PresignResponse>("/api/uploads/presign", {
          method: "POST",
          body: JSON.stringify({
            filename: file.name,
            mime_type: file.type || "application/octet-stream"
          })
        });
        const uploadResponse = await fetch(presign.upload_url, {
          method: presign.method || "PUT",
          headers: presign.headers || {},
          body: file
        });
        if (!uploadResponse.ok) {
          throw new Error(`Upload failed: ${uploadResponse.status} ${await uploadResponse.text()}`);
        }
        uploadedObjectKey = presign.object_key;
      }

      const created = await api<CreateJobResponse>("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          youtube_url: trimmedYoutubeUrl || undefined,
          uploaded_object_key: uploadedObjectKey,
          prefer_youtube_captions: preferCaptions
        })
      });
      setYoutubeUrl("");
      setFile(null);
      await loadJobs();
      setSelectedJobId(created.job_id);
      setActiveTab("jobs");
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setBusySubmit(false);
    }
  };

  const sendJobChat = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedJobId || !chatInput.trim()) {
      return;
    }
    setErrorText("");
    setChatBusy(true);
    try {
      const message = chatInput.trim();
      setChatInput("");
      setChat((prev) => [
        ...prev,
        {
          id: Date.now(),
          role: "user",
          content: message,
          created_at: new Date().toISOString()
        }
      ]);
      const answer = await api<ChatAnswerResponse>(`/api/jobs/${selectedJobId}/chat`, {
        method: "POST",
        body: JSON.stringify({ message })
      });
      setChatContextStats(answer.context_stats || []);
      await loadJobData(selectedJobId);
    } catch (error) {
      setErrorText(String(error));
    } finally {
      setChatBusy(false);
    }
  };

  const runSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!searchQ.trim()) {
      return;
    }
    setErrorText("");
    try {
      const payload = {
        question: searchQ.trim(),
        youtube_url: searchYoutubeUrl.trim() || undefined,
        created_after: searchCreatedAfter ? `${searchCreatedAfter}T00:00:00Z` : undefined,
        created_before: searchCreatedBefore ? `${searchCreatedBefore}T23:59:59Z` : undefined
      };
      const result = await api<SearchResponse>("/api/search", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      setSearchResult(result);
      setActiveTab("search");
    } catch (error) {
      setErrorText(String(error));
    }
  };

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>Audio Summarizer</h1>
          <p className="muted">FastAPI + React migration UI</p>
        </div>
        <div className="tabbar">
          <button
            type="button"
            className={activeTab === "jobs" ? "tab active" : "tab"}
            onClick={() => setActiveTab("jobs")}
          >
            Jobs
          </button>
          <button
            type="button"
            className={activeTab === "search" ? "tab active" : "tab"}
            onClick={() => setActiveTab("search")}
          >
            Global Search
          </button>
        </div>
      </header>

      {errorText && <div className="error">Error: {errorText}</div>}

      <section className="card">
        <h2>Submit Job</h2>
        <form className="submit-grid" onSubmit={submitJob}>
          <div>
            <label htmlFor="youtube-url-input">YouTube URL</label>
            <input
              id="youtube-url-input"
              value={youtubeUrl}
              onChange={(event) => setYoutubeUrl(event.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
            />
          </div>
          <div>
            <label htmlFor="upload-file-input">Upload file</label>
            <input
              id="upload-file-input"
              type="file"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
            />
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={preferCaptions}
              onChange={(event) => setPreferCaptions(event.target.checked)}
            />
            Prefer YouTube captions (fallback Whisper)
          </label>
          <button type="submit" disabled={busySubmit}>
            {busySubmit ? "Submitting..." : "Submit"}
          </button>
        </form>
      </section>

      {activeTab === "jobs" && (
        <section className="content-grid">
          <article className="card">
            <div className="row-between">
              <h2>Jobs</h2>
              <span className="muted">{jobsLoading ? "Refreshing..." : `${jobs.length} total`}</span>
            </div>
            <div className="job-table-wrap">
              <table className="job-table">
                <thead>
                  <tr>
                    <th>Created</th>
                    <th>Title / Source</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr
                      key={job.id}
                      className={selectedJobId === job.id ? "selected" : ""}
                      onClick={() => setSelectedJobId(job.id)}
                    >
                      <td>{fmtDate(job.created_at)}</td>
                      <td>
                        <div>{job.title || job.source_url || job.id}</div>
                        <div className="muted mono">{job.id}</div>
                      </td>
                      <td>{job.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <article className="card">
            <h2>Job Detail</h2>
            {!selectedJobId ? (
              <p className="muted">Select a job from the table.</p>
            ) : (
              <>
                <div className="detail-grid">
                  <div>
                    <div className="muted">Job ID</div>
                    <div className="mono">{selectedJobId}</div>
                  </div>
                  <div>
                    <div className="muted">Created</div>
                    <div>{fmtDate(selectedJob?.created_at || selectedJobRow?.created_at)}</div>
                  </div>
                  <div>
                    <div className="muted">Status</div>
                    <div>{selectedJob?.status || selectedJobRow?.status || "-"}</div>
                  </div>
                  <div>
                    <div className="muted">Transcript Source</div>
                    <div>{selectedJob?.transcript_source || "-"}</div>
                  </div>
                  <div>
                    <div className="muted">Source URL</div>
                    <div className="source-url">
                      {selectedJob?.source_url || selectedJobRow?.source_url ? (
                        <a
                          href={selectedJob?.source_url || selectedJobRow?.source_url || "#"}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {selectedJob?.source_url || selectedJobRow?.source_url}
                        </a>
                      ) : (
                        "-"
                      )}
                    </div>
                  </div>
                </div>

                {selectedJob?.error && <div className="error">Job error: {selectedJob.error}</div>}

                <h3>Summary</h3>
                {summaryArtifact?.file_link && (
                  <p>
                    <a href={summaryArtifact.file_link} target="_blank" rel="noreferrer">
                      Open summary file
                    </a>
                  </p>
                )}
                <pre>{summaryArtifact?.text || "(no summary yet)"}</pre>

                <h3>Transcript</h3>
                {transcriptArtifact?.file_link && (
                  <p>
                    <a href={transcriptArtifact.file_link} target="_blank" rel="noreferrer">
                      Open transcript file
                    </a>
                  </p>
                )}
                <pre>{transcriptArtifact?.text || "(no transcript yet)"}</pre>

                <h3>Job Chat</h3>
                <div className="chat-box">
                  {chat.map((msg) => (
                    <div key={msg.id} className={`chat-msg ${msg.role}`}>
                      <strong>{msg.role}:</strong> {msg.content}
                    </div>
                  ))}
                  {chat.length === 0 && <div className="muted">(no chat messages yet)</div>}
                </div>
                <form className="chat-row" onSubmit={sendJobChat}>
                  <input
                    value={chatInput}
                    onChange={(event) => setChatInput(event.target.value)}
                    placeholder="Ask about this transcript..."
                  />
                  <button type="submit" disabled={chatBusy || !selectedJobId}>
                    {chatBusy ? "Sending..." : "Send"}
                  </button>
                </form>
                {chatContextStats.length > 0 && (
                  <div className="muted small">Context: {chatContextStats.join(", ")}</div>
                )}
              </>
            )}
          </article>
        </section>
      )}

      {activeTab === "search" && (
        <section className="card">
          <h2>Global Search</h2>
          <form className="search-grid" onSubmit={runSearch}>
            <div>
              <label htmlFor="search-question-input">Question</label>
              <input
                id="search-question-input"
                value={searchQ}
                onChange={(event) => setSearchQ(event.target.value)}
                placeholder="What topics were discussed about baseball?"
              />
            </div>
            <div>
              <label htmlFor="search-youtube-url-input">Filter by YouTube URL (optional)</label>
              <input
                id="search-youtube-url-input"
                value={searchYoutubeUrl}
                onChange={(event) => setSearchYoutubeUrl(event.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
              />
            </div>
            <div>
              <label htmlFor="search-created-after-input">Created after (optional)</label>
              <input
                id="search-created-after-input"
                type="date"
                value={searchCreatedAfter}
                onChange={(event) => setSearchCreatedAfter(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="search-created-before-input">Created before (optional)</label>
              <input
                id="search-created-before-input"
                type="date"
                value={searchCreatedBefore}
                onChange={(event) => setSearchCreatedBefore(event.target.value)}
              />
            </div>
            <button type="submit">Run Search</button>
          </form>

          <h3>Answer</h3>
          <pre>{searchResult?.answer || "(no answer yet)"}</pre>

          <h3>Matched Chunks</h3>
          <div className="search-hits">
            {searchResult?.hits?.map((hit, index) => (
              <div key={`${hit.job_id}:${hit.kind}:${hit.chunk_index}:${index}`} className="hit">
                <div className="hit-meta">
                  <span className="mono">{hit.job_id}</span>
                  <span>
                    {hit.kind} #{hit.chunk_index}
                  </span>
                  {hit.file_link && (
                    <a href={hit.file_link} target="_blank" rel="noreferrer">
                      Open source
                    </a>
                  )}
                </div>
                <div>{hit.snippet}</div>
              </div>
            ))}
            {!searchResult?.hits?.length && <div className="muted">(no chunks yet)</div>}
          </div>
        </section>
      )}
    </div>
  );
}
