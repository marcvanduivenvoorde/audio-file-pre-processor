from __future__ import annotations

import importlib.util
import queue
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tkinter import (
    Tk,
    BooleanVar,
    END,
    IntVar,
    StringVar,
    Text,
    filedialog,
    messagebox,
    ttk,
)


def _load_processor_module():
    script_path = Path(__file__).with_name("audio-pre-processor.py")
    module_name = "audio_pre_processor_runtime"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load processor module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    # Register before execution so decorators (e.g. dataclass) can resolve module globals.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


processor = _load_processor_module()


STRATEGY_CHOICES = [
    ("rms", "RMS -21 dBFS"),
    ("rms_peak_cap", "RMS -21 dBFS + peak cap -9 dBFS"),
    ("peak", "Peak -9 dBFS"),
]
STRATEGY_ID_TO_LABEL = {k: v for k, v in STRATEGY_CHOICES}
RADIO_ON = "◉"
RADIO_OFF = "◯"


class _QueueWriter:
    def __init__(self, out_queue: queue.Queue[str]):
        self._queue = out_queue

    def write(self, text: str) -> int:
        if text:
            self._queue.put(text)
        return len(text)

    def flush(self) -> None:
        return None


class App:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Audio Pre-Processor")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1800, max(700, screen_w - 40))
        height = min(900, max(620, screen_h - 120))
        self.root.geometry(f"{width}x{height}")

        self.source_var = StringVar()
        self.normalize_var = BooleanVar(value=False)
        self.overwrite_var = BooleanVar(value=False)
        self.dry_run_var = BooleanVar(value=False)
        self.rms_target_var = IntVar(value=int(round(processor.RMS_TARGET_DBFS)))
        self.peak_cap_var = IntVar(value=int(round(processor.PEAK_CAP_DBFS)))

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._preview_queue: queue.Queue[tuple] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._preview_worker: threading.Thread | None = None
        self._last_plan = None
        self._last_source_dir: Path | None = None
        self._preview_source_dir: Path | None = None
        self._strategy_vars: dict[str, str] = {}
        self._item_to_output_path: dict[str, str] = {}
        self._preview_in_progress = False

        self._build_ui()
        self._poll_logs()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)

        source_row = ttk.Frame(container)
        source_row.pack(fill="x", pady=(0, 10))
        ttk.Label(source_row, text="Source folder:").pack(side="left")
        ttk.Entry(source_row, textvariable=self.source_var).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(source_row, text="Browse...", command=self._choose_source).pack(side="left")

        options = ttk.LabelFrame(container, text="Options", padding=10)
        options.pack(fill="x", pady=(0, 10))
        options_left = ttk.Frame(options)
        options_left.pack(side="left", anchor="w")
        ttk.Checkbutton(
            options_left, text="Normalize (crest-based loudness)", variable=self.normalize_var
        ).pack(anchor="w")
        ttk.Checkbutton(
            options_left, text="Overwrite existing outputs", variable=self.overwrite_var
        ).pack(anchor="w")
        ttk.Checkbutton(options_left, text="Dry run (plan only)", variable=self.dry_run_var).pack(anchor="w")
        self.normalize_targets_frame = ttk.Frame(options_left)
        self.normalize_targets_frame.pack(anchor="w", pady=(6, 0))
        ttk.Label(self.normalize_targets_frame, text="RMS target (dBFS):").grid(
            row=0, column=0, sticky="w"
        )
        self.rms_entry = ttk.Entry(self.normalize_targets_frame, textvariable=self.rms_target_var, width=6)
        self.rms_entry.grid(row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(self.normalize_targets_frame, text="Peak cap (dBFS):").grid(
            row=0, column=2, sticky="w"
        )
        self.peak_entry = ttk.Entry(self.normalize_targets_frame, textvariable=self.peak_cap_var, width=6)
        self.peak_entry.grid(row=0, column=3, sticky="w", padx=(6, 0))
        options_right = ttk.Frame(options)
        options_right.pack(side="right", anchor="e")
        self.options_spinner = ttk.Progressbar(options_right, mode="indeterminate", length=90)
        self.options_spinner.pack(side="right")
        self.options_loading_label = ttk.Label(options_right, text="")
        self.options_loading_label.pack(side="right", padx=(8, 0))

        preview_frame = ttk.LabelFrame(container, text="Pre-process overview", padding=8)
        preview_frame.pack(fill="both", expand=True, pady=(0, 10))

        columns = ("source", "output", "plan", "peak", "rms_peak", "rms", "reason")
        self.preview_table = ttk.Treeview(preview_frame, columns=columns, show="headings", height=12)
        self.preview_table.heading("source", text="Source file")
        self.preview_table.heading("output", text="Output")
        self.preview_table.heading("plan", text="Normalize plan")
        self.preview_table.heading("peak", text="peak")
        self.preview_table.heading("rms_peak", text="rms + peak")
        self.preview_table.heading("rms", text="rms")
        self.preview_table.heading("reason", text="Reason")
        self.preview_table.column("source", width=180, anchor="w")
        self.preview_table.column("output", width=230, anchor="w")
        self.preview_table.column("plan", width=210, anchor="w")
        self.preview_table.column("peak", width=20, anchor="center")
        self.preview_table.column("rms_peak", width=20, anchor="center")
        self.preview_table.column("rms", width=20, anchor="center")
        self.preview_table.column("reason", width=250, anchor="w")
        self.preview_table.tag_configure("editable_strategy", background="#f0f0f0")
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_table.yview)
        self.preview_table.configure(yscrollcommand=preview_scroll.set)
        self.preview_table.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        self.preview_table.bind("<Button-1>", self._on_table_click)

        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=(0, 10))
        self.preview_btn = ttk.Button(buttons, text="Preview Overview", command=self._preview)
        self.preview_btn.pack(side="left")
        self.run_btn = ttk.Button(buttons, text="Process", command=self._run, state="disabled")
        self.run_btn.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Clear Log", command=self._clear_log).pack(side="left", padx=(8, 0))
        self.preview_status = ttk.Label(buttons, text="")
        self.preview_status.pack(side="left", padx=(10, 0))
        ttk.Label(
            buttons,
            text=(
                "peak : Peak -9 dBFS\n"
                "rms + peak : RMS -21 dBFS + peak cap -9 dBFS\n"
                "rms : RMS -21 dBFS"
            ),
        ).pack(side="right")

        self.log = Text(container, wrap="word", height=24)
        self.log.pack(fill="both", expand=True)

        self.source_var.trace_add("write", lambda *_: self._invalidate_preview())
        self.normalize_var.trace_add("write", lambda *_: self._invalidate_preview())
        self.overwrite_var.trace_add("write", lambda *_: self._invalidate_preview())
        self.dry_run_var.trace_add("write", lambda *_: self._invalidate_preview())
        self.rms_target_var.trace_add("write", lambda *_: self._invalidate_preview())
        self.peak_cap_var.trace_add("write", lambda *_: self._invalidate_preview())
        self.normalize_var.trace_add("write", lambda *_: self._update_normalize_inputs_state())
        self._update_normalize_inputs_state()

    def _choose_source(self) -> None:
        selected = filedialog.askdirectory()
        if selected:
            self.source_var.set(selected)

    def _clear_log(self) -> None:
        self.log.delete("1.0", END)

    def _append_log(self, text: str) -> None:
        self.log.insert(END, text)
        self.log.see(END)

    def _set_running(self, running: bool) -> None:
        self.preview_btn.configure(state="disabled" if running or self._preview_in_progress else "normal")
        if running:
            self.run_btn.configure(state="disabled")
        else:
            self.run_btn.configure(state="normal" if self._last_plan is not None else "disabled")

    def _set_preview_loading(self, active: bool) -> None:
        if active:
            self.options_loading_label.configure(text="analysing audio")
            self.options_spinner.start(12)
        else:
            self.options_spinner.stop()
            self.options_loading_label.configure(text="")

    def _update_normalize_inputs_state(self) -> None:
        state = "normal" if bool(self.normalize_var.get()) else "disabled"
        self.rms_entry.configure(state=state)
        self.peak_entry.configure(state=state)

    def _set_normalization_targets(self) -> bool:
        try:
            rms_target = int(self.rms_target_var.get())
            peak_cap = int(self.peak_cap_var.get())
        except Exception:
            messagebox.showerror("Invalid normalize targets", "RMS and Peak must be integer values.")
            return False
        processor.RMS_TARGET_DBFS = float(rms_target)
        processor.PEAK_CAP_DBFS = float(peak_cap)
        return True

    def _invalidate_preview(self) -> None:
        if self._preview_in_progress:
            return
        self._last_plan = None
        self._last_source_dir = None
        self._preview_source_dir = None
        self._item_to_output_path = {}
        self.run_btn.configure(state="disabled")
        self.preview_status.configure(text="")
        self._set_preview_loading(False)
        for item in self.preview_table.get_children():
            self.preview_table.delete(item)

    def _render_action_row(self, source_dir: Path, action) -> None:
        normalize = bool(self.normalize_var.get())
        if action.kind == "skip" or not action.outputs:
            self.preview_table.insert(
                "",
                "end",
                values=(action.source.name, "-", "-", "-", "-", "-", action.reason),
            )
            return
        for idx, out in enumerate(action.outputs):
            src = action.source.name if idx == 0 else ""
            try:
                out_rel = str(out.path.relative_to(source_dir))
            except ValueError:
                out_rel = out.path.name
            reason = action.reason if idx == 0 else ""
            peak_cell = "-"
            rms_peak_cell = "-"
            rms_cell = "-"
            item_id = self.preview_table.insert(
                "",
                "end",
                values=(src, out_rel, out.normalize_label, peak_cell, rms_peak_cell, rms_cell, reason),
            )
            if normalize:
                strategy = out.normalize_strategy or "rms"
                out_path = str(out.path)
                self._strategy_vars[out_path] = strategy
                self._item_to_output_path[item_id] = out_path
                self._set_strategy_cells(item_id, strategy)
                self.preview_table.item(item_id, tags=("editable_strategy",))

    def _set_strategy_cells(self, item_id: str, strategy: str) -> None:
        self.preview_table.set(item_id, "peak", RADIO_ON if strategy == "peak" else RADIO_OFF)
        self.preview_table.set(item_id, "rms_peak", RADIO_ON if strategy == "rms_peak_cap" else RADIO_OFF)
        self.preview_table.set(item_id, "rms", RADIO_ON if strategy == "rms" else RADIO_OFF)

    def _on_table_click(self, event) -> None:
        if not bool(self.normalize_var.get()) or self._preview_in_progress:
            return
        region = self.preview_table.identify("region", event.x, event.y)
        if region != "cell":
            return
        item_id = self.preview_table.identify_row(event.y)
        if not item_id:
            return
        out_path = self._item_to_output_path.get(item_id)
        if not out_path:
            return

        column = self.preview_table.identify_column(event.x)
        selected_strategy: str | None = None
        if column == "#4":
            selected_strategy = "peak"
        elif column == "#5":
            selected_strategy = "rms_peak_cap"
        elif column == "#6":
            selected_strategy = "rms"

        if selected_strategy is None:
            return
        self._strategy_vars[out_path] = selected_strategy
        self._set_strategy_cells(item_id, selected_strategy)

    def _preview(self) -> None:
        if self._preview_worker and self._preview_worker.is_alive():
            return
        if bool(self.normalize_var.get()) and not self._set_normalization_targets():
            return
        source_dir = Path(self.source_var.get().strip())
        if not str(source_dir):
            messagebox.showerror("Missing input", "Please select a source folder.")
            return
        if not source_dir.exists() or not source_dir.is_dir():
            messagebox.showerror("Invalid folder", f"Not a directory: {source_dir}")
            return

        self._preview_in_progress = True
        self._preview_source_dir = source_dir
        self.preview_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.preview_status.configure(text="Analyzing...")
        self._set_preview_loading(True)
        for item in self.preview_table.get_children():
            self.preview_table.delete(item)
        self._strategy_vars = {}
        self._item_to_output_path = {}

        normalize = bool(self.normalize_var.get())
        self._preview_worker = threading.Thread(
            target=self._preview_worker_run,
            args=(source_dir, normalize),
            daemon=True,
        )
        self._preview_worker.start()

    def _preview_worker_run(self, source_dir: Path, normalize: bool) -> None:
        try:
            def _on_action(action, idx: int, total: int) -> None:
                self._preview_queue.put(("action", action, idx, total))

            plan = processor.build_plan(
                source_dir,
                show_progress=False,
                normalize=normalize,
                on_action=_on_action,
            )
            self._preview_queue.put(("done", source_dir, plan))
        except Exception as e:
            self._preview_queue.put(("error", str(e)))

    def _run(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        if self._last_plan is None or self._last_source_dir is None:
            messagebox.showinfo("Preview required", "Click 'Preview Overview' before processing.")
            return
        if bool(self.normalize_var.get()) and not self._set_normalization_targets():
            return

        opts = processor.ProcessorOptions(
            dry_run=bool(self.dry_run_var.get()),
            normalize=bool(self.normalize_var.get()),
            overwrite=bool(self.overwrite_var.get()),
            assume_yes=False,
            show_plan_progress=False,
        )
        strategy_overrides = (
            dict(self._strategy_vars)
            if opts.normalize
            else {}
        )

        source_dir = self._last_source_dir
        plan = self._last_plan
        self._append_log(f"\n=== Run: {source_dir} ===\n")
        self._set_running(True)
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(source_dir, opts, plan, strategy_overrides),
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self, source_dir: Path, opts, plan, strategy_overrides: dict[str, str]) -> None:
        writer = _QueueWriter(self._log_queue)

        with redirect_stdout(writer), redirect_stderr(writer):
            processor.print_plan(plan, source_dir, normalize=bool(opts.normalize))
            actionable = [a for a in plan if a.kind != "skip"]
            if not plan:
                exit_code = 0
            elif not actionable:
                print("Nothing to process (all files skipped).")
                exit_code = 0
            elif opts.dry_run:
                exit_code = 0
            else:
                exit_code, errors = processor.execute_plan(
                    plan,
                    source_dir,
                    overwrite=bool(opts.overwrite),
                    normalize=bool(opts.normalize),
                    normalize_strategy_overrides=strategy_overrides if opts.normalize else None,
                )
                if errors:
                    print()
                    print("Errors:")
                    for e in errors:
                        print(f"- {e}")
                else:
                    print("Done.")
            print(f"\nExit code: {exit_code}\n")

        self.root.after(0, lambda: self._set_running(False))

    def _poll_logs(self) -> None:
        try:
            while True:
                event = self._preview_queue.get_nowait()
                kind = event[0]
                if kind == "action":
                    _, action, idx, total = event
                    if self._preview_source_dir is not None:
                        self._render_action_row(self._preview_source_dir, action)
                    self.preview_status.configure(text=f"Analyzing... {idx}/{total}")
                elif kind == "done":
                    _, source_dir, plan = event
                    self._last_plan = plan
                    self._last_source_dir = source_dir
                    self._preview_source_dir = source_dir
                    self._preview_in_progress = False
                    self._set_preview_loading(False)
                    self.preview_status.configure(text=f"Overview ready ({len(plan)} files)")
                    self.preview_btn.configure(state="normal")
                    self.run_btn.configure(state="normal")
                elif kind == "error":
                    _, msg = event
                    self._preview_in_progress = False
                    self._set_preview_loading(False)
                    self.preview_btn.configure(state="normal")
                    self.run_btn.configure(state="disabled")
                    self.preview_status.configure(text="Preview failed")
                    messagebox.showerror("Preview failed", msg)
        except queue.Empty:
            pass
        try:
            while True:
                chunk = self._log_queue.get_nowait()
                self._append_log(chunk)
        except queue.Empty:
            pass
        finally:
            self.root.after(60, self._poll_logs)


def main() -> int:
    root = Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
