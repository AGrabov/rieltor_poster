"""Rieltor Automation Dashboard.

Запуск:
    uv run streamlit run dashboard.py
"""

from __future__ import annotations

import os
import subprocess
import time
from collections import deque
from pathlib import Path

import streamlit as st

from main import read_drafts_count
from offer_db import OfferDB

# ── Конфіг ───────────────────────────────────────────────────────────

LOG_FILE = Path(__file__).parent / "logs" / "rieltor.log"
LOG_TAIL = 100
AUTO_REFRESH_SEC = 15
STATUSES = ["new", "posted", "failed", "skipped"]
STATUS_LABELS = {
    "new": "🔵 Нові",
    "posted": "🟢 Опубліковані",
    "failed": "🔴 Помилка",
    "skipped": "⚪ Пропущені",
}

PROPERTY_TYPES = ["Квартира", "Кімната", "Будинок", "Комерційна", "Ділянка", "Паркомісце", "Безкоштовне"]
DEAL_TYPES = ["Продаж", "Оренда"]
PROP_OPTIONS = ["Всі", *PROPERTY_TYPES]
DEAL_OPTIONS = ["Всі", *DEAL_TYPES]
DEFAULT_PROP = "Безкоштовне"
DEFAULT_MAX_COUNT = 50

st.set_page_config(
    page_title="Rieltor Dashboard",
    page_icon="🏠",
    layout="wide",
)


# ── Helpers ───────────────────────────────────────────────────────────


def get_summary() -> dict[str, int]:
    try:
        with OfferDB() as db:
            raw = db.summary()
        return {s: raw.get(s, 0) for s in STATUSES}
    except Exception as e:
        st.error(f"Помилка читання БД: {e}")
        return {s: 0 for s in STATUSES}


def read_log_tail(n: int = LOG_TAIL) -> str:
    if not LOG_FILE.exists():
        return "_Лог-файл не знайдено_"
    lines = deque(LOG_FILE.open(encoding="utf-8", errors="replace"), maxlen=n)
    return "".join(lines)


def proc_is_running(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


def stop_proc(proc: subprocess.Popen | None) -> None:
    """Kill the process and all its children (cross-platform)."""
    if not proc or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def launch(cmd: list[str]) -> subprocess.Popen:
    """Запустити фоновий процес. Лог завжди DEBUG (розмір файлу обмежено ротацією).

    Браузер не запускається в headless (на цьому сайті headless не працює) —
    прапорець --headless навмисно не передається.
    """
    env = {**os.environ, "LOG_LEVEL": "DEBUG"}
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent),
        env=env,
    )


def _opt(value: str | None) -> str | None:
    """'Всі' → None (без фільтра), інакше — саме значення."""
    return None if not value or value == "Всі" else value


def build_collect_cmd(max_count: int | None, property_type: str | None = None, deal_type: str | None = None) -> list[str]:
    cmd = ["uv", "run", "python", "main.py", "collect"]
    if max_count:
        cmd += ["--max-count", str(max_count)]
    if deal_type:
        cmd += ["--deal-type", deal_type]
    if property_type:
        cmd += ["--property-type", property_type]
    return cmd


def build_post_cmd(
    publish: bool, max_count: int | None, property_type: str | None = None, deal_type: str | None = None
) -> list[str]:
    cmd = ["uv", "run", "python", "main.py", "post"]
    if publish:
        cmd += ["--publish"]
    if max_count:
        cmd += ["--max-count", str(max_count)]
    if deal_type:
        cmd += ["--deal-type", deal_type]
    if property_type:
        cmd += ["--property-type", property_type]
    return cmd


def render_proc_status(proc_key: str, running_msg: str, done_msg: str = "Готово") -> None:
    """Уніфікований блок «виконується / зупинити / результат» для фонового процесу."""
    proc = st.session_state.get(proc_key)
    if proc_is_running(proc):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.info(f"⏳ {running_msg}")
        with c2:
            if st.button("⏹ Зупинити", key=f"stop_{proc_key}", use_container_width=True):
                stop_proc(proc)
                st.toast("Зупинено", icon="⏹")
                st.rerun()
    elif proc is not None:
        rc = proc.returncode
        if rc == 0:
            st.success(f"✅ {done_msg}")
        else:
            st.error(f"❌ Завершилось з кодом {rc}")


# ── Session state ─────────────────────────────────────────────────────

for _k in ("collect_proc", "post_proc", "schema_proc", "cadastral_proc", "cleanup_proc", "publish_drafts_proc"):
    st.session_state.setdefault(_k, None)
st.session_state.setdefault("drafts_count", None)


# ── Заголовок ─────────────────────────────────────────────────────────

header_left, header_right = st.columns([3, 1])
with header_left:
    st.title("🏠 Rieltor Dashboard")
with header_right:
    st.write("")
    col_refresh, col_auto = st.columns(2)
    with col_refresh:
        manual_refresh = st.button("⟳ Оновити", use_container_width=True)
    with col_auto:
        auto_refresh = st.toggle(f"Авто {AUTO_REFRESH_SEC}с", value=True)


# ── Статистика (завжди видима) ────────────────────────────────────────

summary = get_summary()
total = sum(summary.values())

stat_cols = st.columns([2, 2, 2, 2, 1])
for col, status in zip(stat_cols[:4], STATUSES):
    col.metric(label=STATUS_LABELS[status], value=summary[status])
with stat_cols[4]:
    st.caption("Кадастр")
    if st.button(
        "🗺",
        key="cadastral_btn",
        help="Знайти кадастрові номери для всіх об'єктів без нього "
        "(Будинок, Ділянка, Комерційна). Фоновий процес, без налаштувань.",
        disabled=proc_is_running(st.session_state.cadastral_proc),
    ):
        st.session_state.cadastral_proc = launch(["uv", "run", "python", "main.py", "cadastral"])
        st.toast("Пошук кадастрових номерів запущено!", icon="🗺")
        st.rerun()

if total:
    st.progress(summary["posted"] / total, text=f"опубліковано {summary['posted']}/{total} (всього {total})")
render_proc_status("cadastral_proc", "Пошук кадастрових номерів...", "Кадастрові номери оновлено")

st.divider()


# ── Вкладки ───────────────────────────────────────────────────────────

tab_main, tab_service, tab_db = st.tabs(["📋 Основне", "🛠 Сервіс", "🗄 База / ⚠ Небезпечна зона"])

# ── Вкладка: Основне (головний потік) ─────────────────────────────────
with tab_main:
    col_collect, col_post = st.columns(2)

    # Фаза 1 — Збір
    with col_collect, st.container(border=True):
        st.markdown("**Фаза 1 — Збір** (CRM → БД)")
        with st.popover("⚙ Параметри", use_container_width=True):
            collect_max = st.number_input(
                "Макс. кількість", min_value=0, value=DEFAULT_MAX_COUNT, key="collect_max", help="0 = без обмежень"
            )
            collect_pt = st.selectbox(
                "Тип об'єкта",
                options=PROP_OPTIONS,
                index=PROP_OPTIONS.index(DEFAULT_PROP),
                key="collect_pt",
                help="Безкоштовне = Будинок + Комерційна + Ділянка + Паркомісце",
            )
            collect_dt = st.selectbox("Тип угоди", options=DEAL_OPTIONS, index=0, key="collect_dt")
        if st.button("▶ Зібрати", use_container_width=True, disabled=proc_is_running(st.session_state.collect_proc)):
            st.session_state.collect_proc = launch(
                build_collect_cmd(collect_max or None, _opt(collect_pt), _opt(collect_dt))
            )
            st.toast("Збір запущено!", icon="▶")
            st.rerun()
        render_proc_status("collect_proc", "Збір виконується...", "Збір завершено")

    # Фаза 2 — Публікація
    with col_post, st.container(border=True):
        st.markdown("**Фаза 2 — Публікація** (БД → Rieltor.ua)")
        with st.popover("⚙ Параметри", use_container_width=True):
            post_publish = st.checkbox("Публікувати (інакше — чернетка)", value=False, key="post_publish")
            post_max = st.number_input(
                "Макс. кількість", min_value=0, value=DEFAULT_MAX_COUNT, key="post_max", help="0 = без обмежень"
            )
            post_pt = st.selectbox(
                "Тип об'єкта",
                options=PROP_OPTIONS,
                index=PROP_OPTIONS.index(DEFAULT_PROP),
                key="post_pt",
                help="Безкоштовне = Будинок + Комерційна + Ділянка + Паркомісце",
            )
            post_dt = st.selectbox("Тип угоди", options=DEAL_OPTIONS, index=0, key="post_dt")
        post_label = "▶ Опублікувати" if post_publish else "▶ Зберегти чернетки"
        if st.button(post_label, use_container_width=True, disabled=proc_is_running(st.session_state.post_proc)):
            st.session_state.post_proc = launch(build_post_cmd(post_publish, post_max or None, _opt(post_pt), _opt(post_dt)))
            st.toast("Публікацію запущено!", icon="▶")
            st.rerun()
        render_proc_status("post_proc", "Публікація виконується...", "Публікацію завершено")


# ── Вкладка: Сервіс (рідкі/допоміжні дії) ─────────────────────────────
with tab_service:
    # Масова публікація чернеток
    with st.container(border=True):
        st.markdown("**📤 Опублікувати чернетки** (rieltor.ua)")
        st.caption("Крок 1 — перевірити кількість. Крок 2 — обрати скільки/за який період і опублікувати.")

        if st.button(
            "🔍 Перевірити чернетки",
            use_container_width=True,
            disabled=proc_is_running(st.session_state.publish_drafts_proc),
        ):
            proc = launch(["uv", "run", "python", "main.py", "publish-drafts", "--count-only"])
            try:
                proc.wait(timeout=90)
                st.session_state.drafts_count = read_drafts_count()
            except subprocess.TimeoutExpired:
                stop_proc(proc)
                st.warning("Перевірка зависла — процес зупинено. Спробуйте ще раз.")
            st.rerun()

        n = st.session_state.drafts_count
        if n is not None:
            st.info(f"Чернеток на сайті: **{n}**")

        if n:
            pd1, pd2 = st.columns(2)
            with pd1:
                pub_max = st.number_input("Скільки публікувати", min_value=0, value=0, key="pub_drafts_max", help="0 = всі")
            with pd2:
                pub_delay = st.number_input("Затримка, с", min_value=0.0, value=3.0, step=0.5, key="pub_drafts_delay")
            dd1, dd2 = st.columns(2)
            with dd1:
                pub_from = st.date_input("Дата з", value=None, key="pub_drafts_from")
            with dd2:
                pub_to = st.date_input("Дата по", value=None, key="pub_drafts_to")
            confirm_pub = st.checkbox("Я підтверджую публікацію", value=False, key="confirm_pub_drafts")

            if st.button(
                "📤 Опублікувати",
                use_container_width=True,
                disabled=not confirm_pub or proc_is_running(st.session_state.publish_drafts_proc),
            ):
                cmd = ["uv", "run", "python", "main.py", "publish-drafts", "--delay", str(pub_delay)]
                if pub_max:
                    cmd += ["--max-count", str(int(pub_max))]
                if pub_from:
                    cmd += ["--date-from", pub_from.isoformat()]
                if pub_to:
                    cmd += ["--date-to", pub_to.isoformat()]
                st.session_state.publish_drafts_proc = launch(cmd)
                st.toast("Публікацію чернеток запущено!", icon="📤")
                st.rerun()
        elif n == 0:
            st.caption("Чернеток немає — публікувати нічого.")

        render_proc_status("publish_drafts_proc", "Публікація чернеток виконується...", "Публікацію чернеток завершено")

    # Оновлення схем
    with st.container(border=True):
        s1, s2 = st.columns([3, 1])
        with s1:
            st.markdown("**Схеми форм** (Rieltor.ua → `schemas/`)")
            st.caption("Збирає актуальні поля форм через браузер. Займає ~5–10 хв.")
        with s2:
            st.write("")
            if st.button(
                "🔄 Оновити схеми", use_container_width=True, disabled=proc_is_running(st.session_state.schema_proc)
            ):
                st.session_state.schema_proc = launch(["uv", "run", "python", "rieltor_handler/run_schema_collection.py"])
                st.toast("Збір схем запущено!", icon="🔄")
                st.rerun()
        render_proc_status("schema_proc", "Збір схем виконується (кілька хвилин)...", "Схеми оновлено")


# ── Вкладка: База / Небезпечна зона ───────────────────────────────────
with tab_db:
    st.warning("⚠ Дії в цьому розділі незворотні.")

    # Видалення з БД за статусом
    with st.container(border=True):
        st.markdown("**🗑 Видалити з БД за статусом**")
        del_statuses = st.multiselect(
            "Статуси для видалення",
            options=STATUSES,
            default=["failed"],
            format_func=lambda s: STATUS_LABELS.get(s, s),
            key="del_statuses",
        )
        del_count = sum(summary[s] for s in del_statuses)
        st.caption(f"Буде видалено об'єктів: {del_count}. Незворотно.")
        confirm_del = st.checkbox(
            "Я підтверджую видалення", value=False, key="confirm_del_status", disabled=del_count == 0
        )
        if st.button("🗑 Видалити вибрані", use_container_width=True, disabled=not confirm_del or del_count == 0):
            try:
                with OfferDB() as db:
                    deleted = db.delete_by_statuses(del_statuses)
                st.toast(f"Видалено {deleted} записів", icon="🗑")
                st.rerun()
            except Exception as e:
                st.error(f"Помилка видалення: {e}")

    # Очистка сміття на rieltor.ua
    with st.container(border=True):
        st.markdown("**🗑 Очистити сміття на rieltor.ua**")
        st.caption(
            "Видаляє ВСІ об'єкти із «Закритої бази», потім остаточно чистить «Видалені». "
            "Неудачні/неправильні чернетки. Незворотно!"
        )
        tc1, tc2 = st.columns([3, 1])
        with tc1:
            confirm_cleanup = st.checkbox("Я підтверджую видалення", value=False, key="confirm_cleanup")
        with tc2:
            max_count_clean = st.number_input("Макс.", min_value=0, value=0, key="max_count_cleanup", help="0 = без обмежень")
        if st.button(
            "🗑 Очистити «Закриту базу»",
            use_container_width=True,
            disabled=not confirm_cleanup or proc_is_running(st.session_state.cleanup_proc),
        ):
            cmd = ["uv", "run", "python", "main.py", "clean-trash"]
            if max_count_clean:
                cmd += ["--max-count", str(max_count_clean)]
            st.session_state.cleanup_proc = launch(cmd)
            st.toast("Очистку «Закритої бази» запущено!", icon="🗑")
            st.rerun()
        render_proc_status("cleanup_proc", "Очистка виконується...", "Очистку завершено")


# ── Логи ─────────────────────────────────────────────────────────────

st.divider()
log_header_left, log_header_right = st.columns([3, 1])
with log_header_left:
    st.subheader("Логи")
with log_header_right:
    st.write("")
    if st.button("🗑 Очистити логи", use_container_width=True):
        try:
            LOG_FILE.write_text("", encoding="utf-8")
            st.toast("Логи очищено", icon="🗑")
        except Exception as e:
            st.error(f"Помилка очищення: {e}")

log_lines = st.number_input(
    "Рядків",
    min_value=20,
    max_value=500,
    value=LOG_TAIL,
    step=50,
    label_visibility="collapsed",
)
st.code(read_log_tail(int(log_lines)), language=None)

# ── Автооновлення ─────────────────────────────────────────────────────

if auto_refresh or manual_refresh:
    if auto_refresh and not manual_refresh:
        time.sleep(AUTO_REFRESH_SEC)
    st.rerun()
