"""Бутстрап-апдейтер Rieltor Dashboard: оновлює проєкт із git і запускає лаунчер.

Це єдиний файл, який збирається в .exe. Логіка тут навмисно мінімальна й стабільна
— її майже ніколи не доведеться змінювати, тож запущений .exe ніколи не конфліктує
сам із собою під час `git pull`. Усе, що може еволюціонувати (вікно-керування,
запуск Streamlit, сам дашборд), лишається звичайними .py-файлами й оновлюється з git.

Послідовність роботи:
    знайти теку проєкту → (best-effort) git pull --ff-only → uv sync, якщо
    змінився uv.lock/pyproject.toml → запустити launch_dashboard.py з исходників.

Будь-яка помилка оновлення (немає git, немає мережі, приватний репозиторій,
локальні зміни) НЕ блокує запуск — просто стартуємо на тому, що вже є на диску.

Зборка (з кореня репозиторію, Windows):
    uv run --with pyinstaller pyinstaller --noconsole --onefile ^
        --name RieltorDashboard --icon assets/icon.ico ^
        --add-data "assets/icon.ico;assets" ^
        --distpath dist --workpath build/pyinstaller --specpath build/pyinstaller updater.py

Шляхи — відносні від кореня репозиторію, тож зборка переносна між ПК. .exe шукає
проєкт відносно власного розташування, тому абсолютні шляхи ніде не зашиті.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

# Прапор «без консольного вікна» для дочірніх процесів (тихий git/uv/запуск).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

GIT_TIMEOUT = 60      # с — на `git pull` (мережа може бути повільною)
SYNC_TIMEOUT = 300    # с — на `uv sync` (може докачувати пакети)
LAUNCH_PROBE = 1.0    # с — дати лаунчеру з'явитися, перш ніж закрити сплэш


def find_project_dir() -> Path | None:
    """Знайти теку проєкту (де лежить dashboard.py) — без прив'язки до конкретного ПК.

    Шукаємо вгору від розташування .exe/скрипта, потім від поточної робочої теки.
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


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess | None:
    """Тихо виконати команду. Повертає None при будь-якій помилці (fail-soft)."""
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except Exception:  # noqa: BLE001 — навмисно глушимо все: оновлення не критичне
        return None


def git_available() -> bool:
    cp = _run(["git", "--version"], Path.cwd(), 10)
    return bool(cp and cp.returncode == 0)


def is_git_repo(project: Path) -> bool:
    return (project / ".git").exists()


def _head(project: Path) -> str | None:
    cp = _run(["git", "-C", str(project), "rev-parse", "HEAD"], project, 10)
    return cp.stdout.strip() if cp and cp.returncode == 0 else None


def protect_self(project: Path) -> None:
    """Вивести запущений .exe з оновлення робочого дерева (skip-worktree).

    Windows тримає лок на запущеному .exe — git не зможе його перезаписати. Якщо
    колись закоммитимо нову зборку, без цього `git pull` падав би й оновлення
    зависало б. Помічаємо файл як skip-worktree → git ніколи не чіпає його на диску.
    Ідемпотентно; помилки (файл не трекається тощо) глушимо.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        rel = Path(sys.executable).resolve().relative_to(project)
    except ValueError:
        return  # .exe лежить поза текою проєкту — нічого захищати
    _run(
        ["git", "-C", str(project), "update-index", "--skip-worktree", str(rel).replace("\\", "/")],
        project,
        10,
    )


def _lockfiles_changed(project: Path, before: str | None, after: str | None) -> bool:
    """Чи зачепило оновлення uv.lock/pyproject.toml — тобто чи потрібен uv sync."""
    if not before or not after or before == after:
        return False
    cp = _run(["git", "-C", str(project), "diff", "--name-only", before, after], project, 15)
    if not cp or cp.returncode != 0:
        return True  # не змогли визначити — синхронізуємо про всяк випадок
    changed = {line.strip() for line in cp.stdout.splitlines()}
    return bool(changed & {"uv.lock", "pyproject.toml"})


def update_project(project: Path, status) -> None:
    """Best-effort оновлення з git + синхронізація залежностей. Ніколи не кидає."""
    if not (is_git_repo(project) and git_available()):
        return
    try:
        status("Перевірка оновлень…")
        protect_self(project)
        before = _head(project)
        cp = _run(["git", "-C", str(project), "pull", "--ff-only"], project, GIT_TIMEOUT)
        if not (cp and cp.returncode == 0):
            return  # офлайн / конфлікт / локальні зміни — запускаємось на старому коді
        after = _head(project)
        if after == before:
            return  # вже найновіша версія
        status("Оновлення отримано…")
        if _lockfiles_changed(project, before, after):
            status("Оновлення залежностей…")
            _run(["uv", "sync"], project, SYNC_TIMEOUT)
    except Exception:  # noqa: BLE001 — оновлення не повинно блокувати запуск
        pass


def launcher_cmd(project: Path) -> list[str]:
    """Команда запуску лаунчера з исходників.

    Беремо python.exe (а не pythonw.exe): під pythonw лаунчер/streamlit падали
    тихо. Консоль однаково прихована через CREATE_NO_WINDOW у :func:`launch`.
    """
    py = project / ".venv" / "Scripts" / "python.exe"
    pyw = project / ".venv" / "Scripts" / "pythonw.exe"
    script = str(project / "launch_dashboard.py")
    if py.exists():
        return [str(py), script]
    if pyw.exists():
        return [str(pyw), script]
    return ["uv", "run", "python", script]


def launch(project: Path) -> None:
    subprocess.Popen(
        launcher_cmd(project),
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )


class UpdaterApp:
    """Маленьке вікно-сплэш: показує стан оновлення, потім запускає лаунчер і зникає."""

    def __init__(self) -> None:
        self.project = find_project_dir()

        self.root = tk.Tk()
        self.root.title("Rieltor")
        self.root.geometry("360x130")
        self.root.resizable(False, False)

        ico = icon_path()
        if ico:
            try:
                self.root.iconbitmap(default=str(ico))
            except Exception:  # noqa: BLE001
                pass

        self.status = tk.StringVar(value="Запуск…")
        ttk.Label(self.root, textvariable=self.status, wraplength=328, justify="left").pack(
            padx=16, pady=(20, 12), anchor="w"
        )
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(padx=16, pady=(0, 16), fill="x", side="bottom")
        self.progress.start(12)

        threading.Thread(target=self._boot, daemon=True).start()

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status.set(text))

    def _fail(self, text: str) -> None:
        """Термінальна помилка: показуємо повідомлення, лишаємо вікно відкритим."""
        self._set_status(text)
        self.root.after(0, self.progress.stop)

    def _boot(self) -> None:
        if self.project is None:
            self._fail(
                "Не знайдено dashboard.py.\nТримайте програму в теці проєкту (напр. dist/) "
                "і запускайте через ярлик, а не копію."
            )
            return

        update_project(self.project, self._set_status)

        self._set_status("Запуск дашборду…")
        try:
            launch(self.project)
        except Exception as e:  # noqa: BLE001
            self._fail(f"Не вдалося запустити: {e}")
            return

        time.sleep(LAUNCH_PROBE)  # дати вікну лаунчера з'явитися перед закриттям сплэша
        self.root.after(0, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    UpdaterApp().run()


if __name__ == "__main__":
    main()
