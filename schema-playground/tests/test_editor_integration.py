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


def test_no_loading_indicator_is_stuck(page):
    """There is no boot overlay by design, and nothing else may leave one in the DOM."""
    for _ in range(30):
        if page.locator(".pn-loading").count() == 0:
            break
        page.wait_for_timeout(1_000)
    assert page.locator(".pn-loading").count() == 0, "a loading indicator never cleared"


def test_instance_json_validates_against_the_chain(page):
    assert _squiggles(_editor_with(page, '"name": 123')) > 0


def test_schema_json_validates_core_keywords(page):
    """`title: 123` violates the 2020-12 core vocabulary, which the meta store resolves offline."""
    assert _squiggles(_editor_with(page, '"title": 123')) > 0


def test_an_unregistered_external_schema_is_fetched(page):
    """A live URL in `$schema`, in no store: only a network fetch can produce this marker."""
    assert _squiggles(_editor_with(page, "oo-ld.org/latest/schemas/Person")) > 0


def _suggest_labels(page) -> list[str]:
    rows = page.locator(".suggest-widget .monaco-list-row")
    return [rows.nth(i).inner_text() for i in range(rows.count())]


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


def test_yaml_completion_offers_the_missing_properties(page):
    """The YAML language service completes from the same schema."""
    page.get_by_text("YAML", exact=True).nth(1).click()
    page.wait_for_timeout(4_000)
    editor = _editor_with(page, "works_for:")
    editor.locator(".view-lines").click()
    page.keyboard.press("Control+End")
    page.keyboard.press("Enter")
    page.keyboard.press("Control+Space")
    page.wait_for_timeout(3_000)
    labels = _suggest_labels(page)
    assert any("homepage" in label for label in labels), labels
    page.keyboard.press("Escape")


def test_completion_offers_the_missing_properties(page):
    """Adding a property to the instance suggests what the schema has and the document lacks.

    The example schema deliberately defines more than the instance uses (homepage,
    birth_date), so this list must not be empty. Runs last: it types into the source
    instance buffer and deliberately leaves it that way - restoring by blind undo proved
    flaky, and nothing runs after.
    """
    # the tab tests before this leave the instance column on another tab
    page.get_by_text("JSON", exact=True).nth(1).click()
    page.wait_for_timeout(2_000)
    editor = _editor_with(page, "jane@example.org")
    # Cursor at the end of the last property line, then a new property position.
    editor.get_by_text('"works_for"').click()
    page.keyboard.press("End")
    page.keyboard.type(',')
    page.keyboard.press("Enter")
    page.keyboard.type('"')
    page.wait_for_timeout(3_000)
    labels = _suggest_labels(page)
    if not labels:
        page.keyboard.press("Control+Space")
        page.wait_for_timeout(3_000)
        labels = _suggest_labels(page)
    assert any("homepage" in label for label in labels), labels
    assert any("birth_date" in label for label in labels), labels
    # readable, not merely present: inside a shadow root monaco's focus tracking breaks, and
    # unfixed the focused row painted white text on the widget's near-white background
    rows = page.locator(".suggest-widget .monaco-list-row")
    contrast = rows.nth(0).evaluate(
        """el => {
            const px = s => s.match(/\d+/g).slice(0, 3).map(Number);
            const fg = px(getComputedStyle(el.querySelector('.monaco-icon-label') || el).color);
            let node = el, bg = [255, 255, 255];
            while (node) {
                const c = getComputedStyle(node).backgroundColor;
                if (c && !c.includes('0, 0, 0, 0')) { bg = px(c); break; }
                node = node.parentElement;
            }
            return Math.abs(fg[0]-bg[0]) + Math.abs(fg[1]-bg[1]) + Math.abs(fg[2]-bg[2]);
        }"""
    )
    assert contrast > 150, f"suggest row text unreadable (contrast {contrast})"


def test_schema_completion_offers_the_dialect_keywords(page):
    """The schema editor completes from the OO-LD meta-schema, not just from words on screen.

    Monaco always offers word-based suggestions, so "some suggestions appeared" proves
    nothing: a broken schema association looks identical to a working one until you check
    that a keyword no buffer contains is among them. ``x-oold-prior-version`` is in the
    dialect and in none of the shipped documents, so only the meta-schema can supply it.

    Runs last: it types into the source schema buffer and leaves it that way.
    """
    page.get_by_text("JSON", exact=True).nth(0).click()
    page.wait_for_timeout(2_000)

    editor = _editor_with(page, '"$id": "Person.schema.json"')
    editor.locator(".view-lines").click()
    page.keyboard.press("Control+Home")
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.keyboard.type('"x-')
    page.wait_for_timeout(3_000)

    labels = _suggest_labels(page)
    page.keyboard.press("Escape")
    assert labels, "no suggestions at all"
    dialect = [label for label in labels if "x-oold" in label]
    assert dialect, f"no dialect keywords offered, only: {labels[:20]}"
    assert any("x-oold-prior-version" in label for label in dialect), dialect


def test_schema_completion_offers_dialect_keywords_inside_a_property(page):
    """The dialect applies to every subschema, so a property must complete like the root.

    ``x-oold-range`` is the property-level keyword - it constrains what an IRI-valued
    property points at - so a completion list that has it at the root and not here is the
    wrong way round. 2020-12 recurses into ``properties/*`` through the core applicator's
    ``$dynamicRef``, which resolves back to the dialect because the base carries
    ``$dynamicAnchor: "meta"``.

    Runs last: it types into the source schema buffer and leaves it that way.
    """
    page.get_by_text("JSON", exact=True).nth(0).click()
    page.wait_for_timeout(2_000)

    editor = _editor_with(page, '"$id": "Person.schema.json"')
    editor.locator(".view-lines").click()
    # Monaco renders only the lines in view, so the target is reached through find rather
    # than by clicking it or counting Down presses; both break as the document grows.
    page.keyboard.press("Control+Home")
    page.keyboard.press("Control+f")
    page.wait_for_timeout(500)
    page.keyboard.type('"format": "iri"')
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.keyboard.type('"x-')
    page.wait_for_timeout(3_000)

    labels = _suggest_labels(page)
    page.keyboard.press("Escape")
    assert labels, "no suggestions at all inside the property"
    assert any("x-oold-range" in label for label in labels), (
        f"the dialect does not reach a nested subschema, only: {labels[:20]}"
    )
