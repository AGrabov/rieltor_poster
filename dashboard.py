"""Rieltor Automation Dashboard.

Запуск:
    uv run streamlit run dashboard.py
"""

from __future__ import annotations

import subprocess
import time
from collections import deque
from pathlib import Path

import streamlit as st

from offer_db import OfferDB

# ── Конфіг ───────────────────────────────────────────────────────────

LOG_FILE = Path(__file__).parent / "logs" / "rieltor.log"
LOG_TAIL = 150
AUTO_REFRESH_SEC = 30
STATUSES = ["new", "posted", "failed", "skipped"]
STATUS_LABELS = {
    "new": "🔵 Нові",
    "posted": "🟢 Опубліковані",
    "failed": "🔴 Помилка",
    "skipped": "⚪ Пропущені",
}

PROPERTY_TYPES = ["Квартира", "Кімната", "Будинок", "Комерційна", "Ділянка", "Паркомісце"]
DEAL_TYPES = ["Продаж", "Оренда"]

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


def launch(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent),
    )


def build_collect_cmd(
    max_pages: int | None,
    max_count: int | None,
    property_type: str | None = None,
    deal_type: str | None = None,
) -> list[str]:
    cmd = ["uv", "run", "python", "main.py", "collect"]
    if max_pages:
        cmd += ["--max-pages", str(max_pages)]
    if max_count:
        cmd += ["--max-count", str(max_count)]
    if deal_type:
        cmd += ["--deal-type", deal_type]
    if property_type:
        cmd += ["--property-type", property_type]
    return cmd


def build_post_cmd(
    publish: bool,
    max_count: int | None,
    property_type: str | None = None,
    deal_type: str | None = None,
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


# ── Session state ─────────────────────────────────────────────────────

if "collect_proc" not in st.session_state:
    st.session_state.collect_proc = None
if "post_proc" not in st.session_state:
    st.session_state.post_proc = None
if "schema_proc" not in st.session_state:
    st.session_state.schema_proc = None


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
        auto_refresh = st.toggle("Авто 30с", value=True)

st.divider()

# ── Основний layout ───────────────────────────────────────────────────

left, right = st.columns([1, 2], gap="large")

# ── Статистика ────────────────────────────────────────────────────────

with left:
    st.subheader("Статистика")
    summary = get_summary()
    total = sum(summary.values())

    for status in STATUSES:
        st.metric(label=STATUS_LABELS[status], value=summary[status])

    if total:
        st.caption(f"Всього: {total}")
        st.progress(
            summary["posted"] / total,
            text=f"опубліковано {summary['posted']}/{total}",
        )

# ── Керування ─────────────────────────────────────────────────────────

with right:
    st.subheader("Керування")

    # Фаза 1 — Collect
    with st.container(border=True):
        st.markdown("**Фаза 1 — Збір** (CRM → БД)")
        c1, c2 = st.columns(2)
        with c1:
            max_pages = st.number_input(
                "Макс. сторінок",
                min_value=0,
                value=0,
                help="0 = без обмежень",
            )
        with c2:
            max_count_c = st.number_input(
                "Макс. об'єктів",
                min_value=0,
                value=0,
                key="max_count_collect",
                help="0 = без обмежень",
            )
        f1, f2 = st.columns(2)
        with f1:
            collect_property_type = st.selectbox(
                "Тип об'єкта",
                options=["Всі"] + PROPERTY_TYPES,
                key="collect_property_type",
            )
        with f2:
            collect_deal_type = st.selectbox(
                "Тип угоди",
                options=["Всі"] + DEAL_TYPES,
                key="collect_deal_type",
            )
        collect_btn = st.button(
            "▶ Зібрати",
            use_container_width=True,
            disabled=proc_is_running(st.session_state.collect_proc),
        )

        if collect_btn:
            st.session_state.collect_proc = launch(build_collect_cmd(
                max_pages or None,
                max_count_c or None,
                property_type=collect_property_type if collect_property_type != "Всі" else None,
                deal_type=collect_deal_type if collect_deal_type != "Всі" else None,
            ))
            st.toast("Збір запущено!", icon="▶")

        if proc_is_running(st.session_state.collect_proc):
            st.info("⏳ Збір виконується...")
        elif st.session_state.collect_proc is not None:
            rc = st.session_state.collect_proc.returncode
            if rc == 0:
                st.success("✅ Збір завершено")
            else:
                st.error(f"❌ Збір завершився з кодом {rc}")

    # Фаза 2 — Post
    with st.container(border=True):
        st.markdown("**Фаза 2 — Публікація** (БД → Rieltor.ua)")
        p1, p2 = st.columns(2)
        with p1:
            publish = st.checkbox(
                "Публікувати",
                value=False,
                help="Без галочки — зберігається як чернетка",
            )
        with p2:
            max_count_p = st.number_input(
                "Макс. об'єктів",
                min_value=0,
                value=0,
                key="max_count_post",
                help="0 = без обмежень",
            )
        pf1, pf2 = st.columns(2)
        with pf1:
            post_property_type = st.selectbox(
                "Тип об'єкта",
                options=["Всі"] + PROPERTY_TYPES,
                key="post_property_type",
            )
        with pf2:
            post_deal_type = st.selectbox(
                "Тип угоди",
                options=["Всі"] + DEAL_TYPES,
                key="post_deal_type",
            )
        post_btn = st.button(
            "▶ Опублікувати",
            use_container_width=True,
            disabled=proc_is_running(st.session_state.post_proc),
        )

        if post_btn:
            st.session_state.post_proc = launch(build_post_cmd(
                publish,
                max_count_p or None,
                property_type=post_property_type if post_property_type != "Всі" else None,
                deal_type=post_deal_type if post_deal_type != "Всі" else None,
            ))
            st.toast("Публікацію запущено!", icon="▶")

        if proc_is_running(st.session_state.post_proc):
            st.info("⏳ Публікація виконується...")
        elif st.session_state.post_proc is not None:
            rc = st.session_state.post_proc.returncode
            if rc == 0:
                st.success("✅ Публікацію завершено")
            else:
                st.error(f"❌ Публікацію завершено з кодом {rc}")

    # Оновлення схем
    with st.container(border=True):
        s1, s2 = st.columns([3, 1])
        with s1:
            st.markdown("**Схеми форм** (Rieltor.ua → `schemas/`)")
            st.caption("Збирає актуальні поля форм через браузер. Займає ~5–10 хв.")
        with s2:
            st.write("")
            schema_btn = st.button(
                "🔄 Оновити схеми",
                use_container_width=True,
                disabled=proc_is_running(st.session_state.schema_proc),
            )

        if schema_btn:
            st.session_state.schema_proc = launch(["uv", "run", "python", "rieltor_handler/run_schema_collection.py"])
            st.toast("Збір схем запущено!", icon="🔄")

        if proc_is_running(st.session_state.schema_proc):
            st.info("⏳ Збір схем виконується (це може зайняти кілька хвилин)...")
        elif st.session_state.schema_proc is not None:
            rc = st.session_state.schema_proc.returncode
            if rc == 0:
                st.success("✅ Схеми оновлено")
            else:
                st.error(f"❌ Збір схем завершився з кодом {rc}")

# ── Логи ─────────────────────────────────────────────────────────────

st.divider()
st.subheader("Логи")

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
