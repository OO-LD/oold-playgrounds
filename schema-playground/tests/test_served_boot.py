"""The boot sequence of a real served session.

Serves the app exactly as ``panel serve app.py`` does - ``build()`` with no state, per
session - so the deferred first compute and the loading overlay take the code path a user
sees. The other integration module passes an explicit state and never exercises this branch.

Timing is taken from the server-side params (editor ``ready`` flags, ``main.loading``), not
from browser polling: a Playwright locator count over two dozen shadow-rooted Monaco editors
costs seconds per call, which once inflated this measurement from ten seconds to minutes and
pointed the investigation at the wrong side entirely.
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


def test_a_served_session_boots_fast_and_clears_its_overlay():
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
        assert main.loading is True, "the overlay must cover the boot"

        main.param.watch(lambda event: mark(f"loading={event.new}"), "loading")
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
                if any(name == "loading=False" for _, name in events):
                    break
                time.sleep(0.5)

            # one bounded browser-side sanity check: the app content is really there
            assert page.get_by_text("Source schema").count() > 0
            browser.close()
    finally:
        server.stop()

    print("events:", [(round(t, 1), name) for t, name in events])
    cleared = [t for t, name in events if name == "loading=False"]
    ready = [t for t, name in events if name == "editor ready"]
    assert cleared, "the loading overlay never cleared"
    assert cleared[0] < 45, f"overlay cleared too late: {cleared[0]:.1f}s"
    assert len(ready) >= 10, f"only {len(ready)} editors reported ready"
