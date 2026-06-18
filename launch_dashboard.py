"""Запуск дашборду Rieltor з маленьким вікном-керуванням (без терміналу).

Відкриває невелике вікно «Rieltor Dashboard» (видно на панелі задач), запускає
Streamlit прихованим процесом і відкриває браузер на http://localhost:8501.
Закриття вікна (× або «Зупинити та вийти») зупиняє дашборд — як звичайна програма.

Це звичайний .py-файл (не збирається в .exe). Його запускає updater.py — той
спершу підтягує оновлення з git, потім стартує цей лаунчер із исходників, тож
будь-які зміни тут приїжджають користувачу автоматично через `git pull`.

Шляхи — відносні від кореня репозиторію. Лаунчер шукає проєкт відносно власного
розташування, тому абсолютні шляхи ніде не зашиті.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

HOST = "127.0.0.1"
PORT = 8501
URL = f"http://localhost:{PORT}"

def find_project_dir() -> Path | None:
    """Знайти теку проєкту (де лежить dashboard.py) — без прив'язки до конкретного ПК.

    Шукаємо вгору від розташування .exe/скрипта, потім від поточної робочої теки.
    Працює на будь-якому комп'ютері, якщо .exe лишається в теці проєкту (напр. dist/).
    Абсолютні шляхи ніде не зашиті — повертаємо None, якщо проєкт не знайдено.
    """
    starts: list[Path] = []
    if getattr(sys, "frozen", False):
        starts.append(Path(sys.executable).resolve().parent)
    else:
        starts.append(Path(__file__).resolve().parent)
    starts.append(Path.cwd())
    for start in starts:
        for d in (start, *start.parents):
            if (d / "dashboard.py").exists():
                return d
    return None


def port_open(host: str = HOST, port: int = PORT, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def streamlit_cmd(project: Path) -> list[str]:
    """Команда запуску Streamlit. Віддаємо перевагу pythonw.exe з .venv (без консолі)."""
    pyw = project / ".venv" / "Scripts" / "pythonw.exe"
    py = project / ".venv" / "Scripts" / "python.exe"
    if pyw.exists():
        base = [str(pyw), "-m", "streamlit"]
    elif py.exists():
        base = [str(py), "-m", "streamlit"]
    else:
        base = ["uv", "run", "streamlit"]
    return [
        *base,
        "run",
        "dashboard.py",
        "--server.headless=true",
        f"--server.port={PORT}",
        "--browser.gatherUsageStats=false",
    ]


def icon_path() -> Path | None:
    """Шлях до icon.ico (працює і в .exe через PyInstaller, і зі скрипта)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parent
    for cand in (base / "assets" / "icon.ico", base / "icon.ico"):
        if cand.exists():
            return cand
    return None


def start_streamlit(project: Path) -> subprocess.Popen:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    return subprocess.Popen(
        streamlit_cmd(project),
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )


class DashboardApp:
    """Маленьке вікно-керування: видно на панелі задач, × зупиняє дашборд."""

    def __init__(self) -> None:
        self.project = find_project_dir()
        self.proc: subprocess.Popen | None = None

        self.root = tk.Tk()
        self.root.title("Rieltor Dashboard")
        self.root.geometry("380x170")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        ico = icon_path()
        if ico:
            try:
                self.root.iconbitmap(default=str(ico))
            except Exception:  # noqa: BLE001
                pass

        self.status = tk.StringVar(value="Запуск дашборду…")
        ttk.Label(self.root, textvariable=self.status, wraplength=348, justify="left").pack(
            padx=16, pady=(18, 10), anchor="w"
        )

        btns = ttk.Frame(self.root)
        btns.pack(padx=16, pady=(4, 16), fill="x", side="bottom")
        self.open_btn = ttk.Button(btns, text="Відкрити в браузері", command=self.open_browser, state="disabled")
        self.open_btn.pack(side="left")
        ttk.Button(btns, text="Зупинити та вийти", command=self.on_close).pack(side="right")

        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self) -> None:
        if self.project is None:
            self._set_status(
                "Не знайдено dashboard.py.\nТримайте програму в теці проєкту (напр. dist/) "
                "і запускайте через ярлик, а не копію."
            )
            return
        started_here = False
        if not port_open():
            self._set_status("Запуск дашборду…")
            try:
                self.proc = start_streamlit(self.project)
                started_here = True
            except Exception as e:  # noqa: BLE001
                self._set_status(f"Помилка запуску: {e}")
                return
            for _ in range(60):  # ~30 с очікування сервера
                if port_open():
                    break
                time.sleep(0.5)

        if port_open():
            self._set_status(f"Дашборд працює:\n{URL}")
            self.root.after(0, lambda: self.open_btn.config(state="normal"))
            if started_here:
                self.root.after(0, self.open_browser)
        else:
            self._set_status("Не вдалося запустити дашборд.\nПеревірте середовище (.venv / uv).")

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status.set(text))

    def open_browser(self) -> None:
        webbrowser.open(URL)

    def _stop_streamlit(self) -> None:
        # Зупиняємо лише той процес, який ми запустили самі.
        if self.proc and self.proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def on_close(self) -> None:
        self._stop_streamlit()
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    DashboardApp().run()


if __name__ == "__main__":
    main()
