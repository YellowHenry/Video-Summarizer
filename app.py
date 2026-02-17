import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import logging
import os
import webbrowser

from backend.compression import CompressionConfig
from backend.downloader import AudioDownloader
from backend.jobs import Job, JobQueue
from backend.notifier import Notifier
from backend.storage import Storage
from backend.summarizer import CloudSummarizerClient
from backend.vector_store import VectorStore
from backend.qa import QAService
from backend.jobs import Job


class AudioSummarizerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Audio Summarizer")
        self.root.geometry("720x480")
        logging.basicConfig(level=logging.INFO)

        # Notebook with tabs to reduce clutter
        self.notebook = ttk.Notebook(self.root)
        self.jobs_tab = ttk.Frame(self.notebook)
        self.rag_tab = ttk.Frame(self.notebook)
        self.chat_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.jobs_tab, text="Jobs")
        self.notebook.add(self.rag_tab, text="Search (RAG)")
        self.notebook.add(self.chat_tab, text="Job Chat")
        self.notebook.pack(fill="both", expand=True)

        self.storage = Storage()
        self.summarizer = CloudSummarizerClient()
        self.vector_store = VectorStore()
        self.vector_store.populate_from_storage(self.storage.config.base_dir)
        self.qa_service = QAService(self.vector_store, self.summarizer)
        self.downloader = AudioDownloader()
        self.notifier = Notifier()
        self.job_queue = JobQueue(self.storage, self.summarizer, self.downloader, self.notifier, vector_store=self.vector_store)
        self.event_queue: queue.Queue[Job] = queue.Queue()
        self.jobs: dict[str, Job] = {}
        self.job_queue.add_listener(self.event_queue.put)

        self._build_form(self.jobs_tab)
        self._build_status_table(self.jobs_tab)
        self._build_summary_panel(self.jobs_tab)
        self._build_qa_panel(self.rag_tab)
        self._build_chat_panel(self.chat_tab)
        self._load_existing_jobs()

        self.root.after(200, self._process_job_events)

    def _build_form(self, parent) -> None:
        form = ttk.LabelFrame(parent, text="Submit audio")
        form.pack(fill="x", padx=10, pady=10)

        path_row = ttk.Frame(form)
        path_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(path_row, text="Local file (audio or video):").pack(side="left")
        self.audio_path_var = tk.StringVar()
        ttk.Entry(path_row, textvariable=self.audio_path_var, width=50).pack(side="left", padx=5)
        ttk.Button(path_row, text="Browse", command=self._choose_file).pack(side="left")

        url_row = ttk.Frame(form)
        url_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(url_row, text="YouTube URL (audio):").pack(side="left")
        self.youtube_var = tk.StringVar()
        ttk.Entry(url_row, textvariable=self.youtube_var, width=50).pack(side="left", padx=5)

        mode_row = ttk.Frame(form)
        mode_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(mode_row, text="Transcript mode:").pack(side="left")
        self.transcript_mode = tk.StringVar(value="prefer_captions")
        ttk.Radiobutton(
            mode_row,
            text="Prefer YouTube captions (fallback Whisper)",
            variable=self.transcript_mode,
            value="prefer_captions",
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            mode_row,
            text="Always Whisper",
            variable=self.transcript_mode,
            value="whisper_only",
        ).pack(side="left", padx=4)

        email_row = ttk.Frame(form)
        email_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(email_row, text="Notify email (optional):").pack(side="left")
        self.email_var = tk.StringVar()
        ttk.Entry(email_row, textvariable=self.email_var, width=30).pack(side="left", padx=5)

        button_row = ttk.Frame(form)
        button_row.pack(fill="x", padx=8, pady=4)
        ttk.Button(button_row, text="Submit", command=self._submit_job).pack(side="right")

    def _build_status_table(self, parent) -> None:
        table_frame = ttk.LabelFrame(parent, text="Jobs")
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("id", "source", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        for col, label in zip(columns, ["Job ID", "Source", "Status"]):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=200 if col != "status" else 120)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._handle_select)
        self.tree.bind("<Double-1>", self._handle_double_click)

    def _build_summary_panel(self, parent) -> None:
        panel = ttk.LabelFrame(parent, text="Summary")
        panel.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.summary_text = tk.Text(panel, height=8, wrap="word")
        self.summary_text.pack(fill="both", expand=True)

    def _build_qa_panel(self, parent) -> None:
        panel = ttk.LabelFrame(parent, text="Ask a question (search past transcripts)")
        panel.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        row = ttk.Frame(panel)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Question:").pack(side="left")
        self.question_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.question_var, width=60).pack(side="left", padx=5, fill="x", expand=True)

        filter_row = ttk.Frame(panel)
        filter_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(filter_row, text="Filter by YouTube URL (optional):").pack(side="left")
        self.filter_url_var = tk.StringVar()
        ttk.Entry(filter_row, textvariable=self.filter_url_var, width=50).pack(side="left", padx=5, fill="x", expand=True)

        btn_row = ttk.Frame(panel)
        btn_row.pack(fill="x", padx=8, pady=4)
        ttk.Button(btn_row, text="Ask", command=self._ask_question).pack(side="left")

        self.answer_text = tk.Text(panel, height=6, wrap="word")
        self.answer_text.pack(fill="both", expand=True, padx=8, pady=4)

        links_row = ttk.Frame(panel)
        links_row.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        ttk.Label(links_row, text="Matching chunks (double-click to open):").pack(anchor="w")
        self.links_list = tk.Listbox(links_row, height=5)
        self.links_list.pack(fill="both", expand=True)
        self.links_list.bind("<Double-1>", self._open_link)
        self.link_paths: list[str] = []

    def _build_chat_panel(self, parent) -> None:
        self.chat_panel = ttk.LabelFrame(parent, text="Chat about a job")
        self.chat_panel.pack(fill="both", expand=True, padx=10, pady=10)

        self.chat_job_label = ttk.Label(self.chat_panel, text="No job selected")
        self.chat_job_label.pack(anchor="w", padx=6, pady=(2, 4))

        chat_row = ttk.Frame(self.chat_panel)
        chat_row.pack(fill="x", padx=6, pady=4)
        ttk.Label(chat_row, text="Message:").pack(side="left")
        self.chat_var = tk.StringVar()
        ttk.Entry(chat_row, textvariable=self.chat_var, width=50).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(chat_row, text="Send", command=self._send_chat).pack(side="left")

        self.chat_history = tk.Text(self.chat_panel, height=12, wrap="word")
        self.chat_history.pack(fill="both", expand=True, padx=6, pady=4)

    def _choose_file(self) -> None:
        file_path = filedialog.askopenfilename(
            filetypes=[
                ["Audio files", "*.mp3;*.m4a;*.wav;*.flac;*.aac;*.ogg;*.opus"],
                ["Video files", "*.mp4;*.mov;*.mkv;*.avi;*.webm"],
                ["All", "*"],
            ]
        )
        if file_path:
            self.audio_path_var.set(file_path)

    def _submit_job(self) -> None:
        path = Path(self.audio_path_var.get()) if self.audio_path_var.get() else None
        url = self.youtube_var.get().strip() or None
        if path and not path.exists():
            messagebox.showerror("File not found", f"The file {path} does not exist.")
            return
        if not path and not url:
            messagebox.showerror("Missing input", "Please choose a file or provide a YouTube URL.")
            return

        job = Job(
            audio_path=path,
            youtube_url=url,
            requester_email=self.email_var.get() or None,
            compression=CompressionConfig(),
            prefer_youtube_captions=self.transcript_mode.get() == "prefer_captions",
        )
        self.jobs[job.id] = job
        self.job_queue.submit(job)
        self._add_or_update_row(job)
        self._clear_form()

    def _add_or_update_row(self, job: Job) -> None:
        # maintain sort by created_at descending
        existing_ids = list(self.tree.get_children(""))
        if job.id not in existing_ids:
            self.tree.insert("", "end", iid=job.id, values=(job.display_id(), job.describe(), job.status))
        else:
            self.tree.item(job.id, values=(job.display_id(), job.describe(), job.status))
        # reorder
        jobs_sorted = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)
        for idx, j in enumerate(jobs_sorted):
            if j.id in self.tree.get_children(""):
                self.tree.move(j.id, "", idx)

    def _process_job_events(self) -> None:
        while not self.event_queue.empty():
            job: Job = self.event_queue.get()
            self.jobs[job.id] = job
            self._add_or_update_row(job)
            if job.status == "complete" and job.summary_path:
                self._display_summary(job)
            if job.status == "failed" and job.error:
                messagebox.showerror("Job failed", f"Job {job.id} failed: {job.error}")
        self.root.after(200, self._process_job_events)

    def _handle_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        job_id = selection[0]
        job = self.jobs.get(job_id)
        if job:
            if job.summary_path:
                self._display_summary(job)
            self._display_chat(job)

    def _handle_double_click(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        job_id = selection[0]
        job = self.jobs.get(job_id)
        if not job:
            return
        info_lines = [f"Job ID: {job.id}"]
        if job.transcript_source:
            info_lines.append(f"Transcript source: {job.transcript_source}")
        if getattr(job, "captions_status", None):
            info_lines.append(f"Captions status: {job.captions_status}")
        if getattr(job, "captions_detail", None):
            info_lines.append(f"Captions detail: {job.captions_detail}")
        if job.summary_path:
            info_lines.append(f"Summary path: {job.summary_path}")
        if job.transcript_path:
            info_lines.append(f"Transcript path: {job.transcript_path}")
        messagebox.showinfo("Job details", "\n".join(info_lines))

    def _display_summary(self, job: Job) -> None:
        if not job.summary_path or not job.summary_path.exists():
            return
        content = job.summary_path.read_text(encoding="utf-8")
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, content)

    def _ask_question(self) -> None:
        question = self.question_var.get().strip()
        if not question:
            messagebox.showerror("Missing question", "Enter a question to search the transcript index.")
            return
        filter_url = self.filter_url_var.get().strip() or None
        try:
            result = self.qa_service.answer(question, youtube_url=filter_url)
            self.answer_text.delete("1.0", tk.END)
            self.answer_text.insert(tk.END, result.answer)
            self.links_list.delete(0, tk.END)
            self.link_paths = []
            for hit in result.hits:
                if hit.file_path:
                    display = f"{hit.kind} job={hit.job_id} chunk={hit.chunk_index} {hit.file_path}"
                    self.links_list.insert(tk.END, display)
                    self.link_paths.append(hit.file_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Q&A failed", str(exc))

    def _send_chat(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showerror("No job selected", "Select a job row first.")
            return
        job_id = selection[0]
        job = self.jobs.get(job_id)
        if not job:
            return
        message = self.chat_var.get().strip()
        if not message:
            return

        transcript_path = job.transcript_path
        if not transcript_path or not transcript_path.exists():
            fallback = self.storage.config.base_dir / job.id / "transcript.txt"
            transcript_path = fallback if fallback.exists() else None
        if not transcript_path:
            messagebox.showerror("Chat unavailable", "This job has no transcript.txt yet, so full-context chat is unavailable.")
            return

        history_before = self.storage.load_chat(job.id)
        self.storage.append_chat(job.id, "user", message)
        job.chat = self.storage.load_chat(job.id)

        try:
            transcript_text = transcript_path.read_text(encoding="utf-8")
            summary_text = None
            if job.summary_path and job.summary_path.exists():
                summary_text = job.summary_path.read_text(encoding="utf-8")
            result = self.qa_service.answer_job_chat(
                message,
                transcript_text=transcript_text,
                conversation_history=history_before,
                summary_text=summary_text,
            )
            self.storage.append_chat(job.id, "assistant", result.answer)
            job.chat = self.storage.load_chat(job.id)
            self._display_chat(job)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Chat failed", str(exc))
        self.chat_var.set("")

    def _display_chat(self, job: Job) -> None:
        chat_history = self.storage.load_chat(job.id)
        label = job.display_name or job.title or job.id
        self.chat_job_label.config(text=f"Chat for: {label}")
        self.chat_history.delete("1.0", tk.END)
        for msg in chat_history:
            self.chat_history.insert(tk.END, f"{msg.get('role','?')}: {msg.get('content','')}\n")

    def _open_link(self, _event: tk.Event) -> None:
        selection = self.links_list.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx < len(self.link_paths):
            path = self.link_paths[idx]
            if os.path.exists(path):
                try:
                    os.startfile(path)  # type: ignore[attr-defined]
                except Exception:
                    webbrowser.open(Path(path).as_uri())
            else:
                messagebox.showerror("File not found", f"The file {path} does not exist.")

    def _clear_form(self) -> None:
        self.audio_path_var.set("")
        self.youtube_var.set("")

    def _load_existing_jobs(self) -> None:
        for job_id, summary_path, transcript_path, title, youtube_url, created_at, status, transcript_source, prefer_youtube_captions, captions_attempted, captions_status, captions_detail in self.storage.load_existing_jobs():
            job = Job(
                id=job_id,
                audio_path=None,
                youtube_url=youtube_url,
                requester_email=None,
                compression=CompressionConfig(),
                prefer_youtube_captions=prefer_youtube_captions if prefer_youtube_captions is not None else True,
                status=status or "complete",
                created_at=created_at,
                summary_path=summary_path,
                transcript_path=transcript_path if transcript_path and transcript_path.exists() else None,
                transcript_source=transcript_source,
                captions_attempted=captions_attempted,
                captions_status=captions_status,
                captions_detail=captions_detail,
                title=title,
                display_name=title,
            )
            job.chat = self.storage.load_chat(job_id)
            self.jobs[job.id] = job
            self._add_or_update_row(job)


def main() -> None:
    root = tk.Tk()
    AudioSummarizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
