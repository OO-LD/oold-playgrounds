"""Write the settings overrides the site ships with.

The agent brief is authored as markdown in contents/OOLD-AGENT.md and is also the
default system prompt. Generating the override from that file keeps one source:
pasting the markdown into JSON by hand would drift the moment either is edited.

Without this, a visitor gets the stock Jupyternaut prompt and has to paste the
brief into AI Settings before the agent knows anything about OO-LD.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "contents" / "OOLD-AGENT.md"
OUT = ROOT / "overrides.json"

SETTINGS_MODEL = "@jupyternaut/persona:settings-model"


def main() -> None:
    if not BRIEF.is_file():
        raise SystemExit(f"agent brief not found: {BRIEF}")

    brief = BRIEF.read_text(encoding="utf-8").strip()
    if not brief.startswith("# OO-LD agent brief"):
        raise SystemExit(
            f"{BRIEF.name} no longer starts with the heading the tests pin; "
            "update tests/agent-loop.mjs with it rather than loosening this check"
        )

    overrides = {SETTINGS_MODEL: {"systemPrompt": brief}}
    OUT.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}, systemPrompt {len(brief)} chars")


if __name__ == "__main__":
    main()
