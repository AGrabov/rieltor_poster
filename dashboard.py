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


LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def launch(cmd: list[str], log_level: str = "INFO") -> subprocess.Popen:
    env = {**os.environ, "LOG_LEVEL": log_level}
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent),
        env=env,
    )


def build_collect_cmd(
    max_pages: int | None,
    max_count: int | None,
    headless: bool = True,
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
    if headless:
        cmd += ["--headless"]
    return cmd


def build_post_cmd(
    publish: bool,
    max_count: int | None,
    headless: bool = True,
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
    if headless:
        cmd += ["--headless"]
    return cmd


# ── Session state ─────────────────────────────────────────────────────

if "collect_proc" not in st.session_state:
    st.session_state.collect_proc = None
if "post_proc" not in st.session_state:
    st.session_state.post_proc = None
if "schema_proc" not in st.session_state:
    st.session_state.schema_proc = None
if "cadastral_proc" not in st.session_state:
    st.session_state.cadastral_proc = None
if "cleanup_proc" not in st.session_state:
    st.session_state.cleanup_proc = None
if "publish_drafts_proc" not in st.session_state:
    st.session_state.publish_drafts_proc = None
if "drafts_count" not in st.session_state:
    st.session_state.drafts_count = None
if "headless" not in st.session_state:
    st.session_state.headless = False
if "log_level" not in st.session_state:
    st.session_state.log_level = "INFO"


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

st.divider()

# ── Основний layout ───────────────────────────────────────────────────

left, right = st.columns([1, 2], gap="large")

# ── Статистика ────────────────────────────────────────────────────────

with left:
    st.subheader("Налаштування")
    with st.container(border=True):
        st.session_state.headless = st.toggle(
            "Headless (браузер без UI)",
            value=st.session_state.headless,
            help="Увімкнено — браузер не відображається. Вимкніть для відлагодження.",
        )
        st.session_state.log_level = st.selectbox(
            "Рівень логування",
            options=LOG_LEVELS,
            index=LOG_LEVELS.index(st.session_state.log_level),
        )

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

    # Видалення об'єктів із БД за вибраними статусами
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
            "Я підтверджую видалення",
            value=False,
            key="confirm_del_status",
            disabled=del_count == 0,
        )
        if st.button(
            "🗑 Видалити вибрані",
            use_container_width=True,
            disabled=not confirm_del or del_count == 0,
        ):
            try:
                with OfferDB() as db:
                    deleted = db.delete_by_statuses(del_statuses)
                st.toast(f"Видалено {deleted} записів", icon="🗑")
                st.rerun()
            except Exception as e:
                st.error(f"Помилка видалення: {e}")

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
            st.session_state.collect_proc = launch(
                build_collect_cmd(
                    max_pages or None,
                    max_count_c or None,
                    headless=st.session_state.headless,
                    property_type=collect_property_type if collect_property_type != "Всі" else None,
                    deal_type=collect_deal_type if collect_deal_type != "Всі" else None,
                ),
                log_level=st.session_state.log_level,
            )
            st.toast("Збір запущено!", icon="▶")

        if proc_is_running(st.session_state.collect_proc):
            _col_info, _col_stop = st.columns([3, 1])
            with _col_info:
                st.info("⏳ Збір виконується...")
            with _col_stop:
                if st.button("⏹ Зупинити", key="stop_collect", use_container_width=True):
                    stop_proc(st.session_state.collect_proc)
                    st.toast("Збір зупинено", icon="⏹")
                    st.rerun()
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
                help="Безкоштовне = Будинок + Комерційна + Ділянка + Паркомісце",
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
            st.session_state.post_proc = launch(
                build_post_cmd(
                    publish,
                    max_count_p or None,
                    headless=st.session_state.headless,
                    property_type=post_property_type if post_property_type != "Всі" else None,
                    deal_type=post_deal_type if post_deal_type != "Всі" else None,
                ),
                log_level=st.session_state.log_level,
            )
            st.toast("Публікацію запущено!", icon="▶")

        if proc_is_running(st.session_state.post_proc):
            _col_info, _col_stop = st.columns([3, 1])
            with _col_info:
                st.info("⏳ Публікація виконується...")
            with _col_stop:
                if st.button("⏹ Зупинити", key="stop_post", use_container_width=True):
                    stop_proc(st.session_state.post_proc)
                    st.toast("Публікацію зупинено", icon="⏹")
                    st.rerun()
        elif st.session_state.post_proc is not None:
            rc = st.session_state.post_proc.returncode
            if rc == 0:
                st.success("✅ Публікацію завершено")
            else:
                st.error(f"❌ Публікацію завершено з кодом {rc}")

    # Кадастрові номери
    with st.container(border=True):
        cad1, cad2 = st.columns([3, 1])
        with cad1:
            st.markdown("**Кадастрові номери** (БД → zem.center)")
            st.caption("Шукає кадастровий номер для об'єктів без нього (Будинок, Ділянка, Комерційна).")
        with cad2:
            max_count_cad = st.number_input(
                "Макс.",
                min_value=0,
                value=0,
                key="max_count_cadastral",
                help="0 = без обмежень",
            )
        cadastral_btn = st.button(
            "🗺 Знайти кадастрові номери",
            use_container_width=True,
            disabled=proc_is_running(st.session_state.cadastral_proc),
        )

        if cadastral_btn:
            cmd = ["uv", "run", "python", "main.py", "cadastral"]
            if max_count_cad:
                cmd += ["--max-count", str(max_count_cad)]
            st.session_state.cadastral_proc = launch(cmd, log_level=st.session_state.log_level)
            st.toast("Пошук кадастрових номерів запущено!", icon="🗺")

        if proc_is_running(st.session_state.cadastral_proc):
            _col_info, _col_stop = st.columns([3, 1])
            with _col_info:
                st.info("⏳ Пошук кадастрових номерів виконується...")
            with _col_stop:
                if st.button("⏹ Зупинити", key="stop_cadastral", use_container_width=True):
                    stop_proc(st.session_state.cadastral_proc)
                    st.toast("Пошук зупинено", icon="⏹")
                    st.rerun()
        elif st.session_state.cadastral_proc is not None:
            rc = st.session_state.cadastral_proc.returncode
            if rc == 0:
                st.success("✅ Пошук завершено")
            else:
                st.error(f"❌ Пошук завершився з кодом {rc}")

    # Очистка сміття на rieltor.ua
    with st.container(border=True):
        st.markdown("**🗑 Очистити сміття на rieltor.ua**")
        st.caption(
            "Видаляє ВСІ об'єкти із «Закритої бази», потім остаточно чистить «Видалені». "
            "Неудачні/неправильні чернетки. Незворотно!"
        )
        tc1, tc2 = st.columns([3, 1])
        with tc1:
            confirm_cleanup = st.checkbox(
                "Я підтверджую видалення",
                value=False,
                key="confirm_cleanup",
            )
        with tc2:
            max_count_clean = st.number_input(
                "Макс.",
                min_value=0,
                value=0,
                key="max_count_cleanup",
                help="0 = без обмежень",
            )
        cleanup_btn = st.button(
            "🗑 Очистити «Закриту базу»",
            use_container_width=True,
            disabled=not confirm_cleanup or proc_is_running(st.session_state.cleanup_proc),
        )

        if cleanup_btn:
            cmd = ["uv", "run", "python", "main.py", "clean-trash"]
            if max_count_clean:
                cmd += ["--max-count", str(max_count_clean)]
            st.session_state.cleanup_proc = launch(cmd, log_level=st.session_state.log_level)
            st.toast("Очистку «Закритої бази» запущено!", icon="🗑")

        if proc_is_running(st.session_state.cleanup_proc):
            _col_info, _col_stop = st.columns([3, 1])
            with _col_info:
                st.info("⏳ Очистка виконується...")
            with _col_stop:
                if st.button("⏹ Зупинити", key="stop_cleanup", use_container_width=True):
                    stop_proc(st.session_state.cleanup_proc)
                    st.toast("Очистку зупинено", icon="⏹")
                    st.rerun()
        elif st.session_state.cleanup_proc is not None:
            rc = st.session_state.cleanup_proc.returncode
            if rc == 0:
                st.success("✅ Очистку завершено")
            else:
                st.error(f"❌ Очистка завершилась з кодом {rc}")

    # Масова публікація чернеток
    with st.container(border=True):
        st.markdown("**📤 Опублікувати чернетки** (rieltor.ua)")
        st.caption("Крок 1 — перевірити кількість. Крок 2 — обрати скільки/за який період і опублікувати.")

        # Крок 1 — перевірити
        if st.button(
            "🔍 Перевірити чернетки",
            use_container_width=True,
            disabled=proc_is_running(st.session_state.publish_drafts_proc),
        ):
            proc = launch(
                ["uv", "run", "python", "main.py", "publish-drafts", "--count-only"]
                + (["--headless"] if st.session_state.headless else []),
                log_level=st.session_state.log_level,
            )
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

        # Крок 2 — параметри + публікація (лише коли є чернетки)
        if n:
            pd1, pd2 = st.columns(2)
            with pd1:
                pub_max = st.number_input(
                    "Скільки публікувати", min_value=0, value=0,
                    key="pub_drafts_max", help="0 = всі",
                )
            with pd2:
                pub_delay = st.number_input(
                    "Затримка, с", min_value=0.0, value=3.0, step=0.5,
                    key="pub_drafts_delay",
                )
            dd1, dd2 = st.columns(2)
            with dd1:
                pub_from = st.date_input("Дата з", value=None, key="pub_drafts_from")
            with dd2:
                pub_to = st.date_input("Дата по", value=None, key="pub_drafts_to")
            confirm_pub = st.checkbox(
                "Я підтверджую публікацію", value=False, key="confirm_pub_drafts",
            )

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
                if st.session_state.headless:
                    cmd += ["--headless"]
                st.session_state.publish_drafts_proc = launch(cmd, log_level=st.session_state.log_level)
                st.toast("Публікацію чернеток запущено!", icon="📤")
                st.rerun()
        elif n == 0:
            st.caption("Чернеток немає — публікувати нічого.")

        # Статус процесу публікації
        if proc_is_running(st.session_state.publish_drafts_proc):
            _ci, _cs = st.columns([3, 1])
            with _ci:
                st.info("⏳ Публікація чернеток виконується...")
            with _cs:
                if st.button("⏹ Зупинити", key="stop_publish_drafts", use_container_width=True):
                    stop_proc(st.session_state.publish_drafts_proc)
                    st.toast("Публікацію зупинено", icon="⏹")
                    st.rerun()
        elif st.session_state.publish_drafts_proc is not None:
            rc = st.session_state.publish_drafts_proc.returncode
            if rc == 0:
                st.success("✅ Публікацію чернеток завершено")
            else:
                st.error(f"❌ Публікація завершилась з кодом {rc}")

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
            st.session_state.schema_proc = launch(
                ["uv", "run", "python", "rieltor_handler/run_schema_collection.py"],
                log_level=st.session_state.log_level,
            )
            st.toast("Збір схем запущено!", icon="🔄")

        if proc_is_running(st.session_state.schema_proc):
            _col_info, _col_stop = st.columns([3, 1])
            with _col_info:
                st.info("⏳ Збір схем виконується (це може зайняти кілька хвилин)...")
            with _col_stop:
                if st.button("⏹ Зупинити", key="stop_schema", use_container_width=True):
                    stop_proc(st.session_state.schema_proc)
                    st.toast("Збір схем зупинено", icon="⏹")
                    st.rerun()
        elif st.session_state.schema_proc is not None:
            rc = st.session_state.schema_proc.returncode
            if rc == 0:
                st.success("✅ Схеми оновлено")
            else:
                st.error(f"❌ Збір схем завершився з кодом {rc}")

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
