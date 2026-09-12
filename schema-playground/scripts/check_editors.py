"""Assert inline validation, completion, and rich YAML/Turtle rendering in the served app."""

import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8142/_editors_probe_app"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1800, "height": 1000})
    page.goto(URL, wait_until="domcontentloaded")
    for _ in range(60):
        if page.locator(".monaco-editor").count() > 0:
            break
        page.wait_for_timeout(1_000)
    else:
        sys.exit("no monaco editor appeared")
    page.wait_for_timeout(12_000)

    results: dict[str, bool] = {}

    def token_classes(editor) -> set[str]:
        return set(editor.locator("[class*=mtk]").evaluate_all("els => els.map(e => e.className)"))

    visible = page.locator(".monaco-editor:visible")
    print("visible editors:", visible.count())

    # 1. inline validation: the invalid instance (name: 123) must carry a squiggle
    squiggles = page.locator("[class*=squiggly]").count()
    results["squiggles on invalid instance"] = squiggles > 0
    print(f"squiggle elements: {squiggles}")

    # 2. distinct token colors in the visible JSON editors
    results["json highlighted"] = len(token_classes(visible.nth(0))) > 1

    # 3. YAML tab of the source schema column
    page.get_by_text("YAML", exact=True).nth(0).click()
    page.wait_for_timeout(2_500)
    yaml_editor = page.locator(".monaco-editor:visible").nth(0)
    yaml_classes = token_classes(yaml_editor)
    results["yaml highlighted"] = len(yaml_classes) > 1
    print(f"yaml token classes: {len(yaml_classes)}")

    # 4. RDF tab of the source instance column: turtle tokens
    page.get_by_text("RDF", exact=True).nth(0).click()
    page.wait_for_timeout(2_500)
    turtle_editor = page.locator(".monaco-editor:visible").filter(has_text="@prefix")
    if turtle_editor.count() == 0:
        results["turtle highlighted"] = False
        print("no visible turtle editor found")
    else:
        classes = token_classes(turtle_editor.nth(0))
        results["turtle highlighted"] = len(classes) > 1
        print(f"turtle token classes: {len(classes)}")

    # 5. completion in the source instance JSON editor: cursor in, Ctrl+Space
    page.get_by_text("JSON", exact=True).nth(1).click()
    page.wait_for_timeout(1_500)
    instance_editor = page.locator(".monaco-editor:visible").filter(has_text="jane@example.org").nth(0)
    instance_editor.locator(".view-lines").click()
    page.keyboard.press("Control+Home")
    page.keyboard.press("End")
    page.keyboard.type('"')
    page.wait_for_timeout(3_000)
    suggest = page.locator(".suggest-widget .monaco-list-row").count()
    if suggest == 0:
        page.keyboard.press("Control+Space")
        page.wait_for_timeout(3_000)
        suggest = page.locator(".suggest-widget .monaco-list-row").count()
    results["completion offers entries"] = suggest > 0
    print(f"completion entries: {suggest}")

    page.screenshot(path="probe_monaco.png")
    browser.close()

print()
ok = True
for name, passed in results.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok = ok and passed
print("\nRESULT:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
