"""The editors, exercised in a real browser against a served app.

The app under test carries two deliberate mistakes - an instance value of the wrong type and
a schema title of the wrong type - plus one bare editor whose ``$schema`` names a live URL
registered in no store. Every assertion here fails loudly if the mechanism behind it breaks:
inline validation (JSON and YAML, instances against the resolved chain, schemas against the
meta-schema), schema-driven completion while typing, and the JSON/YAML/Turtle tokenizers.

The page is loaded once per module: the panes are independent, but loading costs ~15 seconds
and the assertions only read (the completion test types into a buffer no other test reads).
"""

from __future__ import annotations

import json
import socket

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


def _probe_app():
    from panelini.panels.monacoeditor import MonacoEditor

    from schema_playground import build
    from schema_playground.config import SOURCE_INSTANCE, TARGET_SCHEMA
    from schema_playground.state import PlaygroundState

    broken_instance = json.loads(SOURCE_INSTANCE)
    broken_instance["name"] = 123  # the schema says string
    broken_schema = json.loads(TARGET_SCHEMA)
    broken_schema["title"] = 123  # the 2020-12 core vocabulary says string

    state = PlaygroundState(
        source_instance=json.dumps(broken_instance, indent=2),
        target_schema=json.dumps(broken_schema, indent=2),
    )
    app = build(state)
    external = MonacoEditor(
        value=json.dumps(
            {"$schema": "https://oo-ld.org/latest/schemas/Person.schema.json", "name": 123},
            indent=2,
        ),
        schema_request="ignore",
        enable_schema_request=True,
        height=120,
    )
    app.main_add(objects=[pn.Column("external probe", external, name="external-probe")])
    return app


@pytest.fixture(scope="module")
def page():
    pn.extension()
    port = _free_port()
    server = pn.serve(
        _probe_app(), port=port, websocket_origin=f"127.0.0.1:{port}", show=False, threaded=True
    )
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except PlaywrightError:
                pytest.skip("no playwright browser installed (python -m playwright install chromium)")
            page = browser.new_page(viewport={"width": 1800, "height": 1200})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            for _ in range(60):
                if page.locator(".monaco-editor").count() > 0:
                    break
                page.wait_for_timeout(1_000)
            else:
                pytest.fail("no monaco editor appeared")
            page.wait_for_timeout(12_000)
            yield page
            browser.close()
    finally:
        server.stop()


def _editor_with(page, text):
    return page.locator(".monaco-editor:visible").filter(has_text=text).nth(0)


def _squiggles(editor) -> int:
    return editor.locator("[class*=squiggly]").count()


def _token_classes(editor) -> set[str]:
    return set(editor.locator("[class*=mtk]").evaluate_all("els => els.map(e => e.className)"))


def test_instance_json_validates_against_the_chain(page):
    assert _squiggles(_editor_with(page, '"name": 123')) > 0


def test_schema_json_validates_core_keywords(page):
    """`title: 123` violates the 2020-12 core vocabulary, which the meta store resolves offline."""
    assert _squiggles(_editor_with(page, '"title": 123')) > 0


def test_an_unregistered_external_schema_is_fetched(page):
    """A live URL in `$schema`, in no store: only a network fetch can produce this marker."""
    assert _squiggles(_editor_with(page, "oo-ld.org/latest/schemas/Person")) > 0


def test_completion_appears_while_typing(page):
    """Schema-driven suggestions inside a string, with no explicit trigger keystroke.

    Runs before the tab-switching tests: it types into the source instance buffer, and the
    suggest widget only opens while that editor holds focus.
    """
    editor = _editor_with(page, "jane@example.org")
    editor.locator(".view-lines").click()
    page.keyboard.press("Control+Home")
    page.keyboard.press("End")
    page.keyboard.type('"em')
    page.wait_for_timeout(3_000)
    rows = page.locator(".suggest-widget .monaco-list-row")
    if rows.count() == 0:
        page.keyboard.press("Control+Space")
        page.wait_for_timeout(3_000)
    labels = [rows.nth(i).inner_text() for i in range(rows.count())]
    assert any("email" in label for label in labels), labels
    page.keyboard.press("Escape")
    # Undo the typed fragment: it makes the buffer unparseable, and the later tests need the
    # instance to keep exporting (auto-closing may have inserted a second quote, so several
    # undo stops).
    for _ in range(4):
        page.keyboard.press("Control+z")
    page.wait_for_timeout(3_000)
    assert "em" not in _editor_with(page, "jane@example.org").inner_text().splitlines()[0]


def test_instance_yaml_validates(page):
    page.get_by_text("YAML", exact=True).nth(1).click()
    page.wait_for_timeout(4_000)
    editor = _editor_with(page, "name: 123")
    assert _squiggles(editor) > 0
    assert len(_token_classes(editor)) > 1, "yaml should be highlighted, not plain"


def test_schema_yaml_validates(page):
    page.get_by_text("YAML", exact=True).nth(2).click()
    page.wait_for_timeout(4_000)
    assert _squiggles(_editor_with(page, "title: 123")) > 0


def test_turtle_is_highlighted(page):
    page.get_by_text("RDF", exact=True).nth(0).click()
    page.wait_for_timeout(2_500)
    turtle = page.locator(".monaco-editor:visible").filter(has_text="@prefix")
    assert turtle.count() > 0
    assert len(_token_classes(turtle.nth(0))) > 1
