"""Watchdog 24/7: Tự động giám sát và giữ cho Social Video Studio + Bot Telegram @thegioithread_bot chạy xuyên suốt 24/24h trên Cloud VPS."""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "bot_24_7.log")

_stop_requested = False
_child_proc = None


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 12 * 1024 * 1024:
            old_file = LOG_FILE + ".old"
            if os.path.exists(old_file):
                try:
                    os.remove(old_file)
                except Exception:
                    pass
            os.rename(LOG_FILE, old_file)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def _handle_sigterm(signum, frame) -> None:
    global _stop_requested, _child_proc
    _stop_requested = True
    log(f"Received signal {signum}. Gracefully stopping child server for 24/7 handover...")
    if _child_proc and _child_proc.poll() is None:
        try:
            _child_proc.terminate()
            try:
                _child_proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                log("Child server did not exit within 6s. Forcing kill...")
                _child_proc.kill()
        except Exception:
            pass


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def main() -> None:
    global _child_proc
    os.chdir(BASE_DIR)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_BROWSER"] = "1"
    env["DEV_RELOAD"] = "0"

    log("=== KHỞI ĐỘNG WATCHDOG SOCIAL VIDEO BOT (@thegioithread_bot) 24/7 ===")

    while not _stop_requested:
        try:
            log("Starting run.py (FastAPI + Telegram Bot Polling)...")
            with open(LOG_FILE, "a", encoding="utf-8") as log_out:
                _child_proc = subprocess.Popen(
                    [sys.executable, "-u", "run.py"],
                    cwd=BASE_DIR,
                    env=env,
                    stdout=log_out,
                    stderr=subprocess.STDOUT,
                )
                code = _child_proc.wait()
            if _stop_requested:
                log("Clean shutdown completed for handover.")
                break
            log(f"run.py exited with code {code}. Tự động khởi động lại sau 3 giây...")
        except KeyboardInterrupt:
            log("Watchdog stopped.")
            break
        except Exception as e:
            log(f"Watchdog error: {e}. Thử lại sau 4 giây...")
        time.sleep(3)


if __name__ == "__main__":
    main()
