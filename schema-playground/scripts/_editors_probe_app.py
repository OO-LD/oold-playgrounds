"""Probe: the real app with a deliberately invalid source instance."""

import json

import panel as pn

from schema_playground import build
from schema_playground.config import SOURCE_INSTANCE
from schema_playground.state import PlaygroundState

pn.extension()

broken = json.loads(SOURCE_INSTANCE)
broken["name"] = 123  # the schema says string
state = PlaygroundState(source_instance=json.dumps(broken, indent=2))
build(state).servable()
