"""The boot sequence of a real served session.

Serves the app exactly as ``panel serve app.py`` does - ``build()`` with no state, per
session - so the deferred first compute takes the code path a user sees. The other
integration module passes an explicit state and never exercises this branch.

There is deliberately no loading overlay to test: the app shell renders immediately with the
source documents, the derived views fill in after the deferred compute, and an own overlay
proved impossible to clear reliably across the server and the Pyodide runtime. What must hold
instead: the session is built fast, the editors become ready, and no loading indicator is
left in the DOM.
"""

from __future__ import annotations

import socket
import time

import pytest

pytest.importorskip("playwright")
pytest.importorskip("panel")

import panel as pn  # noqa: E402
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_served_session_boots_fast_and_leaves_no_overlay():
    from panelini.panels.monacoeditor import MonacoEditor

    from schema_playground import build

    pn.extension()
    events: list[tuple[float, str]] = []
    start = time.monotonic()

    def mark(name: str) -> None:
        events.append((time.monotonic() - start, name))

    def instrumented_build():
        app = build()
        main = app.main[0]
        mark("session built")
        for editor in main.select(MonacoEditor):
            editor.param.watch(lambda _event: mark("editor ready"), "ready")
        return app

    port = _free_port()
    server = pn.serve(
        instrumented_build,
        port=port,
        websocket_origin=f"127.0.0.1:{port}",
        show=False,
        threaded=True,
    )
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except PlaywrightError:
                pytest.skip("no playwright browser installed")
            page = browser.new_page(viewport={"width": 1800, "height": 1200})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            mark("domcontentloaded")

            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if sum(1 for _, name in events if name == "editor ready") >= 10:
                    break
                time.sleep(0.5)

            assert page.get_by_text("Source schema").count() > 0
            page.wait_for_timeout(3000)
            assert page.locator(".pn-loading").count() == 0, "a loading indicator is stuck in the DOM"
            browser.close()
    finally:
        server.stop()

    print("events:", [(round(t, 1), name) for t, name in events])
    built = [t for t, name in events if name == "session built"]
    ready = [t for t, name in events if name == "editor ready"]
    assert built and built[0] < 30, f"session build too slow: {built}"
    assert len(ready) >= 10, f"only {len(ready)} editors reported ready"
