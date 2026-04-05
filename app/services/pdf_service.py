# app/services/pdf_service.py

from pathlib import Path
from playwright.async_api import async_playwright

from app.services.config_service import load_practitioner_config

ROOT = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT / "app" / "generated"


def save_html(html_content: str, filename: str) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / filename
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


def build_header_template(config: dict) -> str:
    left = config.get("header_left_text", "") or ""
    right = config.get("header_right_text", "") or ""

    return f"""
    <div style="
        width: 100%;
        font-size: 9px;
        color: #666;
        padding: 0 12mm;
        box-sizing: border-box;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-family: Arial, sans-serif;
    ">
        <div>{left}</div>
        <div>{right}</div>
    </div>
    """


def build_footer_template(config: dict) -> str:
    left = config.get("footer_left_text", "") or ""
    center = config.get("footer_center_text", "") or ""
    right = config.get("footer_right_text", "") or ""
    show_page_numbers = config.get("show_page_numbers", True)

    page_html = """
        <span class="pageNumber"></span> / <span class="totalPages"></span>
    """ if show_page_numbers else ""

    return f"""
    <div style="
        width: 100%;
        font-size: 9px;
        color: #666;
        padding: 0 12mm;
        box-sizing: border-box;
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        align-items: center;
        font-family: Arial, sans-serif;
    ">
        <div style="text-align: left;">{left}</div>
        <div style="text-align: center;">{center}</div>
        <div style="text-align: right;">{right} {page_html}</div>
    </div>
    """


async def save_pdf(html_content: str, filename: str) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / filename
    config = load_practitioner_config()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page()

        # IMPORTANT: ensures CSS + layout fully render before PDF
        await page.set_content(html_content, wait_until="networkidle")

        await page.pdf(
            path=str(output_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=build_header_template(config),
            footer_template=build_footer_template(config),
            margin={
                "top": "18mm",
                "right": "12mm",
                "bottom": "18mm",
                "left": "12mm",
            },
        )

        await browser.close()

    return output_path