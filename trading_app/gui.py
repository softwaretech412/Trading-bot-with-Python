from __future__ import annotations

from pathlib import Path
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Tuple

from trading_app.config_schema import SETTINGS_GROUPS, SettingField
from trading_app.env_config import load_env_file, save_env_file
from trading_app.log_parser import CoinSnapshot, RuntimeLogParser
from trading_app.process_runner import BotProcessRunner


class TradingBotApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("QUANT Grid Trader Desktop")
        self.geometry("1600x940")
        self.minsize(1360, 820)

        self.project_root = Path(__file__).resolve().parent.parent
        self.env_path = self.project_root / ".env"
        self.settings_values = load_env_file(self.env_path)

        self.log_parser = RuntimeLogParser()
        self.process_runner = BotProcessRunner(self.project_root)
        self.ui_queue: queue.Queue[Tuple[str, object]] = queue.Queue()
        self.settings_locked = False
        self.log_records_count = 0
        self.log_item_to_raw: Dict[str, str] = {}
        self.ai_item_to_symbol: Dict[str, str] = {}

        self.settings_vars: Dict[str, tk.StringVar] = {}
        self.settings_entries: Dict[str, tk.Entry] = {}
        self.eye_buttons: List[tk.Button] = []
        self.settings_widgets: List[tk.Widget] = []

        self._build_layout()
        self._load_settings_into_form()
        self.after(120, self._drain_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        top_frame = ttk.Frame(self, padding=(12, 8))
        top_frame.pack(side=tk.TOP, fill=tk.X)

        self.status_var = tk.StringVar(value="Idle")
        self.status_label = ttk.Label(top_frame, textvariable=self.status_var, font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side=tk.LEFT)

        self.lock_var = tk.StringVar(value="")
        self.lock_label = ttk.Label(top_frame, textvariable=self.lock_var, foreground="#b22222")
        self.lock_label.pack(side=tk.LEFT, padx=(18, 0))

        action_frame = ttk.Frame(top_frame)
        action_frame.pack(side=tk.RIGHT)
        self.btn_save = ttk.Button(action_frame, text="Save Settings", command=self._save_settings)
        self.btn_save.pack(side=tk.LEFT, padx=5)
        self.btn_reload = ttk.Button(action_frame, text="Reload .env", command=self._reload_settings)
        self.btn_reload.pack(side=tk.LEFT, padx=5)
        self.btn_start = ttk.Button(action_frame, text="Start Bot", command=self._start_bot)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(action_frame, text="Stop Bot", command=self._stop_bot, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.settings_page = ttk.Frame(self.notebook)
        self.logs_page = ttk.Frame(self.notebook)
        self.ai_page = ttk.Frame(self.notebook)

        self.notebook.add(self.settings_page, text="Settings Control")
        self.notebook.add(self.logs_page, text="Runtime Logs")
        self.notebook.add(self.ai_page, text="AI Decisions by Coin")

        self._build_settings_page()
        self._build_logs_page()
        self._build_ai_page()

    def _build_settings_page(self) -> None:
        wrapper = ttk.Frame(self.settings_page)
        wrapper.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        canvas = tk.Canvas(wrapper, highlightthickness=0)
        scrollbar = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=canvas.yview)
        self.settings_form_frame = ttk.Frame(canvas)
        self.settings_form_frame.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas_frame = canvas.create_window((0, 0), window=self.settings_form_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfig(canvas_frame, width=e.width - 2)
        )
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        row = 0
        for group in SETTINGS_GROUPS:
            section = ttk.LabelFrame(self.settings_form_frame, text=group.name, padding=(10, 8))
            section.grid(row=row, column=0, sticky="ew", pady=8)
            self.settings_form_frame.columnconfigure(0, weight=1)

            section.columnconfigure(1, weight=1)
            sub_row = 0
            for field in group.fields:
                self._add_setting_row(section, sub_row, field)
                sub_row += 1
            row += 1

    def _add_setting_row(self, parent: ttk.LabelFrame, row: int, field: SettingField) -> None:
        label = ttk.Label(parent, text=field.label)
        label.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)

        var = tk.StringVar(value="")
        entry = ttk.Entry(parent, textvariable=var, width=90)
        if field.secret:
            secret_container = ttk.Frame(parent)
            secret_container.grid(row=row, column=1, sticky="ew", pady=4)
            secret_container.columnconfigure(0, weight=1)

            entry = ttk.Entry(secret_container, textvariable=var, show="*", width=90)
            entry.grid(row=0, column=0, sticky="ew")
            eye_button = tk.Button(
                secret_container,
                text="👁",
                width=3,
                relief=tk.GROOVE,
                command=lambda key=field.key: self._toggle_secret_visibility(key),
            )
            eye_button.grid(row=0, column=1, padx=(6, 0))
            self.eye_buttons.append(eye_button)
            self.settings_widgets.extend([entry, eye_button, label])
        else:
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self.settings_widgets.extend([entry, label])

        self.settings_vars[field.key] = var
        self.settings_entries[field.key] = entry

    def _build_logs_page(self) -> None:
        top = ttk.Frame(self.logs_page)
        top.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        table_frame = ttk.Frame(top)
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("idx", "timestamp", "level", "source", "message")
        self.logs_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=20)
        self.logs_tree.heading("idx", text="#")
        self.logs_tree.heading("timestamp", text="Timestamp")
        self.logs_tree.heading("level", text="Level")
        self.logs_tree.heading("source", text="Source")
        self.logs_tree.heading("message", text="Message (full text)")
        self.logs_tree.column("idx", width=60, anchor=tk.CENTER)
        self.logs_tree.column("timestamp", width=180)
        self.logs_tree.column("level", width=100, anchor=tk.CENTER)
        self.logs_tree.column("source", width=140, anchor=tk.CENTER)
        self.logs_tree.column("message", width=980)
        self.logs_tree.bind("<<TreeviewSelect>>", self._on_log_row_selected)

        log_scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.logs_tree.yview)
        log_scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.logs_tree.xview)
        self.logs_tree.configure(yscrollcommand=log_scroll_y.set, xscrollcommand=log_scroll_x.set)

        self.logs_tree.grid(row=0, column=0, sticky="nsew")
        log_scroll_y.grid(row=0, column=1, sticky="ns")
        log_scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        ttk.Label(top, text="Selected Log Details (never truncated):").pack(anchor="w", pady=(8, 2))
        self.log_details_text = tk.Text(top, height=8, wrap=tk.WORD)
        self.log_details_text.pack(fill=tk.X)
        self.log_details_text.configure(state=tk.DISABLED)

    def _build_ai_page(self) -> None:
        controls = ttk.Frame(self.ai_page)
        controls.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(controls, text="Group / Filter by Coin:").pack(side=tk.LEFT)
        self.ai_filter_var = tk.StringVar(value="ALL")
        self.ai_filter_box = ttk.Combobox(
            controls,
            textvariable=self.ai_filter_var,
            values=["ALL"],
            state="readonly",
            width=20,
        )
        self.ai_filter_box.pack(side=tk.LEFT, padx=(8, 0))
        self.ai_filter_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_ai_table())

        table_frame = ttk.Frame(self.ai_page)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        columns = (
            "symbol",
            "name",
            "verdict",
            "reason",
            "price",
            "vol",
            "lower",
            "upper",
            "net",
            "distance",
            "trend7d",
            "mom24h",
            "viable",
            "buy_amount",
            "buy_price",
            "usd_left",
            "cycle",
            "updated",
        )
        self.ai_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {
            "symbol": "Symbol",
            "name": "Name",
            "verdict": "Verdict",
            "reason": "Reason (full text)",
            "price": "Price",
            "vol": "Volatility %",
            "lower": "Lower Bound",
            "upper": "Upper Bound",
            "net": "Net Step %",
            "distance": "Distance Lower %",
            "trend7d": "Trend 7d %",
            "mom24h": "Momentum 24h %",
            "viable": "Viable",
            "buy_amount": "Buy Amount",
            "buy_price": "Buy Price",
            "usd_left": "USD Left",
            "cycle": "Cycle",
            "updated": "Last Updated",
        }
        widths = {
            "symbol": 85,
            "name": 170,
            "verdict": 90,
            "reason": 420,
            "price": 110,
            "vol": 110,
            "lower": 110,
            "upper": 110,
            "net": 100,
            "distance": 130,
            "trend7d": 110,
            "mom24h": 130,
            "viable": 80,
            "buy_amount": 120,
            "buy_price": 110,
            "usd_left": 110,
            "cycle": 80,
            "updated": 170,
        }
        for key in columns:
            self.ai_tree.heading(key, text=headings[key])
            self.ai_tree.column(key, width=widths[key], anchor=tk.W)
        self.ai_tree.bind("<<TreeviewSelect>>", self._on_ai_row_selected)

        ai_scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.ai_tree.yview)
        ai_scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.ai_tree.xview)
        self.ai_tree.configure(yscrollcommand=ai_scroll_y.set, xscrollcommand=ai_scroll_x.set)

        self.ai_tree.grid(row=0, column=0, sticky="nsew")
        ai_scroll_y.grid(row=0, column=1, sticky="ns")
        ai_scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        ttk.Label(self.ai_page, text="Selected Coin Full Event History (never truncated):").pack(
            anchor="w", padx=8, pady=(8, 2)
        )
        self.ai_detail_text = tk.Text(self.ai_page, height=9, wrap=tk.WORD)
        self.ai_detail_text.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.ai_detail_text.configure(state=tk.DISABLED)

    def _load_settings_into_form(self) -> None:
        for key, var in self.settings_vars.items():
            var.set(self.settings_values.get(key, ""))

    def _reload_settings(self) -> None:
        if self.settings_locked:
            messagebox.showwarning("Settings Locked", "Cannot reload settings while bot is running.")
            return
        self.settings_values = load_env_file(self.env_path)
        self._load_settings_into_form()
        self.status_var.set("Settings reloaded from .env")

    def _collect_settings(self) -> Dict[str, str]:
        return {key: var.get().strip() for key, var in self.settings_vars.items()}

    def _save_settings(self) -> None:
        if self.settings_locked:
            messagebox.showwarning("Settings Locked", "Cannot save settings while bot is running.")
            return
        values = self._collect_settings()
        ok, error = self._validate_settings(values)
        if not ok:
            messagebox.showerror("Invalid Settings", error)
            return
        save_env_file(self.env_path, values)
        self.settings_values = values
        self.status_var.set("Settings saved to .env")

    def _validate_settings(self, values: Dict[str, str]) -> Tuple[bool, str]:
        required = ["MAGICLABS_JWT", "MAGICLABS_API_KEY", "OPENROUTER_API_KEY"]
        for key in required:
            if not values.get(key, "").strip():
                return False, f"{key} is required."

        for group in SETTINGS_GROUPS:
            for field in group.fields:
                value = values.get(field.key, "").strip()
                if not value:
                    continue
                try:
                    if field.field_type == "int":
                        int(value)
                    elif field.field_type == "float":
                        float(value)
                except ValueError:
                    return False, f"{field.label} must be a valid {field.field_type}."
        return True, ""

    def _set_settings_lock(self, locked: bool) -> None:
        self.settings_locked = locked
        state = tk.DISABLED if locked else tk.NORMAL
        for widget in self.settings_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        self.btn_save.configure(state=state)
        self.btn_reload.configure(state=state)
        if locked:
            self.lock_var.set("Settings are locked while bot is running. Stop bot to edit settings.")
        else:
            self.lock_var.set("")

    def _start_bot(self) -> None:
        if self.process_runner.is_running:
            return
        values = self._collect_settings()
        ok, error = self._validate_settings(values)
        if not ok:
            messagebox.showerror("Invalid Settings", error)
            return
        save_env_file(self.env_path, values)
        self.settings_values = values

        self.status_var.set("Starting bot...")
        self._set_settings_lock(True)
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self._append_system_log("SYSTEM", "INFO", "Launching bot process with current settings.")

        try:
            self.process_runner.start(
                env_values=values,
                line_callback=lambda line: self.ui_queue.put(("line", line)),
                exit_callback=lambda code: self.ui_queue.put(("exit", code)),
                error_callback=lambda msg: self.ui_queue.put(("error", msg)),
            )
            self.status_var.set("Bot running")
        except Exception as exc:
            self._set_settings_lock(False)
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_stop.configure(state=tk.DISABLED)
            self.status_var.set("Failed to start")
            messagebox.showerror("Start Failed", str(exc))

    def _stop_bot(self) -> None:
        if not self.process_runner.is_running:
            return
        self.status_var.set("Stopping bot...")
        self._append_system_log("SYSTEM", "INFO", "Stop requested from UI.")
        try:
            self.process_runner.stop()
        except Exception as exc:
            messagebox.showerror("Stop Failed", str(exc))

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                event, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if event == "line":
                self._handle_runtime_line(str(payload))
            elif event == "exit":
                self._handle_process_exit(int(payload))
            elif event == "error":
                self._append_system_log("SYSTEM", "ERROR", str(payload))
                self.status_var.set("Runtime stream error")
        self.after(120, self._drain_ui_queue)

    def _append_system_log(self, source: str, level: str, message: str) -> None:
        self.log_records_count += 1
        item_id = self.logs_tree.insert(
            "",
            tk.END,
            values=(self.log_records_count, "-", level, source, message),
        )
        self.log_item_to_raw[item_id] = message
        self.logs_tree.yview_moveto(1.0)

    def _handle_runtime_line(self, line: str) -> None:
        record, _updated = self.log_parser.parse_line(line)
        self.log_records_count += 1
        if record:
            values = (
                self.log_records_count,
                record.timestamp,
                record.level,
                record.source,
                record.message,
            )
            item_id = self.logs_tree.insert("", tk.END, values=values)
            self.log_item_to_raw[item_id] = record.raw
        else:
            item_id = self.logs_tree.insert(
                "",
                tk.END,
                values=(self.log_records_count, "-", "RAW", "PROCESS", line),
            )
            self.log_item_to_raw[item_id] = line
        self.logs_tree.yview_moveto(1.0)
        self._refresh_ai_table()

    def _handle_process_exit(self, exit_code: int) -> None:
        self._set_settings_lock(False)
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        if exit_code == 0:
            self.status_var.set("Bot stopped cleanly")
        else:
            self.status_var.set(f"Bot exited with code {exit_code}")
        self._append_system_log("SYSTEM", "INFO", f"Bot process ended with code {exit_code}.")

    def _on_log_row_selected(self, _event: object) -> None:
        selected = self.logs_tree.selection()
        if not selected:
            return
        item_id = selected[0]
        raw = self.log_item_to_raw.get(item_id, "")
        self.log_details_text.configure(state=tk.NORMAL)
        self.log_details_text.delete("1.0", tk.END)
        self.log_details_text.insert(tk.END, raw)
        self.log_details_text.configure(state=tk.DISABLED)

    def _refresh_ai_table(self) -> None:
        selected_filter = self.ai_filter_var.get().strip() or "ALL"
        snapshots = self.log_parser.coin_snapshots

        symbols = ["ALL"] + sorted(snapshots.keys())
        if tuple(self.ai_filter_box["values"]) != tuple(symbols):
            self.ai_filter_box["values"] = symbols
            if selected_filter not in symbols:
                self.ai_filter_var.set("ALL")
                selected_filter = "ALL"

        for item_id in self.ai_tree.get_children():
            self.ai_tree.delete(item_id)
        self.ai_item_to_symbol.clear()

        for symbol in sorted(snapshots.keys()):
            if selected_filter != "ALL" and symbol != selected_filter:
                continue
            snap = snapshots[symbol]
            values = self._snapshot_row_values(snap)
            row_id = self.ai_tree.insert("", tk.END, values=values)
            self.ai_item_to_symbol[row_id] = symbol

    def _snapshot_row_values(self, snap: CoinSnapshot) -> Tuple[str, ...]:
        return (
            snap.symbol,
            snap.name,
            snap.verdict,
            snap.reason,
            snap.price,
            snap.volatility_pct,
            snap.lower_bound,
            snap.upper_bound,
            snap.net_step_profit_pct,
            snap.distance_to_lower_pct,
            snap.trend_7d_pct,
            snap.momentum_24h_pct,
            snap.viable,
            snap.buy_amount,
            snap.buy_price,
            snap.usd_left,
            str(snap.cycle_id),
            snap.last_updated_ts,
        )

    def _on_ai_row_selected(self, _event: object) -> None:
        selected = self.ai_tree.selection()
        if not selected:
            return
        symbol = self.ai_item_to_symbol.get(selected[0], "")
        if not symbol:
            return
        snapshot = self.log_parser.coin_snapshots.get(symbol)
        if not snapshot:
            return
        details = "\n".join(snapshot.all_messages) if snapshot.all_messages else "No details recorded yet."
        self.ai_detail_text.configure(state=tk.NORMAL)
        self.ai_detail_text.delete("1.0", tk.END)
        self.ai_detail_text.insert(tk.END, details)
        self.ai_detail_text.configure(state=tk.DISABLED)

    def _toggle_secret_visibility(self, key: str) -> None:
        entry = self.settings_entries.get(key)
        if entry is None:
            return
        current_show = entry.cget("show")
        entry.configure(show="" if current_show == "*" else "*")

    def _on_close(self) -> None:
        try:
            if self.process_runner.is_running:
                if not messagebox.askyesno(
                    "Exit Application",
                    "Bot is still running. Stop bot and exit safely?",
                ):
                    return
                self.status_var.set("Stopping bot before exit...")
                self.process_runner.shutdown()
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Exit Error", f"Failed to close cleanly: {exc}")
            self.destroy()


def launch_app() -> None:
    app = TradingBotApp()
    app.mainloop()
