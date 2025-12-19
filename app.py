import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import logging

from backend.compression import CompressionConfig
from backend.downloader import AudioDownloader
from backend.jobs import Job, JobQueue
from backend.notifier import Notifier
from backend.storage import Storage
from backend.summarizer import CloudSummarizerClient


class AudioSummarizerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Audio Summarizer")
        self.root.geometry("720x480")
        logging.basicConfig(level=logging.INFO)

        self.storage = Storage()
        self.summarizer = CloudSummarizerClient()
        self.downloader = AudioDownloader()
        self.notifier = Notifier()
        self.job_queue = JobQueue(self.storage, self.summarizer, self.downloader, self.notifier)
        self.event_queue: queue.Queue[Job] = queue.Queue()
        self.jobs: dict[str, Job] = {}
        self.job_queue.add_listener(self.event_queue.put)

        self._build_form()
        self._build_status_table()
        self._build_summary_panel()

        self.root.after(200, self._process_job_events)

    def _build_form(self) -> None:
        form = ttk.LabelFrame(self.root, text="Submit audio")
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

    def _build_status_table(self) -> None:
        table_frame = ttk.LabelFrame(self.root, text="Jobs")
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("id", "source", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        for col, label in zip(columns, ["Job ID", "Source", "Status"]):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=200 if col != "status" else 120)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._handle_select)
        self.tree.bind("<Double-1>", self._handle_double_click)

    def _build_summary_panel(self) -> None:
        panel = ttk.LabelFrame(self.root, text="Summary")
        panel.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.summary_text = tk.Text(panel, height=8, wrap="word")
        self.summary_text.pack(fill="both", expand=True)

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
        if job.id not in self.tree.get_children(""):
            self.tree.insert("", "end", iid=job.id, values=(job.display_id(), job.describe(), job.status))
        else:
            self.tree.item(job.id, values=(job.display_id(), job.describe(), job.status))

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
        if job and job.summary_path:
            self._display_summary(job)

    def _handle_double_click(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        job_id = selection[0]
        job = self.jobs.get(job_id)
        if not job:
            return
        info_lines = [f"Job ID: {job.id}"]
        if job.summary_path:
            info_lines.append(f"Summary path: {job.summary_path}")
        messagebox.showinfo("Job details", "\n".join(info_lines))

    def _display_summary(self, job: Job) -> None:
        if not job.summary_path or not job.summary_path.exists():
            return
        content = job.summary_path.read_text(encoding="utf-8")
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, content)

    def _clear_form(self) -> None:
        self.audio_path_var.set("")
        self.youtube_var.set("")


def main() -> None:
    root = tk.Tk()
    AudioSummarizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
