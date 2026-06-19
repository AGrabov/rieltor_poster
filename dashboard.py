"""Rieltor Automation Dashboard.

Запуск:
    uv run streamlit run dashboard.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import deque
from html import escape
from pathlib import Path

import streamlit as st
from streamlit.components.v1 import html as components_html

from main import read_drafts_count
from offer_db import OfferDB
from offer_edit import ADDRESS_FORM_FIELDS, merge_offer_edits

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

# Лог-консоль: рівні логування і поріг фільтра за серйозністю.
LEVEL_RE = re.compile(r"-(DEBUG|INFO|WARNING|ERROR|CRITICAL)-")
LEVEL_SEVERITY = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
LEVEL_COLORS = {
    "DEBUG": "#7A808F",
    "INFO": "#D7DAE3",
    "WARNING": "#F5A524",
    "ERROR": "#FF6B6B",
    "CRITICAL": "#FF6B6B",
}
LOG_FILTERS = {"Всі": 0, "INFO": 20, "WARN": 30, "ERROR": 40}

st.set_page_config(
    page_title="Rieltor Dashboard",
    page_icon="🏠",
    layout="wide",
)


# ── Стилі (залежать від активної теми) ────────────────────────────────

# Палітра тонкого «дотюнінгу» поверх нативної теми Streamlit.
# Сама тема (темна/світла) перемикається у .streamlit/config.toml → base.
THEME_PALETTES = {
    "dark": {
        "card_bg": "#171A23", "card_border": "#262A36",
        "title": "#E6E8EE", "subtitle": "#9097A8",
        "accent": "#7C83FF", "accent2": "#5A57E6",
        "shadow": "0 1px 2px rgba(0,0,0,.35)", "hover": "0 6px 16px rgba(124,131,255,.28)",
    },
    "light": {
        "card_bg": "#FFFFFF", "card_border": "#E4E6EE",
        "title": "#1E2330", "subtitle": "#6B7180",
        "accent": "#4F46E5", "accent2": "#8B83FF",
        "shadow": "0 1px 2px rgba(16,22,40,.04)", "hover": "0 4px 12px rgba(79,70,229,.16)",
    },
}


def active_theme() -> str:
    """Визначити активну тему ('dark'/'light') з config.toml → theme.base."""
    try:
        base = str(st.get_option("theme.base") or "").lower()
    except Exception:
        base = ""
    return "light" if "light" in base else "dark"


def inject_css(theme: str) -> None:
    p = THEME_PALETTES.get(theme, THEME_PALETTES["dark"])
    st.markdown(
        f"""
        <style>
          [data-testid="stMainBlockContainer"] {{ padding-top: 2.2rem; padding-bottom: 3rem; }}

          .app-brand {{ display: flex; align-items: center; gap: .75rem; }}
          .app-logo {{ font-size: 2.1rem; line-height: 1; }}
          .app-title {{ font-size: 1.65rem; font-weight: 800; letter-spacing: -.02em;
            color: {p["title"]}; line-height: 1.1; }}
          .app-subtitle {{ font-size: .85rem; color: {p["subtitle"]}; margin-top: 2px; }}
          .app-accent {{ height: 3px; border-radius: 3px; margin: .55rem 0 .2rem;
            background: linear-gradient(90deg, {p["accent"]}, {p["accent2"]} 50%, transparent 100%); }}

          [data-testid="stMetric"] {{
            background: {p["card_bg"]}; border: 1px solid {p["card_border"]}; border-radius: .8rem;
            padding: .85rem 1rem; box-shadow: {p["shadow"]}; }}
          [data-testid="stMetricValue"] {{ font-weight: 700; }}

          .stButton > button {{ border-radius: .6rem; font-weight: 600;
            transition: transform .06s ease, box-shadow .15s ease; }}
          .stButton > button:hover {{ transform: translateY(-1px); box-shadow: {p["hover"]}; }}

          [data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: .85rem; }}
          [data-testid="stTabs"] button[role="tab"] {{ font-weight: 600; }}

          [data-testid="stProgress"] > div > div > div {{
            background-image: linear-gradient(90deg, {p["accent"]}, {p["accent2"]}); }}
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_css(active_theme())


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
        return ""
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
            if st.button("⏹ Зупинити", key=f"stop_{proc_key}", width='stretch'):
                stop_proc(proc)
                st.toast("Зупинено", icon="⏹")
                st.rerun()
    elif proc is not None:
        rc = proc.returncode
        if rc == 0:
            st.success(f"✅ {done_msg}")
        else:
            st.error(f"❌ Завершилось з кодом {rc}")


def _fmt_errors(errors) -> str:
    """Стиснути список помилок у короткий рядок для таблиці."""
    if not errors:
        return ""
    if isinstance(errors, list):
        parts = []
        for e in errors:
            if isinstance(e, dict):
                parts.append(str(e.get("message") or e.get("error") or json.dumps(e, ensure_ascii=False)))
            else:
                parts.append(str(e))
        return " | ".join(parts)
    return str(errors)


EDITABLE_STATUSES = ("failed", "skipped")


def _editor_label(r) -> str:
    art = r.article or f"ID {r.estate_id}"
    title = (r.title or "").strip()
    if len(title) > 50:
        title = title[:50] + "…"
    return f"#{art} — {title} ({STATUS_LABELS.get(r.status, r.status)})"


def render_offer_editor(records: list) -> None:
    """Форма ручного виправлення помилкових об'єктів (failed/skipped).

    Поля адреси редагуються формою, решта — сирим JSON. Збереження повертає
    об'єкт у чергу (статус → new), щоб наступний запуск Фази 2 спробував знову.
    """
    editable = {r.estate_id: r for r in records if r.status in EDITABLE_STATUSES}

    st.divider()
    st.markdown("**✏ Ручне виправлення** (failed / skipped)")
    if not editable:
        st.caption("Серед показаних об'єктів немає помилкових (failed/skipped) для редагування.")
        return
    st.caption(
        "Виправте поля об'єкта, який бот не зміг опублікувати — збереження поверне його "
        "в чергу (статус → new). Авто-оновлення на час редагування призупиняється автоматично."
    )

    # Якщо раніше вибраний об'єкт зник із поточного фільтра — скидаємо вибір,
    # інакше selectbox отримає значення поза options.
    if st.session_state.get("edit_select") not in editable:
        st.session_state["edit_select"] = None

    sel = st.selectbox(
        "Об'єкт для редагування",
        options=[None, *editable.keys()],
        format_func=lambda eid: "— оберіть —" if eid is None else _editor_label(editable[eid]),
        key="edit_select",
    )
    if sel is None:
        return

    rec = editable[sel]
    with st.container(border=True):
        if rec.errors:
            st.error(f"Помилки бота: {_fmt_errors(rec.errors)}")

        ed_title = st.text_input("Заголовок", value=rec.title or "", key=f"edit_title_{sel}")

        addr = rec.offer_data.get("address") or {}
        address_edits: dict[str, str] = {}
        with st.expander("Адреса", expanded=True):
            for label in ADDRESS_FORM_FIELDS:
                address_edits[label] = st.text_input(
                    label, value=str(addr.get(label) or ""), key=f"edit_addr_{label}_{sel}"
                )

        with st.expander("Сирий JSON (решта полів)", expanded=False):
            raw_json = st.text_area(
                "offer_data (JSON)",
                value=json.dumps(rec.offer_data, ensure_ascii=False, indent=2),
                height=320,
                key=f"edit_json_{sel}",
            )

        if st.button(
            "💾 Зберегти і повернути в чергу (→ new)",
            width='stretch',
            key=f"edit_save_{sel}",
        ):
            try:
                merged = merge_offer_edits(raw_json, address_edits)
            except ValueError as e:
                st.error(f"❌ {e}")
                return
            try:
                with OfferDB() as db:
                    db.edit_offer(sel, offer_data=merged, title=ed_title.strip() or None, status="new")
            except Exception as e:
                st.error(f"Помилка збереження: {e}")
                return
            # Скидаємо стан віджетів редактора — об'єкт зник зі списку (став 'new').
            for k in [key for key in st.session_state if key.endswith(f"_{sel}") or key == "edit_select"]:
                del st.session_state[k]
            st.toast("Збережено, повернуто в чергу", icon="💾")
            st.rerun()


def render_log_console(text: str, level_filter: str, search: str) -> None:
    """Кольорова прокручувана консоль логів з авто-прокруткою донизу."""
    threshold = LOG_FILTERS.get(level_filter, 0)
    needle = search.lower().strip() if search else ""
    current = "INFO"  # рівень за замовчуванням для рядків без мітки (продовження трейсбеків)
    rows: list[str] = []
    for line in text.splitlines():
        m = LEVEL_RE.search(line)
        if m:
            current = m.group(1)
        sev = LEVEL_SEVERITY.get(current, 20)
        if sev < threshold:
            continue
        if needle and needle not in line.lower():
            continue
        color = LEVEL_COLORS.get(current, "#AEB3BF")
        rows.append(f'<div class="ln" style="color:{color}">{escape(line) or "&nbsp;"}</div>')

    body = "".join(rows) or '<div class="ln empty">— порожньо —</div>'
    doc = f"""
    <div id="box">{body}</div>
    <style>
      html, body {{ margin: 0; }}
      #box {{
        height: 372px; overflow-y: auto; background: #15171E; border-radius: 10px;
        padding: 12px 14px; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12.5px;
        line-height: 1.55; color: #AEB3BF; border: 1px solid #2A2E3A;
      }}
      #box .ln {{ white-space: pre-wrap; word-break: break-word; }}
      #box .empty {{ color: #6B7180; font-style: italic; }}
      #box::-webkit-scrollbar {{ width: 10px; }}
      #box::-webkit-scrollbar-thumb {{ background: #3A3F4D; border-radius: 5px; }}
    </style>
    <script>
      const box = document.getElementById('box');
      if (box) box.scrollTop = box.scrollHeight;
    </script>
    """
    components_html(doc, height=392, scrolling=False)


# ── Session state ─────────────────────────────────────────────────────

for _k in ("collect_proc", "post_proc", "schema_proc", "cadastral_proc", "cleanup_proc", "publish_drafts_proc"):
    st.session_state.setdefault(_k, None)
st.session_state.setdefault("drafts_count", None)


# ── Заголовок ─────────────────────────────────────────────────────────

header_left, header_right = st.columns([3, 1])
with header_left:
    st.markdown(
        """
        <div class="app-brand">
          <span class="app-logo">🏠</span>
          <div>
            <div class="app-title">Rieltor Dashboard</div>
            <div class="app-subtitle">Автоматизація публікації оголошень · CRM → Rieltor.ua</div>
          </div>
        </div>
        <div class="app-accent"></div>
        """,
        unsafe_allow_html=True,
    )
with header_right:
    st.write("")
    col_refresh, col_auto = st.columns(2)
    with col_refresh:
        manual_refresh = st.button("⟳ Оновити", width='stretch')
    with col_auto:
        auto_refresh = st.toggle(f"Авто {AUTO_REFRESH_SEC}с", value=True)


# ── Статистика (завжди видима) ────────────────────────────────────────

summary = get_summary()
total = sum(summary.values())

stat_cols = st.columns([2, 2, 2, 2, 1])
for col, status in zip(stat_cols[:4], STATUSES):
    col.metric(label=STATUS_LABELS[status], value=summary[status], border=True)
with stat_cols[4]:
    st.caption("Кадастр")
    if st.button(
        "🗺",
        key="cadastral_btn",
        help="Знайти кадастрові номери для всіх об'єктів без нього "
        "(Будинок, Ділянка, Комерційна). Фоновий процес, без налаштувань.",
        width='stretch',
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

tab_main, tab_objects, tab_service, tab_db = st.tabs(
    ["📋 Основне", "📂 Об'єкти", "🛠 Сервіс", "🗄 База / ⚠ Небезпечна зона"]
)

# ── Вкладка: Основне (головний потік) ─────────────────────────────────
with tab_main:
    col_collect, col_post = st.columns(2)

    # Фаза 1 — Збір
    with col_collect, st.container(border=True):
        st.markdown("**Фаза 1 — Збір** (CRM → БД)")
        with st.popover("⚙ Параметри", width='stretch'):
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
        if st.button("▶ Зібрати", width='stretch', disabled=proc_is_running(st.session_state.collect_proc)):
            st.session_state.collect_proc = launch(
                build_collect_cmd(collect_max or None, _opt(collect_pt), _opt(collect_dt))
            )
            st.toast("Збір запущено!", icon="▶")
            st.rerun()
        render_proc_status("collect_proc", "Збір виконується...", "Збір завершено")

    # Фаза 2 — Публікація
    with col_post, st.container(border=True):
        st.markdown("**Фаза 2 — Публікація** (БД → Rieltor.ua)")
        with st.popover("⚙ Параметри", width='stretch'):
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
        if st.button(post_label, width='stretch', disabled=proc_is_running(st.session_state.post_proc)):
            st.session_state.post_proc = launch(build_post_cmd(post_publish, post_max or None, _opt(post_pt), _opt(post_dt)))
            st.toast("Публікацію запущено!", icon="▶")
            st.rerun()
        render_proc_status("post_proc", "Публікація виконується...", "Публікацію завершено")


# ── Вкладка: Об'єкти (перегляд БД) ────────────────────────────────────
with tab_objects:
    f1, f2, f3, f4 = st.columns([3, 2, 2, 1])
    with f1:
        obj_statuses = st.multiselect(
            "Статус",
            options=STATUSES,
            default=[],
            format_func=lambda s: STATUS_LABELS.get(s, s),
            key="obj_statuses",
            placeholder="Всі статуси",
        )
    with f2:
        obj_pt = st.selectbox("Тип об'єкта", options=PROP_OPTIONS, index=0, key="obj_pt")
    with f3:
        obj_search = st.text_input("Пошук", key="obj_search", placeholder="артикул / заголовок / ID")
    with f4:
        obj_limit = st.number_input("Ліміт", min_value=10, max_value=2000, value=200, step=50, key="obj_limit")

    try:
        with OfferDB() as db:
            records = db.list_offers(
                statuses=obj_statuses or None,
                property_type=_opt(obj_pt),
                search=obj_search or None,
                limit=int(obj_limit),
            )
    except Exception as e:
        records = []
        st.error(f"Помилка читання БД: {e}")

    if not records:
        st.info("Нічого не знайдено за заданими фільтрами.")
    else:
        st.caption(f"Показано об'єктів: **{len(records)}**")
        table = [
            {
                "ID": r.estate_id,
                "Артикул": r.article or "",
                "Заголовок": r.title or "",
                "Тип": r.property_type or "",
                "Угода": r.deal_type or "",
                "Статус": STATUS_LABELS.get(r.status, r.status),
                "Створено": r.created_at,
                "Оновлено": r.updated_at,
                "Rieltor ID": r.rieltor_offer_id or "",
                "Помилки": _fmt_errors(r.errors),
            }
            for r in records
        ]
        st.dataframe(
            table,
            width='stretch',
            hide_index=True,
            column_config={
                "ID": st.column_config.NumberColumn(width="small", format="%d"),
                "Заголовок": st.column_config.TextColumn(width="large"),
                "Помилки": st.column_config.TextColumn(width="medium"),
            },
        )

        # ── Ручне виправлення помилкових об'єктів (failed / skipped) ──
        # Захист: помилка в редакторі не повинна «гасити» всю вкладку з таблицею.
        try:
            render_offer_editor(records)
        except Exception as e:  # noqa: BLE001
            st.error(f"Редактор недоступний: {e}")


# ── Вкладка: Сервіс (рідкі/допоміжні дії) ─────────────────────────────
with tab_service:
    # Масова публікація чернеток
    with st.container(border=True):
        st.markdown("**📤 Опублікувати чернетки** (rieltor.ua)")
        st.caption("Крок 1 — перевірити кількість. Крок 2 — обрати скільки/за який період і опублікувати.")

        if st.button(
            "🔍 Перевірити чернетки",
            width='stretch',
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
                width='stretch',
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
                "🔄 Оновити схеми", width='stretch', disabled=proc_is_running(st.session_state.schema_proc)
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
        if st.button("🗑 Видалити вибрані", width='stretch', disabled=not confirm_del or del_count == 0):
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
            width='stretch',
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
lh_title, lh_btn, _lh_spacer = st.columns([1.3, 1, 10], vertical_alignment="center")
with lh_title:
    st.subheader("Логи")
with lh_btn:
    if st.button("🗑", key="clear_logs", help="Очистити лог-файл"):
        try:
            LOG_FILE.write_text("", encoding="utf-8")
            st.toast("Логи очищено", icon="🗑")
        except Exception as e:
            st.error(f"Помилка очищення: {e}")

lc1, lc2, lc3 = st.columns([2, 3, 1])
with lc1:
    log_level = st.segmented_control(
        "Рівень", options=list(LOG_FILTERS.keys()), default="Всі", key="log_level", label_visibility="collapsed"
    )
with lc2:
    log_search = st.text_input("Пошук у логах", key="log_search", placeholder="🔍 пошук у логах", label_visibility="collapsed")
with lc3:
    log_lines = st.number_input(
        "Рядків", min_value=20, max_value=500, value=LOG_TAIL, step=50, label_visibility="collapsed"
    )

log_text = read_log_tail(int(log_lines))
if not log_text.strip():
    st.caption("_Лог-файл порожній або не знайдено._")
else:
    render_log_console(log_text, log_level or "Всі", log_search)

# ── Автооновлення ─────────────────────────────────────────────────────

# Під час редагування об'єкта авто-оновлення вимикаємо: блокуючий sleep+rerun
# інакше «морозить» форму (кожна взаємодія впирається в паузу). Ручне «Оновити»
# працює завжди.
editing = st.session_state.get("edit_select") is not None
if manual_refresh:
    st.rerun()
elif auto_refresh and not editing:
    time.sleep(AUTO_REFRESH_SEC)
    st.rerun()
