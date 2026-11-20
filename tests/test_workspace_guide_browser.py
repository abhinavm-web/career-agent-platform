"""Offline browser coverage for contextual help and small-screen navigation."""
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.environ.get("CAREER_AGENT_TEST_BROWSER") == "1", "Requires installed Chromium")
class WorkspaceGuideTests(unittest.TestCase):
    def test_help_does_not_activate_controls_and_mobile_list_can_be_restored(self):
        import uvicorn
        from playwright.sync_api import expect, sync_playwright
        from career_agent.dashboard import DashboardConfig, create_app
        from career_agent.models import Job
        from career_agent.storage import Store

        with tempfile.TemporaryDirectory() as folder, socket.socket() as sock:
            db = Path(folder) / "workspace.sqlite3"
            with Store(db) as store:
                store.add_jobs([Job("Example", "Example product lead", "Remote", "https://example.invalid/jobs/1")])
            app = create_app(DashboardConfig(db_path=db, packet_dir=Path(folder) / "packets"))
            sock.bind(("127.0.0.1", 0))
            server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page(viewport={"width": 360, "height": 800}, has_touch=True)
                    page.goto(f"http://127.0.0.1:{sock.getsockname()[1]}")
                    page.locator("#menu-toggle").tap()
                    page.locator("#nav-jobs").tap()
                    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
                    page.locator("#control-help").tap()
                    page.locator("#show-add-job").tap()
                    expect(page.get_by_role("dialog")).to_be_visible()
                    expect(page.locator("#add-job-form")).to_be_hidden()
                    page.keyboard.press("Escape")
                    expect(page.locator("#show-add-job")).to_be_focused()
                    page.locator("#job-list .job-item").first.tap()
                    expect(page.locator("#job-list")).to_be_hidden()
                    page.get_by_role("tab", name="Application", exact=True).tap()
                    page.locator(".compact-back").tap()
                    expect(page.locator("#job-list .selected")).to_be_focused()
                    page.locator("#job-list .selected").tap()
                    expect(page.get_by_role("tab", name="Application", exact=True)).to_have_attribute("aria-selected", "true")
                    for width in [320, 768, 1024, 1440]:
                        page.set_viewport_size({"width": width, "height": 850})
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    page.get_by_role("button", name=re.compile("^Information about Opportunity board$")).click()
                    expect(page.get_by_role("dialog")).to_be_visible()
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
