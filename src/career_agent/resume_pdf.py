"""Local PDF exports of reviewed packet text, with adapted Career Ops styling.

Template and Chromium print settings adapted from career-ops-hq/career-ops,
commit 605a1e61ee2b0d269b371ebe4351180993ebbfee. See third_party/CareerOps-LICENSE.txt.
"""
from __future__ import annotations

from html import escape
from importlib.resources import files

from .application import validate_packet


class PDFUnavailable(RuntimeError):
    """Optional renderer or its browser is unavailable."""


def resume_html(packet: dict) -> str:
    """Render only saved résumé text; never interpolate profile notes or raw HTML."""
    validate_packet(packet)
    text = packet["resume_markdown"]
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("Resume exceeds the PDF export limit")
    parts = []
    in_list = False
    for line in text.splitlines():
        if not line.startswith("- ") and in_list:
            parts.append("</ul>")
            in_list = False
        if line.startswith("# "):
            parts.append('<header class="header"><h1>' + escape(line[2:]) + '</h1><div class="header-gradient"></div></header>')
        elif line.startswith("## "):
            parts.append('<h2 class="section-title">' + escape(line[3:]) + '</h2>')
        elif line.startswith("- "):
            if not in_list:
                parts.append('<ul class="job">')
                in_list = True
            parts.append("<li>" + escape(line[2:]) + "</li>")
        elif line.strip():
            parts.append('<p class="summary-text">' + escape(line) + "</p>")
    if in_list:
        parts.append("</ul>")
    css = files("career_agent").joinpath("templates", "resume.css").read_text(encoding="utf-8")
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>Resume</title><style>' + css + '</style></head><body><main class="page">'
            + "\n".join(parts) + '</main></body></html>')


def resume_pdf(packet: dict) -> bytes:
    document = resume_html(packet)
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as error:
        raise PDFUnavailable("Install the pdf extra and Playwright Chromium to export PDFs") from error
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=30_000)
            try:
                context = browser.new_context(java_script_enabled=False, offline=True)
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_default_timeout(30_000)
                page.set_content(document, wait_until="load")
                # Career Ops' printBackground/preferCSSPageSize rendering settings.
                return page.pdf(print_background=True, prefer_css_page_size=True,
                                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
            finally:
                browser.close()
    except Error as error:
        raise PDFUnavailable("PDF renderer unavailable; install Playwright Chromium and retry") from error
