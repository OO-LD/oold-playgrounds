"""Deliberately broken snippet: proves the ty checker is live in the browser."""

from oold.model._notation import OoldModel


class Widget(OoldModel):
    size: int = 0
    label: str = "widget"


# 1. stdlib-only error, needs no injected third-party source
count: int = "definitely not an int"

# 2. pydantic model field error, needs the injected oold and pydantic sources
widget = Widget()
widget.size = "also not an int"
