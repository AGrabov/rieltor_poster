"""Запуск дашборду Rieltor без вікна терміналу.

Збирається в .exe через PyInstaller (--noconsole --onefile): подвійний клік
запускає Streamlit прихованим процесом (без терміналу) і відкриває браузер на
http://localhost:8501. Якщо дашборд уже працює — просто відкриває вкладку.

Зборка:
    uv run --with pyinstaller pyinstaller --noconsole --onefile ^
        --name RieltorDashboard --distpath dist --workpath build/pyinstaller ^
        --specpath build launch_dashboard.py

Зупинка дашборду: через «Диспетчер задач» (процес streamlit/pythonw) або з
самого дашборду.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8501
URL = f"http://localhost:{PORT}"

# Запасний шлях до проєкту, якщо .exe винесли кудись із-поза репозиторію.
FALLBACK_PROJECT_DIR = Path(r"D:\Coding\web_projects\Rieltor")


def find_project_dir() -> Path:
    """Знайти теку проєкту (де лежить dashboard.py).

    Шукаємо від розташування .exe/скрипта вгору по дереву, потім — запасний шлях.
    """
    if getattr(sys, "frozen", False):
        start = Path(sys.executable).resolve().parent
    else:
        start = Path(__file__).resolve().parent
    for d in (start, *start.parents):
        if (d / "dashboard.py").exists():
            return d
    if (FALLBACK_PROJECT_DIR / "dashboard.py").exists():
        return FALLBACK_PROJECT_DIR
    return start


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
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


def start_streamlit(project: Path) -> None:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    subprocess.Popen(
        streamlit_cmd(project),
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def main() -> None:
    project = find_project_dir()
    if not port_open(HOST, PORT):
        start_streamlit(project)
        # Чекаємо до ~30 с, поки сервер підніметься
        for _ in range(60):
            if port_open(HOST, PORT):
                break
            time.sleep(0.5)
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
