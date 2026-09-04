from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import threading
from typing import Callable, Dict, Optional


LineCallback = Callable[[str], None]
ExitCallback = Callable[[int], None]
ErrorCallback = Callable[[str], None]


class BotProcessRunner:
    def __init__(self, project_root: Path, script_name: str = "quant-py-trading-bot.py") -> None:
        self.project_root = project_root
        self.script_path = self.project_root / script_name
        self.process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._line_callback: Optional[LineCallback] = None
        self._exit_callback: Optional[ExitCallback] = None
        self._error_callback: Optional[ErrorCallback] = None
        self._stop_requested = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(
        self,
        env_values: Dict[str, str],
        line_callback: LineCallback,
        exit_callback: ExitCallback,
        error_callback: ErrorCallback,
    ) -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("Bot is already running.")
            if not self.script_path.exists():
                raise FileNotFoundError(f"Missing script: {self.script_path}")

            self._line_callback = line_callback
            self._exit_callback = exit_callback
            self._error_callback = error_callback
            self._stop_requested = False

            run_env = dict(**env_values)
            merged_env = dict(**os.environ)
            merged_env.update(run_env)
            merged_env["PYTHONUNBUFFERED"] = "1"

            self.process = subprocess.Popen(
                [sys.executable, str(self.script_path)],
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=merged_env,
            )
            self._reader_thread = threading.Thread(target=self._stream_output, daemon=True)
            self._reader_thread.start()

    def _stream_output(self) -> None:
        proc = self.process
        if proc is None:
            return
        try:
            if proc.stdout is not None:
                for raw_line in proc.stdout:
                    line = raw_line.rstrip("\n")
                    if self._line_callback:
                        self._line_callback(line)
        except Exception as exc:
            if self._error_callback:
                self._error_callback(f"Output stream error: {exc}")
        finally:
            exit_code = proc.wait()
            if self._exit_callback:
                self._exit_callback(exit_code)

    def stop(self, timeout_seconds: float = 8.0) -> None:
        with self._lock:
            if not self.is_running or self.process is None:
                return
            self._stop_requested = True
            proc = self.process

        proc.terminate()
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

    def shutdown(self) -> None:
        self.stop()
        thread = self._reader_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
