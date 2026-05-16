import { FormEvent, MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import MarkdownBlock from "./components/MarkdownBlock";

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
  allow_whisper_fallback: boolean;
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

type AuthMeResponse = {
  email: string;
  name?: string | null;
  picture?: string | null;
};

type DigestSettingsResponse = {
  enabled: boolean;
  cadence: "daily" | "weekly";
  timezone: string;
  send_hour_local: number;
  weekly_weekday: number;
  recipient_email: string;
  delivery_available: boolean;
  delivery_reason?: string | null;
  next_send_at?: string | null;
  last_run_at?: string | null;
  last_run_status?: string | null;
  last_sent_at?: string | null;
  profile_summary?: string | null;
  profile_updated_at?: string | null;
  historical_backfill_pending: boolean;
};

type UpdateDigestSettingsRequest = {
  enabled: boolean;
  cadence: "daily" | "weekly";
  timezone: string;
};

type DigestRunOut = {
  id: number;
  status: string;
  cadence: string;
  job_count: number;
  subject?: string | null;
  window_start_at?: string | null;
  window_end_at?: string | null;
  created_at: string;
  sent_at?: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
const AUTH_TOKEN_STORAGE_KEY = "audio_summarizer_auth_token";

class ApiError extends Error {
  status: number;

  body: string;

  constructor(status: number, body: string) {
    super(`${status}: ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const parsed = JSON.parse(error.body);
      if (parsed && typeof parsed.detail === "string") {
        return parsed.detail;
      }
    } catch {
      return error.body;
    }
    return error.body;
  }
  return String(error);
}

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function api<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text);
  }
  return (await response.json()) as T;
}

async function apiWithRetry<T>(
  path: string,
  token: string,
  init?: RequestInit,
  attempts = 2
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await api<T>(path, token, init);
    } catch (error) {
      lastError = error;
      const isFetchNetworkError =
        error instanceof TypeError && String(error.message || "").toLowerCase().includes("fetch");
      const isApi5xx = error instanceof ApiError && error.status >= 500;
      const shouldRetry = attempt < attempts && (isFetchNetworkError || isApi5xx);
      if (!shouldRetry) {
        throw error;
      }
      await sleep(450 * attempt);
    }
  }
  throw lastError;
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

function fmtListDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "numeric",
      day: "numeric",
      year: "2-digit",
      hour: "numeric",
      minute: "2-digit"
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function isApiArtifactLink(link: string): boolean {
  try {
    const resolved = new URL(link, window.location.origin);
    const apiOrigin = new URL(API_BASE, window.location.origin).origin;
    return resolved.origin === apiOrigin && resolved.pathname.startsWith("/api/artifacts/");
  } catch {
    return link.includes("/api/artifacts/");
  }
}

function extractYouTubeVideoId(rawUrl: string | null | undefined): string | null {
  if (!rawUrl) {
    return null;
  }
  try {
    const parsed = new URL(rawUrl);
    const host = parsed.hostname.replace(/^www\./, "").toLowerCase();
    if (host === "youtu.be") {
      const id = parsed.pathname.split("/").filter(Boolean)[0];
      return id || null;
    }
    if (host === "youtube.com" || host === "m.youtube.com") {
      if (parsed.pathname === "/watch") {
        return parsed.searchParams.get("v");
      }
      if (parsed.pathname.startsWith("/shorts/") || parsed.pathname.startsWith("/embed/")) {
        const id = parsed.pathname.split("/").filter(Boolean)[1];
        return id || null;
      }
    }
    return null;
  } catch {
    return null;
  }
}

function looksLikeUrl(value: string | null | undefined): boolean {
  if (!value) {
    return false;
  }
  const trimmed = value.trim().toLowerCase();
  return trimmed.startsWith("http://") || trimmed.startsWith("https://") || trimmed.startsWith("www.");
}

function parseInitialJobQuery(): { jobId: string; tab: "summary" | "transcript" | "chat" } {
  try {
    const params = new URLSearchParams(window.location.search);
    const rawTab = params.get("tab");
    const tab =
      rawTab === "transcript" || rawTab === "chat" || rawTab === "summary" ? rawTab : "summary";
    return { jobId: params.get("job") || "", tab };
  } catch {
    return { jobId: "", tab: "summary" };
  }
}

const JOB_PROGRESS_STAGES = [
  { key: "queued", label: "Queued", helper: "Waiting for a worker." },
  { key: "downloading", label: "Downloading", helper: "Fetching media or captions." },
  { key: "preprocessing", label: "Prepping", helper: "Normalizing audio and inputs." },
  { key: "summarizing", label: "Summarizing", helper: "Building transcript and summary." },
  { key: "complete", label: "Ready", helper: "Summary, transcript, and chat are ready." },
  { key: "failed", label: "Needs attention", helper: "The job stopped before completion." }
] as const;

type JobProgressStage = (typeof JOB_PROGRESS_STAGES)[number]["key"];

function normalizeJobStatus(status: string | null | undefined): JobProgressStage {
  const normalized = (status || "").trim().toLowerCase();
  if (normalized === "failed" || normalized === "error") {
    return "failed";
  }
  if (normalized === "complete" || normalized === "completed" || normalized === "done") {
    return "complete";
  }
  if (normalized === "summarizing" || normalized === "transcribing") {
    return "summarizing";
  }
  if (normalized === "preprocessing" || normalized === "processing") {
    return "preprocessing";
  }
  if (normalized === "downloading") {
    return "downloading";
  }
  return "queued";
}

function transcriptRouteLabel(job: JobDetail | null): string {
  if (!job) {
    return "Transcript route pending";
  }
  if (job.transcript_source === "youtube_subtitles") {
    return "Manual YouTube captions";
  }
  if (job.transcript_source === "youtube_auto_captions") {
    return "YouTube auto captions";
  }
  if (job.transcript_source === "whisper") {
    return "Whisper fallback";
  }
  if (job.prefer_youtube_captions) {
    return job.allow_whisper_fallback ? "Captions first, Whisper fallback allowed" : "Captions only";
  }
  return "Whisper transcription";
}

function friendlyFailureMessage(error: string | null | undefined): string {
  const detail = (error || "").toLowerCase();
  if (detail.includes("yt-dlp") || detail.includes("youtube") || detail.includes("requested format")) {
    return "YouTube did not provide a usable transcript or downloadable audio format for this run. This can happen when YouTube changes its player challenge, rate-limits the worker, or only exposes image/storyboard formats.";
  }
  if (detail.includes("openai") || detail.includes("api key") || detail.includes("model")) {
    return "The AI step could not complete. The source was accepted, but the summarization provider returned an error.";
  }
  if (detail.includes("timeout")) {
    return "The job took too long and timed out before the summary was ready.";
  }
  return "This job stopped before a summary could be generated. The technical detail is preserved below for debugging.";
}

function summaryEmptyCopy(status: string | null | undefined): { title: string; body: string } {
  const stage = normalizeJobStatus(status);
  if (stage === "failed") {
    return {
      title: "No summary was generated",
      body: "The job failed before the AI summary was ready. Try again, or open Debug info for the exact technical failure."
    };
  }
  if (stage === "complete") {
    return {
      title: "Summary artifact is missing",
      body: "The job is marked complete, but the summary file was not available. Refresh or check Debug info."
    };
  }
  return {
    title: "Summary is not ready yet",
    body: "The worker is still processing this job. The page refreshes automatically while it moves through the timeline."
  };
}

export default function App() {
  const initialJobQuery = useMemo(() => parseInitialJobQuery(), []);
  const [authToken, setAuthToken] = useState<string>(() => localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || "");
  const [authProfile, setAuthProfile] = useState<AuthMeResponse | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [googleReady, setGoogleReady] = useState(false);
  const googleButtonRef = useRef<HTMLDivElement | null>(null);

  const [activeTab, setActiveTab] = useState<"jobs" | "search">("jobs");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preferCaptions, setPreferCaptions] = useState(true);
  const [allowWhisperFallback, setAllowWhisperFallback] = useState(true);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string>("");
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const [summaryArtifact, setSummaryArtifact] = useState<ArtifactResponse | null>(null);
  const [transcriptArtifact, setTranscriptArtifact] = useState<ArtifactResponse | null>(null);
  const [jobDataLoading, setJobDataLoading] = useState(false);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatContextStats, setChatContextStats] = useState<string[]>([]);
  const [searchQ, setSearchQ] = useState("");
  const [searchYoutubeUrl, setSearchYoutubeUrl] = useState("");
  const [searchCreatedAfter, setSearchCreatedAfter] = useState("");
  const [searchCreatedBefore, setSearchCreatedBefore] = useState("");
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [busySubmit, setBusySubmit] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [errorText, setErrorText] = useState("");
  const [jobDetailTab, setJobDetailTab] = useState<"summary" | "transcript" | "chat">("summary");
  const [isJobListVisible, setIsJobListVisible] = useState(true);
  const detectedTimeZone = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    []
  );
  const pendingUrlJobIdRef = useRef<string>(initialJobQuery.jobId);
  const pendingUrlTabRef = useRef<"summary" | "transcript" | "chat" | null>(
    initialJobQuery.jobId ? initialJobQuery.tab : null
  );
  const [digestSettings, setDigestSettings] = useState<DigestSettingsResponse | null>(null);
  const [digestHistory, setDigestHistory] = useState<DigestRunOut[]>([]);
  const [digestEnabled, setDigestEnabled] = useState(false);
  const [digestCadence, setDigestCadence] = useState<"daily" | "weekly">("daily");
  const [digestTimezone, setDigestTimezone] = useState(detectedTimeZone);
  const [digestBusy, setDigestBusy] = useState(false);
  const [digestError, setDigestError] = useState("");
  const [digestSaveNotice, setDigestSaveNotice] = useState("");

  const clearSession = () => {
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    setAuthToken("");
    setAuthProfile(null);
    setJobs([]);
    setSelectedJobId("");
    setSelectedJob(null);
    setSummaryArtifact(null);
    setTranscriptArtifact(null);
    setJobDataLoading(false);
    setArtifactsLoading(false);
    setChat([]);
    setDigestSettings(null);
    setDigestHistory([]);
    setDigestError("");
    setDigestSaveNotice("");
  };

  const handleApiError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) {
      clearSession();
      setAuthError("Session expired. Please sign in again.");
      return;
    }
    if (error instanceof TypeError && String(error.message || "").toLowerCase().includes("fetch")) {
      setErrorText("Network error while contacting the API. Please retry.");
      return;
    }
    setErrorText(apiErrorMessage(error));
  };

  const selectedJobRow = useMemo(() => jobs.find((job) => job.id === selectedJobId), [jobs, selectedJobId]);
  const totalJobsLabel = useMemo(() => jobs.length.toLocaleString("en-US"), [jobs.length]);
  const completedJobsLabel = useMemo(
    () => jobs.filter((job) => String(job.status).toLowerCase() === "complete").length.toLocaleString("en-US"),
    [jobs]
  );

  const displayJobTitle = (job: JobSummary): string => {
    if (job.title && job.title.trim() && !looksLikeUrl(job.title)) {
      return job.title.trim();
    }
    const videoId = extractYouTubeVideoId(job.source_url);
    if (videoId) {
      return `YouTube video (${videoId})`;
    }
    return job.source_url || "(untitled job)";
  };
  const selectedDisplayTitle = useMemo(() => {
    const jobForTitle = selectedJob || selectedJobRow;
    return jobForTitle ? displayJobTitle(jobForTitle) : "(no selected job)";
  }, [selectedJob, selectedJobRow]);
  const selectedStatus = selectedJob?.status || selectedJobRow?.status || "";
  const selectedStage = normalizeJobStatus(selectedStatus);
  const selectedStageIndex = JOB_PROGRESS_STAGES.findIndex((stage) => stage.key === selectedStage);
  const selectedUpdatedAt = selectedJob?.updated_at || selectedJobRow?.updated_at || "";
  const selectedSourceUrl = selectedJob?.source_url || selectedJobRow?.source_url || "";
  const canRetrySelectedJob = selectedStage === "failed" && Boolean(selectedSourceUrl);

  useEffect(() => {
    if (window.google?.accounts?.id) {
      setGoogleReady(true);
      return;
    }
    const timer = window.setInterval(() => {
      if (window.google?.accounts?.id) {
        setGoogleReady(true);
        window.clearInterval(timer);
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let canceled = false;
    if (!authToken) {
      setAuthProfile(null);
      setAuthReady(true);
      return () => {
        canceled = true;
      };
    }

    setAuthReady(false);
    api<AuthMeResponse>("/api/auth/me", authToken)
      .then((profile) => {
        if (canceled) {
          return;
        }
        setAuthProfile(profile);
        setAuthError("");
      })
      .catch((error) => {
        if (canceled) {
          return;
        }
        clearSession();
        setAuthError(`Sign-in required: ${String(error)}`);
      })
      .finally(() => {
        if (!canceled) {
          setAuthReady(true);
        }
      });

    return () => {
      canceled = true;
    };
  }, [authToken]);

  useEffect(() => {
    if (!authReady || authProfile || authToken) {
      return;
    }
    if (!GOOGLE_CLIENT_ID) {
      setAuthError("VITE_GOOGLE_CLIENT_ID is not configured.");
      return;
    }
    if (!googleReady || !googleButtonRef.current || !window.google?.accounts?.id) {
      return;
    }

    const handleCredential = (response: { credential?: string }) => {
      const credential = response.credential || "";
      if (!credential) {
        setAuthError("Google sign-in did not return a credential.");
        return;
      }
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, credential);
      setAuthToken(credential);
      setAuthError("");
      setErrorText("");
    };

    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleCredential,
      auto_select: false,
      cancel_on_tap_outside: true
    });
    googleButtonRef.current.innerHTML = "";
    window.google.accounts.id.renderButton(googleButtonRef.current, {
      type: "standard",
      theme: "filled_blue",
      size: "large",
      shape: "pill",
      text: "signin_with",
      width: 320
    });
  }, [authReady, authProfile, authToken, googleReady]);

  const loadJobs = async () => {
    if (!authToken) {
      return;
    }
    setJobsLoading(true);
    try {
      const items = await api<JobSummary[]>("/api/jobs", authToken);
      setJobs(items);
      setSelectedJobId((currentSelected) => {
        if (!items.length) {
          return "";
        }
        if (currentSelected && items.some((job) => job.id === currentSelected)) {
          return currentSelected;
        }
        if (pendingUrlJobIdRef.current && items.some((job) => job.id === pendingUrlJobIdRef.current)) {
          return pendingUrlJobIdRef.current;
        }
        pendingUrlJobIdRef.current = "";
        pendingUrlTabRef.current = null;
        return items[0].id;
      });
    } finally {
      setJobsLoading(false);
    }
  };

  const loadDigestData = async () => {
    if (!authToken) {
      return;
    }
    const [settingsResponse, historyResponse] = await Promise.all([
      api<DigestSettingsResponse>("/api/digests/settings", authToken),
      api<DigestRunOut[]>("/api/digests/history", authToken)
    ]);
    setDigestSettings(settingsResponse);
    setDigestHistory(historyResponse);
    setDigestEnabled(settingsResponse.enabled);
    setDigestCadence(settingsResponse.cadence);
    setDigestTimezone(settingsResponse.enabled ? settingsResponse.timezone || detectedTimeZone : detectedTimeZone);
  };

  const loadJobData = async (jobId: string, options: { showLoading?: boolean } = {}) => {
    if (!authToken) {
      return;
    }
    if (options.showLoading) {
      setJobDataLoading(true);
      setArtifactsLoading(true);
    }
    try {
      const [detail, chatResponse] = await Promise.all([
        api<JobDetail>(`/api/jobs/${jobId}`, authToken),
        api<ChatResponse>(`/api/jobs/${jobId}/chat`, authToken).catch(() => ({
          job_id: jobId,
          messages: [] as ChatMessage[]
        }))
      ]);
      setSelectedJob(detail);
      setChat(chatResponse.messages);
      setChatContextStats([]);
    } finally {
      if (options.showLoading) {
        setJobDataLoading(false);
      }
    }

    if (options.showLoading) {
      setArtifactsLoading(true);
    }
    try {
      const [summary, transcript] = await Promise.all([
        api<ArtifactResponse>(`/api/jobs/${jobId}/summary`, authToken).catch(() => null),
        api<ArtifactResponse>(`/api/jobs/${jobId}/transcript`, authToken).catch(() => null)
      ]);
      setSummaryArtifact(summary);
      setTranscriptArtifact(transcript);
    } finally {
      if (options.showLoading) {
        setArtifactsLoading(false);
      }
    }
  };

  useEffect(() => {
    if (!authProfile || !authToken) {
      return () => undefined;
    }
    loadJobs().catch(handleApiError);
    loadDigestData().catch(handleApiError);
    const timer = window.setInterval(() => {
      loadJobs().catch(handleApiError);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [authProfile, authToken]);

  useEffect(() => {
    if (!authProfile || !authToken) {
      return;
    }
    if (!selectedJobId) {
      setSelectedJob(null);
      setSummaryArtifact(null);
      setTranscriptArtifact(null);
      setJobDataLoading(false);
      setArtifactsLoading(false);
      setChat([]);
      setJobDetailTab("summary");
      return;
    }
    if (pendingUrlTabRef.current && selectedJobId === pendingUrlJobIdRef.current) {
      setJobDetailTab(pendingUrlTabRef.current);
      pendingUrlJobIdRef.current = "";
      pendingUrlTabRef.current = null;
    } else {
      setJobDetailTab("summary");
    }
    setSummaryArtifact(null);
    setTranscriptArtifact(null);
    loadJobData(selectedJobId, { showLoading: true }).catch(handleApiError);
  }, [authProfile, authToken, selectedJobId]);

  useEffect(() => {
    if (!authProfile || !authToken || !selectedJobId) {
      return;
    }
    const timer = window.setInterval(() => {
      loadJobData(selectedJobId).catch(handleApiError);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [authProfile, authToken, selectedJobId]);

  useEffect(() => {
    if (!authReady) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (selectedJobId) {
      params.set("job", selectedJobId);
      params.set("tab", jobDetailTab);
    } else {
      params.delete("job");
      params.delete("tab");
    }
    const nextQuery = params.toString();
    const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}`;
    window.history.replaceState({}, "", nextUrl);
  }, [authReady, selectedJobId, jobDetailTab]);

  const submitJob = async (event: FormEvent) => {
    event.preventDefault();
    if (!authToken) {
      setAuthError("Sign in is required before submitting jobs.");
      return;
    }
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
        const presign = await api<PresignResponse>("/api/uploads/presign", authToken, {
          method: "POST",
          body: JSON.stringify({
            filename: file.name,
            mime_type: file.type || "application/octet-stream"
          })
        });
        const uploadHeaders: Record<string, string> = { ...(presign.headers || {}) };
        if (presign.upload_url.startsWith(API_BASE)) {
          uploadHeaders.Authorization = `Bearer ${authToken}`;
        }
        const uploadResponse = await fetch(presign.upload_url, {
          method: presign.method || "PUT",
          headers: uploadHeaders,
          body: file
        });
        if (!uploadResponse.ok) {
          throw new Error(`Upload failed: ${uploadResponse.status} ${await uploadResponse.text()}`);
        }
        uploadedObjectKey = presign.object_key;
      }

      const created = await api<CreateJobResponse>("/api/jobs", authToken, {
        method: "POST",
        body: JSON.stringify({
          youtube_url: trimmedYoutubeUrl || undefined,
          uploaded_object_key: uploadedObjectKey,
          prefer_youtube_captions: preferCaptions,
          allow_whisper_fallback: preferCaptions ? allowWhisperFallback : true
        })
      });
      setYoutubeUrl("");
      setFile(null);
      await loadJobs();
      setSelectedJobId(created.job_id);
      setActiveTab("jobs");
    } catch (error) {
      handleApiError(error);
    } finally {
      setBusySubmit(false);
    }
  };

  const retrySelectedJob = async () => {
    if (!authToken || !selectedSourceUrl) {
      return;
    }
    setErrorText("");
    setRetryBusy(true);
    try {
      const created = await api<CreateJobResponse>("/api/jobs", authToken, {
        method: "POST",
        body: JSON.stringify({
          youtube_url: selectedSourceUrl,
          prefer_youtube_captions: selectedJob?.prefer_youtube_captions ?? preferCaptions,
          allow_whisper_fallback:
            selectedJob?.prefer_youtube_captions === false
              ? true
              : selectedJob?.allow_whisper_fallback ?? allowWhisperFallback
        })
      });
      await loadJobs();
      setSelectedJobId(created.job_id);
      setJobDetailTab("summary");
    } catch (error) {
      handleApiError(error);
    } finally {
      setRetryBusy(false);
    }
  };

  const sendJobChat = async (event: FormEvent) => {
    event.preventDefault();
    if (!authToken || !selectedJobId || !chatInput.trim()) {
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
      const answer = await apiWithRetry<ChatAnswerResponse>(`/api/jobs/${selectedJobId}/chat`, authToken, {
        method: "POST",
        body: JSON.stringify({ message })
      });
      setChatContextStats(answer.context_stats || []);
      await loadJobData(selectedJobId);
    } catch (error) {
      handleApiError(error);
    } finally {
      setChatBusy(false);
    }
  };

  const saveDigestSettings = async (event: FormEvent) => {
    event.preventDefault();
    if (!authToken) {
      setAuthError("Sign in is required before updating digest settings.");
      return;
    }
    setDigestBusy(true);
    setDigestError("");
    setDigestSaveNotice("");
    try {
      const payload: UpdateDigestSettingsRequest = {
        enabled: digestEnabled,
        cadence: digestCadence,
        timezone: digestTimezone || detectedTimeZone || "UTC"
      };
      const updated = await api<DigestSettingsResponse>("/api/digests/settings", authToken, {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      setDigestSettings(updated);
      setDigestEnabled(updated.enabled);
      setDigestCadence(updated.cadence);
      setDigestTimezone(updated.timezone || detectedTimeZone);
      setDigestHistory(await api<DigestRunOut[]>("/api/digests/history", authToken));
      setDigestSaveNotice("Digest settings saved.");
    } catch (error) {
      setDigestError(apiErrorMessage(error));
    } finally {
      setDigestBusy(false);
    }
  };

  const runSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!authToken || !searchQ.trim()) {
      return;
    }
    setErrorText("");
    setSearchBusy(true);
    try {
      const payload = {
        question: searchQ.trim(),
        youtube_url: searchYoutubeUrl.trim() || undefined,
        created_after: searchCreatedAfter ? `${searchCreatedAfter}T00:00:00Z` : undefined,
        created_before: searchCreatedBefore ? `${searchCreatedBefore}T23:59:59Z` : undefined
      };
      const result = await api<SearchResponse>("/api/search", authToken, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      setSearchResult(result);
      setActiveTab("search");
    } catch (error) {
      handleApiError(error);
    } finally {
      setSearchBusy(false);
    }
  };

  const openArtifactLink = async (event: MouseEvent<HTMLAnchorElement>, fileLink: string | null | undefined) => {
    if (!fileLink) {
      return;
    }
    if (!isApiArtifactLink(fileLink)) {
      return;
    }
    event.preventDefault();
    if (!authToken) {
      setAuthError("Sign in required to open private artifacts.");
      return;
    }
    try {
      const response = await fetch(fileLink, {
        headers: {
          Authorization: `Bearer ${authToken}`
        }
      });
      if (!response.ok) {
        throw new Error(`${response.status}: ${await response.text()}`);
      }
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      window.open(blobUrl, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
    } catch (error) {
      handleApiError(error);
    }
  };

  if (!authReady) {
    return (
      <div className="auth-splash">
        <div className="auth-glow auth-glow-1" />
        <div className="auth-glow auth-glow-2" />
        <section className="auth-card">
          <h1>Audio Summarizer</h1>
          <p>Checking your Google session...</p>
        </section>
      </div>
    );
  }

  if (!authProfile) {
    return (
      <div className="auth-splash">
        <div className="auth-glow auth-glow-1" />
        <div className="auth-glow auth-glow-2" />
        <section className="auth-card">
          <h1>Audio Summarizer</h1>
          <p>Sign in with Google to access your private jobs and transcripts.</p>
          {authError && <div className="error">{authError}</div>}
          <div className="google-button-shell" ref={googleButtonRef} />
        </section>
      </div>
    );
  }

  return (
    <div className="page-shell">
      <div className="page">
        <header className="header">
          <div className="header-brand">
            <div className="brand-row">
              <div className="brand-badge" aria-hidden>
                AS
              </div>
              <div className="mini-nav" aria-hidden>
                <span>Overview</span>
                <span>Learn</span>
                <span>Support</span>
              </div>
            </div>
            <h1>General statistics</h1>
            <p className="muted">{authProfile.email}</p>
          </div>
          <div className="header-actions">
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
            <button
              type="button"
              className="signout-btn"
              onClick={() => {
                clearSession();
                if (window.google?.accounts?.id) {
                  window.google.accounts.id.disableAutoSelect();
                }
              }}
            >
              Sign out
            </button>
          </div>
        </header>

        {errorText && <div className="error">Error: {errorText}</div>}

        {activeTab === "jobs" && (
          <section className={isJobListVisible ? "content-grid" : "content-grid jobs-list-hidden"}>
            <div className="left-rail">
              <section className="card submit-card">
                <div className="row-between">
                  <h2>Submit Job</h2>
                  <button
                    type="button"
                    className="list-toggle-btn"
                    onClick={() => setIsJobListVisible((visible) => !visible)}
                  >
                    {isJobListVisible ? "Hide Jobs" : "Show Jobs"}
                  </button>
                </div>
                <p className="submit-meta">{totalJobsLabel} total jobs</p>
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
                    Prefer YouTube captions first
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={allowWhisperFallback}
                      disabled={!preferCaptions}
                      onChange={(event) => setAllowWhisperFallback(event.target.checked)}
                    />
                    Allow Whisper fallback when captions are unavailable
                  </label>
                  <button type="submit" disabled={busySubmit}>
                    {busySubmit ? "Submitting..." : "Submit"}
                  </button>
                </form>
                {busySubmit && (
                  <div className="loading-state compact">
                    Creating a private job and sending it to the worker queue...
                  </div>
                )}
                <div className="submit-footer">
                  <span>Completed jobs</span>
                  <strong>{completedJobsLabel}</strong>
                </div>
              </section>

              <section className="card digest-card">
                <div className="row-between">
                  <h2>Email Digests</h2>
                  <span className="muted small">
                    {digestSettings?.enabled ? digestSettings.cadence : "off"}
                  </span>
                </div>
                <p className="muted small">
                  Send a daily or weekly recap of your completed summaries to your signed-in email.
                </p>
                {digestError && <div className="error">Digest settings: {digestError}</div>}
                {digestSaveNotice && <div className="notice">{digestSaveNotice}</div>}
                <form className="digest-grid" onSubmit={saveDigestSettings}>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={digestEnabled}
                      onChange={(event) => setDigestEnabled(event.target.checked)}
                    />
                    Receive email digests
                  </label>

                  <div>
                    <label>Cadence</label>
                    <div className="segmented-choice">
                      <label className="segmented-option">
                        <input
                          type="radio"
                          name="digest-cadence"
                          checked={digestCadence === "daily"}
                          onChange={() => setDigestCadence("daily")}
                        />
                        <span>Daily</span>
                      </label>
                      <label className="segmented-option">
                        <input
                          type="radio"
                          name="digest-cadence"
                          checked={digestCadence === "weekly"}
                          onChange={() => setDigestCadence("weekly")}
                        />
                        <span>Weekly</span>
                      </label>
                    </div>
                  </div>

                  <div className="digest-readonly">
                    <div>
                      <span className="muted small">Recipient</span>
                      <strong>{digestSettings?.recipient_email || authProfile?.email || "-"}</strong>
                    </div>
                    <div>
                      <span className="muted small">Timezone</span>
                      <strong>{digestTimezone || detectedTimeZone}</strong>
                    </div>
                    <div>
                      <span className="muted small">Next send</span>
                      <strong>{fmtDate(digestSettings?.next_send_at)}</strong>
                    </div>
                    <div>
                      <span className="muted small">Last sent</span>
                      <strong>{fmtDate(digestSettings?.last_sent_at)}</strong>
                    </div>
                    <div>
                      <span className="muted small">Last status</span>
                      <strong>{digestSettings?.last_run_status || "-"}</strong>
                    </div>
                    <div>
                      <span className="muted small">Schedule</span>
                      <strong>
                        {digestCadence} at {digestSettings?.send_hour_local ?? 8}:00 local
                      </strong>
                    </div>
                  </div>

                  {!digestSettings?.delivery_available && (
                    <div className="digest-warning">
                      {digestSettings?.delivery_reason || "Digest delivery is unavailable right now."}
                    </div>
                  )}

                  {digestSettings?.historical_backfill_pending && (
                    <div className="digest-info-note">
                      Your next digest will include older completed jobs from before you enabled digests.
                    </div>
                  )}

                  <div className="digest-profile-preview">
                    <div className="meta-section-kicker">Your profile lately</div>
                    <p className="muted small">
                      {digestSettings?.profile_summary ||
                        "We'll build your taste profile from your completed jobs, including older ones, when your first digest is prepared."}
                    </p>
                  </div>

                  <div className="digest-history">
                    <div className="meta-section-kicker">Recent digests</div>
                    {digestHistory.length > 0 ? (
                      digestHistory.slice(0, 5).map((item) => (
                        <div className="digest-history-row" key={item.id}>
                          <div>
                            <div className="digest-history-subject">
                              {item.subject || `${item.cadence} digest`}
                            </div>
                            <div className="muted small">
                              {fmtDate(item.sent_at || item.created_at)} · {item.job_count} jobs
                            </div>
                          </div>
                          <span className={`digest-run-status status-${item.status}`}>{item.status}</span>
                        </div>
                      ))
                    ) : (
                      <div className="muted small">No digest history yet.</div>
                    )}
                  </div>

                  <button type="submit" disabled={digestBusy}>
                    {digestBusy ? "Saving..." : "Save digest settings"}
                  </button>
                </form>
              </section>
            </div>

            {isJobListVisible && (
              <article className="card jobs-list-card">
                <div className="row-between">
                  <h2>Jobs</h2>
                  <span className="muted">{jobsLoading ? "Refreshing..." : `${jobs.length} total`}</span>
                </div>
                <div className="job-table-wrap">
                  <table className="job-table">
                    <thead>
                      <tr>
                        <th>Created</th>
                        <th>Job</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jobs.map((job) => (
                        <tr
                          key={job.id}
                          className={selectedJobId === job.id ? "selected" : ""}
                          onClick={() => {
                            setSelectedJobId(job.id);
                          }}
                        >
                          <td className="job-created">{fmtListDate(job.created_at)}</td>
                          <td className="job-primary">
                            <div className="job-title">{displayJobTitle(job)}</div>
                            <div className="job-meta-row">
                              <span className="job-status-chip">{job.status || "unknown"}</span>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>
            )}

            <article className={`card job-detail-card${isJobListVisible ? "" : " summary-spotlight"}`}>
              <div className="row-between">
                <h2>Job Detail</h2>
              </div>
              {!selectedJobId ? (
                <p className="muted">Select a job from the table.</p>
              ) : (
                <div className="job-detail-body">
                  <div className="detail-title">{selectedDisplayTitle}</div>
                  <section className={`job-progress-card stage-${selectedStage}`} aria-label="Job progress">
                    <div className="progress-card-header">
                      <div>
                        <div className="meta-section-kicker">Processing timeline</div>
                        <strong>{JOB_PROGRESS_STAGES[selectedStageIndex]?.label || "Queued"}</strong>
                      </div>
                      <div className="progress-status-copy">
                        <span>{jobDataLoading ? "Refreshing job detail..." : `Updated ${fmtDate(selectedUpdatedAt)}`}</span>
                        <span>{transcriptRouteLabel(selectedJob)}</span>
                      </div>
                    </div>
                    <ol className="progress-timeline">
                      {JOB_PROGRESS_STAGES.map((stage, index) => {
                        const isFailedStage = selectedStage === "failed";
                        const isCurrent = stage.key === selectedStage;
                        const isPast = !isFailedStage && index < selectedStageIndex;
                        const isSkippedByFailure = isFailedStage && stage.key !== "failed";
                        return (
                          <li
                            key={stage.key}
                            className={[
                              "progress-step",
                              isCurrent ? "current" : "",
                              isPast ? "past" : "",
                              isSkippedByFailure ? "muted-step" : ""
                            ]
                              .filter(Boolean)
                              .join(" ")}
                          >
                            <span className="progress-dot" aria-hidden />
                            <span className="progress-label">{stage.label}</span>
                            <span className="progress-helper">{stage.helper}</span>
                          </li>
                        );
                      })}
                    </ol>
                  </section>

                  {selectedStage === "failed" && (
                    <section className="failure-card" aria-label="Job failure">
                      <div>
                        <strong>We could not finish this job.</strong>
                        <p>{friendlyFailureMessage(selectedJob?.error)}</p>
                        <p className="muted small">Technical detail is kept in Debug info so the main view stays readable.</p>
                      </div>
                      {canRetrySelectedJob && (
                        <button type="button" className="retry-btn" disabled={retryBusy} onClick={retrySelectedJob}>
                          {retryBusy ? "Retrying..." : "Try again"}
                        </button>
                      )}
                    </section>
                  )}

                  <div className="job-detail-tabbar" role="tablist" aria-label="Job detail sections">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={jobDetailTab === "summary"}
                      className={jobDetailTab === "summary" ? "tab active" : "tab"}
                      onClick={() => setJobDetailTab("summary")}
                    >
                      Summary
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={jobDetailTab === "transcript"}
                      className={jobDetailTab === "transcript" ? "tab active" : "tab"}
                      onClick={() => setJobDetailTab("transcript")}
                    >
                      Transcript
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={jobDetailTab === "chat"}
                      className={jobDetailTab === "chat" ? "tab active" : "tab"}
                      onClick={() => setJobDetailTab("chat")}
                    >
                      Job Chat
                    </button>
                  </div>

                  {jobDetailTab === "summary" && (
                    <section className="job-detail-tab-panel" aria-label="Summary">
                      {artifactsLoading && <div className="loading-state">Loading summary and transcript artifacts...</div>}
                      {summaryArtifact?.file_link && (
                        <p>
                          <a
                            href={summaryArtifact.file_link}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => openArtifactLink(event, summaryArtifact.file_link)}
                          >
                            Open summary file
                          </a>
                        </p>
                      )}
                      {summaryArtifact?.text ? (
                        <MarkdownBlock
                          text={summaryArtifact.text}
                          className="markdown-panel summary-panel primary-content-panel"
                        />
                      ) : (
                        <div className="summary-panel primary-content-panel empty-artifact">
                          <strong>{summaryEmptyCopy(selectedStatus).title}</strong>
                          <p>{summaryEmptyCopy(selectedStatus).body}</p>
                        </div>
                      )}
                    </section>
                  )}

                  {jobDetailTab === "transcript" && (
                    <section className="job-detail-tab-panel" aria-label="Transcript">
                      {artifactsLoading && <div className="loading-state">Loading transcript artifact...</div>}
                      {transcriptArtifact?.file_link && (
                        <p>
                          <a
                            href={transcriptArtifact.file_link}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => openArtifactLink(event, transcriptArtifact.file_link)}
                          >
                            Open transcript file
                          </a>
                        </p>
                      )}
                      <pre className="transcript-panel">
                        {transcriptArtifact?.text ||
                          (selectedStage === "failed"
                            ? "(no transcript was generated before this job failed)"
                            : "(transcript is not ready yet)")}
                      </pre>
                    </section>
                  )}

                  {jobDetailTab === "chat" && (
                    <section className="job-detail-tab-panel" aria-label="Job Chat">
                      <div className="chat-box primary-content-panel">
                        {jobDataLoading && <div className="loading-state">Refreshing chat history...</div>}
                        {chat.map((msg) => (
                          <div key={msg.id} className={`chat-msg ${msg.role}`}>
                            <div className="chat-role">
                              <strong>{msg.role}:</strong>
                            </div>
                            {msg.role === "assistant" ? (
                              <MarkdownBlock text={msg.content} className="chat-markdown" />
                            ) : (
                              <div className="chat-plain">{msg.content}</div>
                            )}
                          </div>
                        ))}
                        {chat.length === 0 && (
                          <div className="muted">
                            {selectedStage === "complete"
                              ? "(no chat messages yet)"
                              : "(chat works best after the transcript is ready)"}
                          </div>
                        )}
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
                    </section>
                  )}

                  <section className="job-meta-section" aria-label="Job metadata">
                    <div className="meta-section-kicker">Job details</div>
                    <div className="detail-grid">
                      <div>
                        <div className="muted">Created</div>
                        <div>{fmtDate(selectedJob?.created_at || selectedJobRow?.created_at)}</div>
                      </div>
                      <div>
                        <div className="muted">Status</div>
                        <div>{selectedJob?.status || selectedJobRow?.status || "-"}</div>
                      </div>
                      <div className="detail-span-2">
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

                    <details className="debug-disclosure">
                      <summary>Debug info</summary>
                      <div className="debug-grid">
                        <div className="debug-span-2">
                          <div className="muted">Job ID</div>
                          <div className="mono">{selectedJobId}</div>
                        </div>
                        <div>
                          <div className="muted">Transcript Source</div>
                          <div>{transcriptRouteLabel(selectedJob)}</div>
                        </div>
                        <div>
                          <div className="muted">Whisper Fallback</div>
                          <div>
                            {selectedJob ? (selectedJob.allow_whisper_fallback ? "allowed" : "disabled") : "-"}
                          </div>
                        </div>
                        {selectedJob?.error && (
                          <div className="debug-span-2">
                            <div className="muted">Failure detail</div>
                            <div className="debug-error-text">{selectedJob.error}</div>
                          </div>
                        )}
                      </div>
                    </details>
                  </section>
                </div>
              )}
            </article>
          </section>
        )}

        {activeTab === "search" && (
          <section className="card search-card">
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
              <button type="submit" disabled={searchBusy || !searchQ.trim()}>
                {searchBusy ? "Searching..." : "Run Search"}
              </button>
            </form>

            {searchBusy && <div className="loading-state">Searching across your transcripts...</div>}

            <h3>Answer</h3>
            {searchResult?.answer ? (
              <MarkdownBlock text={searchResult.answer} className="markdown-panel" />
            ) : (
              <pre>(no answer yet)</pre>
            )}

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
                      <a
                        href={hit.file_link}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(event) => openArtifactLink(event, hit.file_link)}
                      >
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
    </div>
  );
}
