"""Load the built browser app and report what the console says.

A Pyodide failure is invisible from the server side: the page serves fine and the error only
appears once micropip has run. This drives a real browser so that failure is reported here
rather than discovered by a user.

Adapted from ``schema-playground/scripts/check_wasm.py``, which is where the console
handling and the loading-overlay wait were worked out.

Usage: python scripts/check_wasm.py [url] [--timeout SECONDS] [--shot path.png]
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_URL = "http://127.0.0.1:8140/app.html"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--shot", default="wasm.png")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    messages: list[tuple[str, str]] = []
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.on("console", lambda m: messages.append((m.type, m.text)))
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)

        # Ready means the editor did its work, not merely that Bokeh drew something: the
        # canvas only carries blocks once the sample was parsed and its plan built, all
        # inside the browser. Located rather than read off ``document.body.innerText``:
        # Panel renders into shadow roots, which that text does not reach, so the page
        # looks empty while showing an app.
        deadline = args.timeout * 1000
        ready = False
        found: list[str] = []
        try:
            for selector, timeout in ((".awl-block", deadline), ("[data-testid=status]", 60_000)):
                page.wait_for_selector(selector, timeout=timeout, state="attached")
                found.append(selector)
            ready = True
        except Exception:
            ready = False

        # A stuck spinner over a working app looks exactly like a broken app. Give it up to
        # a minute: the browser build has no server whose timing this script could see.
        overlay_cleared = False
        if ready:
            for _ in range(60):
                if page.locator(".pn-loading").count() == 0:
                    overlay_cleared = True
                    break
                page.wait_for_timeout(1000)

        # Exactly one live document: the prerendered one must be disposed on takeover, or a
        # second copy of every model stays alive and Bokeh.documents[0] is a dead decoy.
        single_document = False
        blocks = 0
        if ready:
            page.wait_for_timeout(2000)
            state = page.evaluate(
                "() => ({docs: Bokeh.documents.length, blocks: document.querySelectorAll('.awl-block').length})"
            )
            single_document = state["docs"] == 1
            blocks = int(state["blocks"])

        page.wait_for_timeout(3000)
        page.screenshot(path=args.shot, full_page=False)
        rendered = ", ".join(found) or "(nothing rendered)"
        browser.close()

    print(f"ready: {ready}")
    print(f"overlay cleared: {overlay_cleared}")
    print(f"single document: {single_document}")
    print(f"blocks drawn: {blocks}")
    print(f"screenshot: {args.shot}")
    print("\n--- rendered ---")
    print(rendered)

    print(f"\n--- console ({len(messages)} messages) ---")
    for kind, text in messages[-60:]:
        # A micropip resolution failure is the whole point of this script, and it is the one
        # message that is long, so it is never truncated.
        limit = 100_000 if ("micropip" in text or "PythonError" in text) else 300
        print(f"[{kind}] {text[:limit]}")
    if not messages:
        print("(none)")

    print("\n--- page errors ---")
    for text in errors[:20]:
        print(text[:800])
    if not errors:
        print("(none)")

    # A JavaScript error is reported but does not by itself fail the check: the app is judged
    # on whether it rendered and became usable, which is what a user would notice.
    passed = ready and overlay_cleared and single_document and blocks > 0
    print(f"\nRESULT: {'OK' if passed else 'FAIL'}")
    if errors and passed:
        print("(the app works; the JavaScript errors above did not stop it)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
