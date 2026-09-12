"""Generate the demo notebook from readable cell sources."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contents" / "oold_demo.ipynb"

SETUP = '''\
import importlib
import importlib.metadata
import sys

import piplite

# typing-extensions must be first: Pyodide ships 4.11.0 and micropip refuses to
# resolve a >=4.14.0 requirement against an already-installed older version.
await piplite.install("typing-extensions>=4.14.0")

# typing_extensions has no __version__; the distribution metadata is the only
# reliable source. Drop the stale module so the freshly installed files win.
for _name in [k for k in sys.modules if k == "typing_extensions"]:
    del sys.modules[_name]
import typing_extensions

print("typing-extensions", importlib.metadata.version("typing_extensions"))
print("python", sys.version)
'''

DEPS = '''\
import traceback

RESULTS = {}

DEPS = ["rdflib", "pyld", "sparqlwrapper", "jsondiff"]
IMPORT_NAME = {"sparqlwrapper": "SPARQLWrapper", "pyld": "pyld"}


async def probe(pkg):
    mod = IMPORT_NAME.get(pkg, pkg)
    try:
        await piplite.install(pkg)
    except Exception as exc:
        return {"install": "FAIL", "import": "SKIP", "error": f"{type(exc).__name__}: {exc}"}
    try:
        m = importlib.import_module(mod)
    except Exception:
        return {"install": "OK", "import": "FAIL", "error": traceback.format_exc(limit=2).strip()}
    return {"install": "OK", "import": "OK", "version": getattr(m, "__version__", "n/a")}


for pkg in DEPS:
    RESULTS[pkg] = await probe(pkg)

for pkg, r in RESULTS.items():
    print(f"{pkg:16} install={r['install']:5} import={r['import']:5} {r.get('version', r.get('error', ''))}")
'''

INSTALL_OOLD = '''\
try:
    await piplite.install("oold", keep_going=True)
    import oold

    RESULTS["oold"] = {"install": "OK", "import": "OK", "version": oold.__version__}
except Exception:
    RESULTS["oold"] = {"install": "FAIL", "import": "FAIL", "error": traceback.format_exc(limit=3).strip()}

print("oold:", RESULTS["oold"])

# Prove the shipped wheel is the local dev build, not the PyPI release: the
# experimental module only exists on the branch.
import oold.model._notation as _n

print("oold.model._notation ->", _n.__file__)
print("exports:", [s for s in ("Link", "OoldField", "OoldModel") if hasattr(_n, s)])
'''

HTTP_SHIM = '''\
# pyodide_http.patch_all() patches requests and urllib only, never httpx.
# datamodel-code-generator[http] resolves remote $ref via httpx, so map it onto
# the patched requests API. follow_redirects -> allow_redirects.
try:
    await piplite.install("pyodide-http")
    import pyodide_http

    pyodide_http.patch_all()

    import requests

    try:
        import httpx

        def patch_get(url, headers=None, verify=None, follow_redirects=True, params=None, **kwargs):
            return requests.get(url, headers=headers, params=params, allow_redirects=follow_redirects)

        httpx.get = patch_get
        print("httpx shim installed")
    except ImportError:
        print("httpx not present, shim not needed")
    print("pyodide_http.patch_all() applied")
except Exception as exc:
    print("network patching unavailable:", type(exc).__name__, exc)
'''

NOTATION_MODELS = '''\
from pydantic import Field

from oold.backend.document_store import SimpleDictDocumentStore
from oold.backend.interface import SetResolverParam, set_resolver
from oold.model._notation import Link, LinkList, OoldField, OoldModel


class Organization(OoldModel):
    id: str
    name: str | None = None
    type: str | None = "ex:Organization"


class Location(OoldModel):
    id: str | None = None
    address: str | None = None
    type: str | None = "ex:Location"


class Person(OoldModel):
    id: str
    name: str | None = None
    type: str | None = "ex:Person"

    # 1. no range= needed: the target is read from the annotation
    knows: LinkList["Person"] = OoldField()

    # 2. Link[T] inside the annotation (to-one and to-many)
    employer: Link[Organization] = OoldField()
    friends: list["Person"] = OoldField()

    # 3. union: literal text | inline object | reference
    location: str | Location | None = OoldField(link=True)


Person.model_rebuild()
print("models defined:", Person, Organization, Location)
'''

NOTATION_MAIN = '''\
store = SimpleDictDocumentStore()
store.store_json_dicts({
    "ex:bob": {"id": "ex:bob", "name": "Bob", "type": "ex:Person"},
    "ex:carol": {"id": "ex:carol", "name": "Carol", "type": "ex:Person"},
    "ex:acme": {"id": "ex:acme", "name": "ACME", "type": "ex:Organization"},
    "ex:eiffel": {"id": "ex:eiffel", "address": "Champ de Mars", "type": "ex:Location"},
})
set_resolver(SetResolverParam(iri="ex", resolver=store))

alice = Person(
    id="ex:alice",
    name="Alice",
    knows=["ex:bob", "ex:carol"],
    employer="ex:acme",
    friends=[Person(id="ex:bob", name="Bob")],
)

print("1. OoldField() - target inferred from the annotation")
assert alice.knows[0].name == "Bob"
assert isinstance(alice.knows[0], Person)
print("   knows[0].name          =", alice.knows[0].name)
print("   isinstance(.., Person) =", isinstance(alice.knows[0], Person))

print("\\n2. Link[T] inside the annotation")
assert isinstance(alice.employer, Organization)
assert alice.employer.name == "ACME"
assert isinstance(alice.friends[0], Person)
print("   employer.name          =", alice.employer.name)
print("   friends[0].name        =", alice.friends[0].name)

print("\\n3. union arms: text | inline object | reference")
text = Person(id="ex:p-text", location="at the Eiffel Tower")
ref = Person(id="ex:p-ref", location={"@id": "ex:eiffel"})
inline = Person(
    id="ex:p-inline",
    location={"id": "ex:office", "address": "Main St 1", "type": "ex:Location"},
)
blank = Person(id="ex:p-blank", location={"address": "no id", "type": "ex:Location"})

assert text.location == "at the Eiffel Tower"
assert isinstance(ref.location, Location)
assert ref.location.address == "Champ de Mars"
assert inline.location.address == "Main St 1"
assert blank.link_iris("location") is None
print("   text   ->", repr(text.location))
print("   ref    ->", ref.location.address)
print("   inline ->", inline.location.address)
print("   blank  ->", blank.location.address, "(no IRI)")

print("\\n4. serialisation: links to IRIs; references boxed where a literal arm exists")
dumped = alice.model_dump(exclude_none=True)
assert dumped["knows"] == ["ex:bob", "ex:carol"]
assert dumped["employer"] == "ex:acme"
assert ref.model_dump(exclude_none=True)["location"] == {"@id": "ex:eiffel"}
assert isinstance(blank.model_dump(exclude_none=True)["location"], dict)
print("   alice ->", dumped)
print("   ref   ->", ref.model_dump(exclude_none=True))
print("   blank ->", blank.model_dump(exclude_none=True))

print("\\n5. lazy resolution and query DSL")
lazy = Person(id="ex:lazy", knows=["ex:bob"])
assert lazy.link_iris("knows") == ["ex:bob"]
condition = Person.name == "Bob"
assert condition.field == "name" and condition.value == "Bob"
print("   link_iris('knows')     =", lazy.link_iris("knows"))
print("   Person.name == 'Bob'   =", condition)

print("\\n6. de-serialisation: every union arm survives a round trip")
for label, value, expected in [
    ("text     ", "at the Eiffel Tower", str),
    ("reference", {"@id": "ex:eiffel"}, Location),
    ("inline   ", {"address": "Main St 1", "type": "ex:Location"}, Location),
]:
    original = Person(id="ex:rt", location=value)
    payload = original.model_dump(exclude_none=True)
    restored = Person(**payload)
    assert isinstance(restored.location, expected), label
    shown = str(payload["location"])[:34]
    print(f"   {label} {shown:36} -> {type(restored.location).__name__}")

restored = Person(**alice.model_dump(exclude_none=True))
assert [x.id for x in restored.knows] == ["ex:bob", "ex:carol"]
assert restored.employer.name == "ACME"
print("   lists and to-one links round trip too")

print("\\nALL CHECKS PASSED")
'''

CODEGEN = '''\
import json

OOLD_SCHEMA = {
    "@context": {
        "ex": "https://example.org/",
        "serial_number": "ex:serialNumber",
        "manufacturer": {"@id": "ex:manufacturer", "@type": "@id"},
        "power_rating_watts": "ex:powerRating",
        "calibration_tags": "ex:calibrationTag",
    },
    "title": "Instrument",
    "type": "object",
    "required": ["serial_number"],
    "properties": {
        "serial_number": {"type": "string", "title": "Serial number"},
        "manufacturer": {"type": "string", "title": "Manufacturer"},
        "power_rating_watts": {"type": "number", "title": "Power rating watts"},
        "calibration_tags": {
            "type": "array",
            "items": {"type": "string"},
            "title": "Calibration tags",
        },
        "commissioned_on": {"type": "string", "format": "date", "title": "Commissioned on"},
    },
}

from datamodel_code_generator import DataModelType, PythonVersion
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

print("datamodel-code-generator", importlib.metadata.version("datamodel-code-generator"))

_types = get_data_model_types(
    DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_310
)

# parse(), not generate(): generate() writes files, parse() stays in memory.
# formatters=[] plus format_=False keeps black, isort and ruff out of the path.
# ruff in particular shells out via subprocess.run and hard-fails on Emscripten.
parser = JsonSchemaParser(
    json.dumps(OOLD_SCHEMA),
    data_model_type=_types.data_model,
    data_model_root_type=_types.root_model,
    data_model_field_type=_types.field_model,
    data_type_manager_type=_types.data_type_manager,
    target_python_version=PythonVersion.PY_310,
    formatters=[],
    use_title_as_name=True,
    use_schema_description=True,
    use_field_description=True,
    use_double_quotes=True,
    collapse_root_models=True,
    reuse_model=True,
)
GENERATED_SOURCE = str(parser.parse(format_=False))
print(GENERATED_SOURCE)
'''

EXEC_GENERATED = '''\
# The decisive step: this class exists only as a runtime object. No source file
# on disk ever contains "class Instrument", so a static analyser has nothing to
# read.
exec(GENERATED_SOURCE, globals())

instrument = Instrument(
    serial_number="SN-0001",
    manufacturer="ex:acme",
    power_rating_watts=42.5,
    calibration_tags=["annual", "iso17025"],
)
print("runtime class:", Instrument)
print("defined in   :", Instrument.__module__)
print("source file  :", getattr(sys.modules.get(Instrument.__module__), "__file__", "none"))
print("fields       :", sorted(Instrument.model_fields))
print("instance     :", instrument.model_dump(exclude_none=True))

_ip = get_ipython()
print("in user_ns        :", "Instrument" in _ip.user_ns)
print("in user_global_ns :", "Instrument" in _ip.user_global_ns)
print("user_ns is global :", _ip.user_ns is _ip.user_global_ns)
'''

CREATE_MODEL = '''\
# Second runtime path, with no generated source text at all: pydantic builds the
# class straight from a field mapping. Nothing anywhere is parseable as
# "class Sensor".
from pydantic import create_model

Sensor = create_model(
    "Sensor",
    sensor_uri=(str, ...),
    reading_unit=(str | None, None),
    sample_rate_hz=(float | None, None),
    calibrated_by=(str | None, None),
)
sensor = Sensor(sensor_uri="ex:s1", reading_unit="K", sample_rate_hz=10.0)
print("create_model class:", Sensor)
print("fields            :", sorted(Sensor.model_fields))
print("instance          :", sensor.model_dump(exclude_none=True))

# Control: a runtime class that is not a pydantic model. It separates "created
# at runtime" from "pydantic v2 keeps fields out of the class namespace", which
# are two different reasons a name might fail to complete.
exec(
    "class PlainRuntime:\\n"
    "    alpha = 1\\n"
    "    beta = 'two'\\n"
    "    def gamma(self):\\n"
    "        return self.alpha\\n",
    globals(),
)
plain = PlainRuntime()
print("control class     :", PlainRuntime, sorted(n for n in dir(PlainRuntime) if not n.startswith("_")))
'''

DIR_DUMP = '''\
# Isolates where any completion gap comes from: pydantic v2 field storage or
# IPython evaluation policy. pydantic v2 keeps fields in model_fields rather
# than as class attributes, so whether dir() surfaces them is not obvious.
def public(names):
    return sorted(n for n in names if not n.startswith("_"))


def dir2(obj):
    return set(dir(obj)) | set(dir(obj.__class__))


LINK_FIELDS = ["knows", "employer", "friends", "location"]
GEN_FIELDS = ["serial_number", "manufacturer", "power_rating_watts", "calibration_tags"]
SENSOR_FIELDS = ["sensor_uri", "reading_unit", "sample_rate_hz", "calibrated_by"]

print("Person.model_fields    :", sorted(Person.model_fields))
print("Instrument.model_fields:", sorted(Instrument.model_fields))
print("Sensor.model_fields    :", sorted(Sensor.model_fields))
print()
print("dir(Person) link fields    :", [f for f in LINK_FIELDS if f in dir(Person)])
print("dir(alice) link fields     :", [f for f in LINK_FIELDS if f in dir(alice)])
print("dir2(Person) link fields   :", [f for f in LINK_FIELDS if f in dir2(Person)])
print("dir2(alice) link fields    :", [f for f in LINK_FIELDS if f in dir2(alice)])
print()
print("dir(Instrument) gen fields :", [f for f in GEN_FIELDS if f in dir(Instrument)])
print("dir(instrument) gen fields :", [f for f in GEN_FIELDS if f in dir(instrument)])
print("dir(Sensor)     gen fields :", [f for f in SENSOR_FIELDS if f in dir(Sensor)])
print("dir(sensor)     gen fields :", [f for f in SENSOR_FIELDS if f in dir(sensor)])
print()
print("public(dir(alice))[:40]      :", public(dir(alice))[:40])
print("public(dir(instrument))[:40] :", public(dir(instrument))[:40])
print()
print("BaseModel defines __getattr__:", "__getattr__" in vars(__import__("pydantic").BaseModel))
'''

COMPLETION_SELFTEST = '''\
# The kernel answers complete_request through IPCompleter.completions(), not
# through InteractiveShell.complete(), and the two disagree when use_jedi is on.
# Use the same call the kernel uses.
from IPython.core.completer import provisionalcompleter

ip = get_ipython()
print("use_jedi           :", ip.Completer.use_jedi)
print("evaluation policy  :", ip.Completer.evaluation)


def complete(line):
    with provisionalcompleter():
        comps = list(ip.Completer.completions(line, len(line)))
    return [c.text for c in comps if not c.text.startswith("_")]


def hits(line, want):
    got = complete(line)
    return [w for w in want if w in got], got


# Expectation per probe: True means the names must be offered, False means they
# must not be, and the reason is recorded below.
PROBES = [
    ("Person.", LINK_FIELDS, True),
    ("alice.", LINK_FIELDS, True),
    ("Instrument.", GEN_FIELDS, False),
    ("instrument.", GEN_FIELDS, True),
    ("Sensor.", SENSOR_FIELDS, False),
    ("sensor.", SENSOR_FIELDS, True),
    ("PlainRuntime.", ["alpha", "beta", "gamma"], True),
    ("plain.", ["alpha", "beta", "gamma"], True),
    ("alice.employer.", ["name", "id", "type"], True),
]

for line, want, expected in PROBES:
    hit, got = hits(line, want)
    print(f"  {line:16} {len(got):3} matches  hit={hit}")

print()
for line, want, expected in PROBES:
    hit, got = hits(line, want)
    if expected:
        assert hit == want, f"{line} expected all of {want}, got {hit}"
    else:
        # pydantic v2 keeps declared fields in model_fields, not in the class
        # namespace, so a bare model class offers only the pydantic API. This is
        # a pydantic property, not a limitation of runtime construction:
        # PlainRuntime above is built by the same exec and does complete.
        assert hit == [], f"{line} unexpectedly offered {hit}"
        assert "model_validate" in got, f"{line} returned nothing at all"

print("COMPLETION SELFTEST PASSED")
'''

ANYWIDGET = '''\
try:
    await piplite.install("anywidget")
    import anywidget
    import traitlets

    class Probe(anywidget.AnyWidget):
        _esm = "function render({ model, el }) { el.textContent = 'anywidget alive: ' + model.get('value'); }\\nexport default { render };"
        value = traitlets.Unicode("oold").tag(sync=True)

    RESULTS["anywidget"] = {"install": "OK", "import": "OK", "version": anywidget.__version__}
    print("anywidget", anywidget.__version__)
    probe = Probe()
    display(probe)
except Exception:
    RESULTS["anywidget"] = {"install": "FAIL", "import": "FAIL", "error": traceback.format_exc(limit=3).strip()}
    print("anywidget FAILED:", RESULTS["anywidget"]["error"])
'''

SUMMARY = '''\
import json

print(json.dumps(RESULTS, indent=2, default=str))
print("\\nNOTEBOOK COMPLETE")
'''


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.rstrip("\n").splitlines(keepends=True),
    }


CELLS = [
    md("# oold in JupyterLite\n\nStatic, serverless deployment. The kernel is Pyodide in the browser tab.\n"),
    md("## 1. Environment\n"),
    code(SETUP),
    md("## 2. Pyodide compatibility of oold runtime dependencies\n"),
    code(DEPS),
    code(INSTALL_OOLD),
    code(HTTP_SHIM),
    md("## 3. Link notations: `OoldField()`, `Link[T]`, union arms\n"),
    code(NOTATION_MODELS),
    code(NOTATION_MAIN),
    md("## 4. Runtime code generation\n\nSchema in, pydantic source out, `exec` it. The resulting class has no source file.\n"),
    code(CODEGEN),
    code(EXEC_GENERATED),
    code(CREATE_MODEL),
    md("## 5. Completion over live objects\n"),
    code(DIR_DUMP),
    code(COMPLETION_SELFTEST),
    md("## 6. anywidget under Pyodide\n"),
    code(ANYWIDGET),
    md("## 7. Summary\n"),
    code(SUMMARY),
]

NOTEBOOK = {
    "cells": CELLS,
    "metadata": {
        "kernelspec": {"display_name": "Python (Pyodide)", "language": "python", "name": "python"},
        "language_info": {
            "name": "python",
            "version": "3.12",
            "mimetype": "text/x-python",
            "file_extension": ".py",
            "codemirror_mode": {"name": "ipython", "version": 3},
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(NOTEBOOK, indent=1) + "\n", encoding="utf-8")
print("wrote", OUT, len(CELLS), "cells")
