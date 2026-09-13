"""Load the built browser app and report what the console says.

A Pyodide failure is invisible from the server side: the page serves fine and the error only
appears once micropip has run. This drives a real browser so that failure is reported here
rather than discovered by a user.

Usage: python scripts/check_wasm.py [url] [--timeout SECONDS] [--shot path.png]
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_URL = "http://127.0.0.1:8130/app.html"


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

        # Ready means the app did its work, not merely that Bokeh drew something: the
        # transformed instance only appears once the schemas were read, the instance exported
        # to RDF and imported through the target schema, all inside the browser.
        # Located rather than read off ``document.body.innerText``: Panel renders into shadow
        # roots, which that text does not reach, so the page looks empty while showing an app.
        deadline = args.timeout * 1000
        ready = False
        found: list[str] = []
        try:
            for text, timeout in (("Transformed instance", deadline), ("full_name", 60_000)):
                page.wait_for_selector(f"text={text}", timeout=timeout, state="attached")
                found.append(text)
            ready = True
        except Exception:
            ready = False

        # The loading overlay must leave once the app rendered - a stuck spinner over a
        # working app looks exactly like a broken app. Give it up to a minute: the browser
        # build has no server whose timing this script could see.
        overlay_cleared = False
        if ready:
            for _ in range(60):
                if page.locator(".pn-loading").count() == 0:
                    overlay_cleared = True
                    break
                page.wait_for_timeout(1000)

        page.wait_for_timeout(3000)
        page.screenshot(path=args.shot, full_page=False)
        body = ", ".join(found) or "(nothing rendered)"
        browser.close()

    print(f"ready: {ready}")
    print(f"overlay cleared: {overlay_cleared}")
    print(f"screenshot: {args.shot}")
    print("\n--- rendered ---")
    print(body)

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
    # on whether it rendered, transformed and became usable, which is what a user would notice.
    passed = ready and overlay_cleared
    print(f"\nRESULT: {'OK' if passed else 'FAIL'}")
    if errors and passed:
        print("(the app works; the JavaScript errors above did not stop it)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
