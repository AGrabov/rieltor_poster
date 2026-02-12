from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from setup_logger import init_logging
logger = init_logging(level=str(os.environ.get("LOG_LEVEL", "DEBUG")).upper(), filename="logs/schema_collector.log")


from .schema_collector.collector import OfferCreateSchemaCollector
from .schema_collector.helpers import (_slug, _key4, _sig3)

from .rieltor_session import RieltorCredentials, RieltorSession



def _attach_field_keys(schema: Dict[str, Any]) -> None:
    for f in schema.get("fields") or []:
        meta = f.get("meta") or {}
        meta.setdefault("field_key", _key4(f.get("nav", ""), f.get("section", ""), f.get("label", ""), f.get("widget", "")))
        meta.setdefault("sig", _sig3(f.get("section", ""), f.get("label", ""), f.get("widget", "")))
        f["meta"] = meta


def _inject_conditionals_into_meta(schema: Dict[str, Any], cond: List[Dict[str, Any]]) -> None:
    """Put visible_when rules into meta of affected fields, and ensure conditional-only fields exist in schema."""
    fields = schema.get("fields") or []

    # index by sig
    by_sig: Dict[str, Dict[str, Any]] = {}
    for f in fields:
        meta = f.get("meta") or {}
        sig = meta.get("sig") or _sig3(f.get("section", ""), f.get("label", ""), f.get("widget", ""))
        by_sig[sig] = f

    # ensure added fields exist
    added_inserted = 0
    for g in (cond or []):
        nav = g.get("nav") or ""
        for opt in (g.get("options") or []):
            if opt.get("select_failed"):
                continue
            for a in (opt.get("added") or []):
                sec = a.get("section") or g.get("section") or nav
                lab = a.get("label") or ""
                wid = a.get("widget") or ""
                if not lab or not wid:
                    continue
                sig = _sig3(sec, lab, wid)
                if sig in by_sig:
                    continue
                new_f = {
                    "nav": nav,
                    "section": sec or nav,
                    "label": lab,
                    "widget": wid,
                    "required": False,
                    "options": list(a.get("options") or []),
                    "meta": {
                        "field_key": _key4(nav, sec, lab, wid),
                        "sig": sig,
                        "inferred_from": "radio_probe_added",
                    },
                }
                fields.append(new_f)
                by_sig[sig] = new_f
                added_inserted += 1

    if added_inserted:
        logger.info("Injected conditional-only fields into schema: %d", added_inserted)

    # build visible_when rules
    merged_rules = 0
    for g in (cond or []):
        controller = {
            "nav": g.get("nav", ""),
            "section": g.get("section", ""),
            "label": g.get("label", ""),
            "widget": g.get("widget", "radio") or "radio",
            "field_key": g.get("controller_field_key") or _key4(g.get("nav", ""), g.get("section", ""), g.get("label", ""), "radio"),
            "ord": g.get("controller_ord"),
        }
        for opt in (g.get("options") or []):
            if opt.get("select_failed"):
                continue
            val = opt.get("value")
            for a in (opt.get("added") or []):
                sec = a.get("section") or g.get("section") or ""
                lab = a.get("label") or ""
                wid = a.get("widget") or ""
                if not sec or not lab or not wid:
                    continue
                sig = _sig3(sec, lab, wid)
                f = by_sig.get(sig)
                if not f:
                    continue
                meta = f.get("meta") or {}
                vw = meta.get("visible_when") or []
                rule = {"controller": controller, "value": val, "source": "radio_probe"}
                js = json.dumps(rule, ensure_ascii=False, sort_keys=True)
                seen = set(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in vw)
                if js not in seen:
                    vw.append(rule)
                    meta["visible_when"] = vw
                    f["meta"] = meta
                    merged_rules += 1

    logger.info("Merged visible_when rules: %d", merged_rules)


def run_collection(
    *,
    phone: str,
    password: str,
    property_types: List[str] | str | None = None,
    deal_types: List[str] | str | None = None,
    headless: bool = False,
    slow_mo_ms: int = 0,
    out_path: str = "models/schema_dump.json",
    ui_delay_ms: int = 350,
    radio_follow_window: int = 4,
    enable_radio_probe: bool = True,
    seed_address_city: str = "Київ",
    discovery_rounds: int = 3,
    smoke_fill: bool = True,
    debug: bool = False,
) -> str:
    if debug:
        logger.setLevel("DEBUG")

    creds = RieltorCredentials(phone=phone, password=password)

    if isinstance(property_types, str):
        property_types = [property_types]

    if not property_types:
        property_types = [
            "Квартира",
            "Кімната",
            "Будинок",
            "Комерційна",
            "Ділянка",
            "Паркомісце",
        ]

    # Parse deal types
    if isinstance(deal_types, str):
        deal_types = [deal_types]

    if not deal_types:
        deal_types = ["sell", "lease"]

    # Normalize deal types to folder names
    deal_type_folders = {
        "sell": "sell",
        "продаж": "sell",
        "lease": "lease",
        "rent": "lease",
        "оренда": "lease",
    }

    out_path_p = Path(out_path)
    if out_path_p.suffix.lower() == ".json":
        base_out_dir = out_path_p.parent / out_path_p.stem
        combined_path = out_path_p
    else:
        base_out_dir = out_path_p
        combined_path = base_out_dir / "schema_dump.json"

    base_out_dir.mkdir(parents=True, exist_ok=True)

    dump: Dict[str, Any] = {}

    with RieltorSession(creds=creds, headless=headless, slow_mo_ms=slow_mo_ms, debug=debug) as sess:
        logger.info("Login")
        sess.login()
        page = sess.page
        if page is None:
            raise RuntimeError("No page")

        collector = OfferCreateSchemaCollector(
            page,
            ui_delay_ms=ui_delay_ms,
            radio_follow_window=radio_follow_window,
            enable_radio_probe=enable_radio_probe,
            debug=debug,
        )
        collector.open()
        collector.open_all_blocks_sticky()

        for deal_type in deal_types:
            # Get folder name for this deal type
            folder_name = deal_type_folders.get(deal_type.lower(), deal_type.lower())
            deal_type_ui = "Продаж" if folder_name == "sell" else "Оренда"

            logger.info("========== DEAL TYPE: %s (%s) ==========", deal_type_ui, folder_name)

            # Create subfolder for this deal type
            out_dir = base_out_dir / folder_name
            out_dir.mkdir(parents=True, exist_ok=True)

            # Select deal type
            collector.select_deal_type(deal_type_ui)

            dump[folder_name] = {}

            def collect_and_save_schema(
                property_type: str,
                subtype: str | None = None,
                subtype_ui: str | None = None,
            ) -> Dict[str, Any]:
                """Collect schema for a property type (and optional subtype) and save it."""
                # discovery: seed address + optional smoke fill until schema stops growing
                schema = collector.discover_schema_until_stable(
                    seed_address_city=seed_address_city,
                    max_rounds=int(discovery_rounds),
                    smoke_fill=bool(smoke_fill),
                )

                # after discovery: collect clean schema again
                collector.open_all_blocks_sticky()
                schema = collector.collect_schema_dynamic_h6()
                _attach_field_keys(schema)

                cond: List[Dict[str, Any]] = []
                if enable_radio_probe:
                    try:
                        cond = collector.probe_radios_dynamic()
                    except Exception as e:
                        logger.warning("Radio probe failed: %s", e)

                # merge conditionals into meta (visible_when + add missing dynamic fields)
                if cond:
                    _inject_conditionals_into_meta(schema, cond)

                payload = {
                    "url": page.url,
                    "deal_type": deal_type_ui,
                    "property_type": property_type,
                    "subtype": subtype_ui,
                    "ui_delay_ms": ui_delay_ms,
                    "navigation": schema.get("navigation") or [],
                    "fields": schema.get("fields") or [],
                    "conditionals": {
                        "radio_dynamic": cond,
                    },
                }

                # Determine filename
                if subtype:
                    filename = f"{_slug(property_type)}_{_slug(subtype)}.json"
                else:
                    filename = f"{_slug(property_type)}.json"

                pt_path = out_dir / filename
                pt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Saved schema: %s/%s -> %s", folder_name, filename, pt_path)

                return payload

            for pt in property_types:
                logger.info("=== PROPERTY TYPE: %s (deal: %s) ===", pt, deal_type_ui)
                collector.select_property_type(pt)

                # Special handling for "Паркомісце" - has subtypes "Гараж" and "Паркомісце"
                if _slug(pt).casefold() == "паркомісце":
                    parking_subtypes = [
                        ("garage", "Гараж"),
                        ("parking", "Паркомісце"),
                    ]
                    for subtype_key, subtype_ui in parking_subtypes:
                        logger.info("--- PARKING SUBTYPE: %s ---", subtype_ui)
                        collector.select_parking_type(subtype_ui)

                        payload = collect_and_save_schema(pt, subtype_key, subtype_ui)
                        dump[folder_name][f"{pt}_{subtype_ui}"] = payload
                        page.wait_for_timeout(ui_delay_ms)
                else:
                    payload = collect_and_save_schema(pt)
                    dump[folder_name][pt] = payload
                    page.wait_for_timeout(ui_delay_ms)

    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved combined schema dump: %s", combined_path)
    logger.info("Per-type schemas directory: %s", base_out_dir)
    return str(combined_path)


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    phone = (os.getenv("PHONE") or "").strip()
    password = (os.getenv("PASSWORD") or "").strip()
    if not phone or not password:
        raise SystemExit("Set PHONE and PASSWORD in env (.env supported)")

    out = run_collection(
        phone=phone,
        password=password,
        property_types=[
            "Квартира",
            # "Кімната",
            # "Будинок",
            # "Комерційна",
            # "Ділянка",
            # # "Паркомісце"
        ],
        deal_types=["sell", "lease"],  # Продаж and Оренда
        headless=False,
        slow_mo_ms=0,
        out_path="schemas/schema_dump.json",
        ui_delay_ms=350,
        radio_follow_window=3,
        enable_radio_probe=True,
        seed_address_city="Київ",
        discovery_rounds=2,
        smoke_fill=True,
        debug=True,
    )
    print(f"Saved: {out}")
