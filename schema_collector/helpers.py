from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List


def _norm(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split()).strip()


def _cf(s: str) -> str:
    return _norm(s).casefold()


def _xpath_literal(s: str) -> str:
    s = "" if s is None else str(s)
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", ".join(
        [f"'{p}'" if i == len(parts) - 1 else f"'{p}', \"'\"" for i, p in enumerate(parts)]
    ) + ")"


def _key4(nav: str, section: str, label: str, widget: str) -> str:
    """Stable string key for matching fields across runs."""
    return "||".join([_cf(nav), _cf(section), _cf(label), _cf(widget)])


def _sig3(section: str, label: str, widget: str) -> str:
    """Dedupe key independent of nav."""
    return "||".join([_cf(section), _cf(label), _cf(widget)])


def _slug(s: str) -> str:
    """Filesystem-safe slug (keeps UA/CYR letters)."""
    s = _norm(s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-zА-Яа-яІіЇїЄєҐґ_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "item"


@dataclass
class FieldInfo:
    nav: str
    section: str
    label: str
    widget: str
    required: bool
    options: List[str]
    meta: Dict[str, Any]