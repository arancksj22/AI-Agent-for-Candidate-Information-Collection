"""Terminal chat I/O with an inactivity timeout that works on Windows and Unix."""
from __future__ import annotations

import queue
import sys
import threading
import time

EOF = object()


class Terminal:
    def __init__(self, show_actions: bool = True):
        self.show_actions = show_actions
        self._lines: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError):
                line = ""
            if line == "":
                self._lines.put(EOF)
                return
            self._lines.put(line.rstrip("\r\n"))

    def say(self, text: str) -> None:
        print(f"\nAgent: {text}", flush=True)

    def stream_start(self) -> None:
        print("\nAgent: ", end="", flush=True)

    def stream(self, text: str) -> None:
        print(text, end="", flush=True)

    def stream_end(self) -> None:
        print("", flush=True)

    def info(self, text: str) -> None:
        if self.show_actions:
            print(f"  · {text}", flush=True)

    def ask(self, timeout_s: float):
        """Wait for one candidate message. Returns the text, None on timeout, or EOF."""
        print("You: ", end="", flush=True)
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("", flush=True)
                return None
            try:
                # Short waits keep Ctrl+C responsive on Windows.
                return self._lines.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
