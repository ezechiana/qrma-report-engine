# app/services/config_service.py

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "app" / "config" / "practitioner_config.json"


DEFAULT_CONFIG = {
    "brand_name": "NaturalApproaches.com",
    "report_title": "Personalised Wellness Scan Report",
    "subtitle": "Wellness screening summary",
    "logo_url": "",
    "cover_image_url": "",
    "primary_color": "#065f46",
    "accent_color": "#d97706",
    "text_color": "#1f2937",
    "tone": "clinical",
    "include_toc": True,
    "include_appendix": True,
    "include_product_recommendations": False,
    "product_catalog": "",
    "max_sections": 6,
    "max_markers_per_section": 3,
    "clinic_contact": "",
    "clinic_website": "",
    "clinic_email": "",
    "header_left_text": "",
    "header_right_text": "",
    "footer_left_text": "",
    "footer_center_text": "",
    "footer_right_text": "",
    "show_page_numbers": True,
}


def load_practitioner_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)

    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    return config


def save_practitioner_config(new_config: dict) -> dict:
    config = dict(DEFAULT_CONFIG)
    config.update(new_config)

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return config