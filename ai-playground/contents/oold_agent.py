"""One module an LLM agent reaches the whole OO-LD toolchain through.

The agent runs inside a Pyodide kernel: no subprocess, no threads, an in-memory filesystem
rooted at ``/drive``, and a network that only reaches the origin the site was served from.
Everything here is shaped by that.

Start a session with::

    import oold_agent
    await oold_agent.setup()

``setup`` is the only coroutine. It installs ``oold`` and the ``validation`` extra into the
kernel, which cannot happen at import time because ``piplite.install`` is async. Everything
after it is an ordinary function call.

Four groups of entry points:

* :func:`oold_cli` runs the real ``oold`` command line in-process, so a transcript reads like
  CLI use even though Emscripten has no subprocess to spawn one in.
* :func:`validate_schema`, :func:`validate_instance` and :func:`validate` wrap the library API
  and hand back the :class:`oold.validation.report.Report` object. Findings are data on that
  report, not exceptions.
* :func:`csv_profile` reads a CSV into the description an agent needs before it can propose
  terms: columns, inferred types, example values, how much is missing, and which column looks
  like the identifier. :func:`xlsx_profile` and :func:`sheets` answer the same questions for a
  spreadsheet, one sheet at a time, and :func:`doc_profile` and :func:`tables` answer them for
  a table inside a docx, a pptx or a pdf. :func:`doc_text` returns the prose next to those
  tables, which is usually what says what a column means.
* :func:`to_rdf`, :func:`from_rdf`, :func:`transform`, :func:`graph` and :func:`terms` carry an
  instance between schemas through RDF and render what came out. Ported from the schema
  playground (``schema_playground.transform``, ``.mappings``, ``.graph``, ``.terms``).

Every ``oold``, ``pyld`` and ``rdflib`` import is deferred into the function that needs it.
Importing this module must work before :func:`setup` has installed anything, otherwise the
agent cannot read its own API.
"""

from __future__ import annotations

import os

# The descriptor binding is selected when oold.model is first imported, so it has to be in the
# environment before that happens. This module is the agent's first import by construction.
os.environ.setdefault("OOLD_DESCRIPTOR_BINDING", "1")

import contextlib
import csv
import datetime
import html
import io
import json
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

__all__ = [
    "JSON_LD",
    "NQUADS",
    "REQUIREMENTS",
    "TURTLE",
    "chain",
    "csv_profile",
    "doc_profile",
    "doc_text",
    "from_rdf",
    "graph",
    "oold_cli",
    "reference_preserving_frame",
    "setup",
    "sheets",
    "tables",
    "terms",
    "to_rdf",
    "transform",
    "validate",
    "validate_instance",
    "validate_schema",
    "xlsx_profile",
]

SETUP_HINT = "oold is not installed in this kernel yet. Run: await oold_agent.setup()"
CALAMINE_HINT = "python-calamine is not installed in this kernel yet. Run: await oold_agent.setup()"
DOCUMENT_HINT = "the document readers are not installed in this kernel yet. Run: await oold_agent.setup()"

#: Serializations accepted and produced.
TURTLE = "text/turtle"
JSON_LD = "application/ld+json"
NQUADS = "application/n-quads"

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


# ---------------------------------------------------------------------------- kernel setup

#: What :func:`setup` installs on top of the oold wheel.
#:
#: ``piplite`` resolves no extras, so ``jsonschema[format-nongpl]`` has to be spelled out:
#: ``oold.validation.formats`` copies `iri`, `uri`, `hostname` and the rest out of
#: ``Draft202012Validator.FORMAT_CHECKER`` at import time and raises KeyError on any that
#: the backing package never registered.
#:
#: ``python-calamine`` and ``pymupdf`` are compiled wheels from the Pyodide distribution and
#: are installed rather than imported, for the same reason ``pyodide-http`` is: the kernel
#: loads a distribution package only when the literal import appears in the cell source it is
#: about to run, and the imports in :func:`_workbook` and :func:`_pdf_content` are invisible
#: to that scan. ``openpyxl``, ``python-docx`` and ``python-pptx`` are in no distribution, are
#: pure python and are vendored into the site. ``openpyxl`` is the only xlsx writer available
#: and ``DataFrame.to_excel`` fails without it.
#:
#: ``pymupdf`` is the largest single thing a cold start pulls: 17.3 MB from the CDN, 2.2 s
#: of the 9.8 s :func:`setup` takes against a local server, and nothing on a second visit
#: because the CDN serves it with a year of cache. It reads pdf text and locates tables in
#: one package; ``pypdf`` would vendor as pure python and has no table extraction at all.
REQUIREMENTS = (
    "jsonschema>=4.25",
    "referencing>=0.30",
    "click>=8.1",
    "fqdn",
    "idna",
    "isoduration",
    "jsonpointer>1.13",
    "rfc3339-validator",
    "rfc3986-validator>0.1.0",
    "rfc3987-syntax>=1.1.0",
    "uri-template",
    "webcolors>=24.6.0",
    "python-calamine",
    "openpyxl",
    "pymupdf",
    "python-docx",
    "python-pptx",
)


async def setup(verbose: bool = True) -> dict[str, Any]:
    """Install oold and its validation dependencies into the running kernel.

    Returns ``{"oold": version, "python": version, "installed": [...], "failed": [...]}``.
    Outside Pyodide there is no ``piplite`` and nothing is installed; whatever the interpreter
    already has is reported instead, so the same module works in a desktop checkout.
    """
    report: dict[str, Any] = {"installed": [], "failed": [], "python": sys.version.split()[0]}

    try:
        import piplite
    except ImportError:
        piplite = None

    if piplite is not None:
        # Pyodide ships typing-extensions 4.11.0 and micropip refuses to resolve a >=4.14.0
        # requirement against an already-imported older version, so it goes first and the
        # stale module object is dropped.
        await piplite.install("typing-extensions>=4.14.0")
        sys.modules.pop("typing_extensions", None)
        import typing_extensions  # noqa: F401

        # pyodide-http ships with the Pyodide distribution rather than sitting in
        # site-packages, and the kernel only loads a distribution package when the literal
        # import appears in the cell source it is about to run. An import inside a module on
        # the contents drive is invisible to that scan, so it is installed like any other
        # requirement instead.
        for requirement in ("pyodide-http", "oold", *REQUIREMENTS):
            try:
                await piplite.install(requirement, keep_going=True)
                report["installed"].append(requirement)
            except Exception as exc:
                report["failed"].append(f"{requirement}: {type(exc).__name__}: {exc}")

    # Covers requests and urllib, never httpx. Nothing here fetches on purpose - every wrapper
    # runs offline - but a schema pasted in by the user may still carry a remote $ref.
    try:
        import pyodide_http

        pyodide_http.patch_all()
        report["pyodide_http"] = True
    except ImportError as exc:
        report["pyodide_http"] = False
        report["failed"].append(f"pyodide-http: {exc}")

    import oold

    report["oold"] = oold.__version__
    report["oold_path"] = str(oold.__file__)
    try:
        import oold.validation  # noqa: F401

        report["validation"] = True
    except ImportError as exc:
        report["validation"] = False
        report["failed"].append(f"oold.validation: {exc}")

    if verbose:
        print(f"oold {report['oold']} on python {report['python']}")
        print(f"validation extra: {'ready' if report['validation'] else 'MISSING'}")
        if report["failed"]:
            for line in report["failed"]:
                print(f"failed: {line}")
    return report


def _validation():
    """The ``oold.validation`` package, or an actionable error if setup has not run."""
    try:
        from oold import validation
    except ImportError as exc:
        raise RuntimeError(SETUP_HINT) from exc
    return validation


# ---------------------------------------------------------------------------- command line

#: Commands of the oold CLI that accept ``--offline``. The kernel reaches only the origin it
#: was served from, so a run that is allowed to fetch stalls on a connection it cannot make.
_OFFLINE_COMMANDS = frozenset({"validate", "validate-instance", "compliance", "rules"})

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def oold_cli(*args: Any, offline: bool = True) -> str:
    """Run ``oold <args>`` in-process and return everything it printed.

    Emscripten has no subprocess, so ``!oold validate ...`` does not work; this drives the same
    click application the console script binds to. stdout and stderr are captured together,
    colour codes are stripped, and the exit code is appended as a final line. ``SystemExit``
    never escapes: the CLI ends every command with one.

    ``--offline`` is appended for the commands that take it unless the caller already passed
    it, because the only reachable host is the one serving the site.
    """
    argv = [str(a) for a in args]
    if offline and "--offline" not in argv and argv[:1] and argv[0] in _OFFLINE_COMMANDS:
        argv.append("--offline")

    from oold.validation.cli import main as cli

    buffer = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        try:
            cli(argv, prog_name="oold", standalone_mode=False)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except Exception as exc:
            show = getattr(exc, "show", None)
            if callable(show):
                show(file=buffer)
                code = getattr(exc, "exit_code", 1)
            else:
                print(f"{type(exc).__name__}: {exc}", file=buffer)
                code = 1

    text = _ANSI.sub("", buffer.getvalue())
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}exit code {code}\n"


# ---------------------------------------------------------------------------- validation

def schema_filename(schema: Any) -> str:
    """The filename a schema expects to be addressed by, taken from its ``$id``."""
    identifier = (schema or {}).get("$id") if isinstance(schema, dict) else None
    if isinstance(identifier, str):
        name = identifier.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".schema.json"):
            return name
    return "Document.schema.json"


def _local_ref(value: Any, schema_file: str) -> Any:
    """Point a document's ``@context`` / ``$schema`` at the local copy of its schema.

    An instance drafted against a published schema names it by URL. The validator runs offline,
    so the reference is redirected to the file written beside it. A context given inline as an
    object is left alone: it needs no resolving.
    """
    if isinstance(value, str):
        return schema_file
    if isinstance(value, list):
        return [_local_ref(item, schema_file) for item in value]
    return value


@contextlib.contextmanager
def _staged(members: Iterable[tuple[str, Any]]) -> Iterator[Path]:
    """Write ``(filename, document)`` pairs into one temporary directory and yield it.

    The validation API reads from disk, and it reads more than the file it was handed:
    :func:`validate_schema` globs the sibling ``*.schema.json`` because a reference cycle is a
    property of the graph rather than of one document, and an explicit schema has to sit in the
    same directory as its instance for relative ``@context`` references to resolve. So a
    document that exists only in memory needs a directory laid out the way the pipeline expects
    to find one. This works unchanged under Emscripten, whose filesystem is in memory anyway.
    """
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        for name, document in members:
            (base / name).write_text(json.dumps(document), encoding="utf-8")
        yield base


def _as_document(source: Any) -> dict[str, Any] | None:
    """A JSON document from a dict, a JSON string, or a path on the drive."""
    if isinstance(source, dict):
        return source
    if isinstance(source, (str, Path)):
        text = str(source)
        if text.lstrip().startswith("{"):
            return json.loads(text)
        return json.loads(Path(text).read_text(encoding="utf-8"))
    return None


def _failed_report(source: str, message: str):
    """A report carrying one fatal error, so a caller never has to catch anything."""
    from oold.validation.report import Report

    report = Report(source=source)
    report.fatal_error = message
    return report


def validate_schema(source: Any, extends: Iterable[dict[str, Any]] = (), offline: bool = True):
    """Validate one OO-LD schema and return its :class:`Report`.

    ``source`` is a path on the drive or a schema already in memory. An in-memory schema is
    staged into a temporary directory together with ``extends``, the schemas it builds on,
    because a ``$ref`` that resolves nowhere is reported as an unresolved reference rather than
    ignored.

    Read the result off the report: ``.passed``, ``.counts``, ``.failures()``, ``.to_dict()``.
    A finding is never raised.
    """
    validation = _validation()
    options = validation.Options(offline=offline)
    try:
        if isinstance(source, (str, Path)) and not str(source).lstrip().startswith("{"):
            return validation.validate_schema(Path(source), options)
        document = _as_document(source)
        if not isinstance(document, dict):
            return _failed_report(str(source), "not a schema document")
        name = schema_filename(document)
        members = [(schema_filename(m), m) for m in extends if isinstance(m, dict)]
        members.append((name, document))
        with _staged(members) as base:
            return validation.validate_schema(base / name, options)
    except Exception as exc:
        return _failed_report(str(source), f"{type(exc).__name__}: {exc}")


def validate_instance(
    source: Any,
    schema: Any = None,
    extends: Iterable[dict[str, Any]] = (),
    offline: bool = True,
):
    """Validate one instance document and return its :class:`Report`.

    Without ``schema`` the instance is validated against whatever its ``$schema`` names,
    resolved relative to the instance. With one, that schema wins.

    Either argument may be a path or a document in memory. As soon as one of them is in memory
    both are staged into a temporary directory, with ``$schema`` and ``@context`` rewritten
    onto the staged file name.
    """
    validation = _validation()
    options = validation.Options(offline=offline)
    try:
        instance_is_path = isinstance(source, (str, Path)) and not str(source).lstrip().startswith("{")
        schema_is_path = isinstance(schema, (str, Path)) and not str(schema).lstrip().startswith("{")

        if instance_is_path and (schema is None or schema_is_path):
            return validation.validate_instance(
                Path(source), Path(schema) if schema is not None else None, options
            )

        document = _as_document(source)
        if not isinstance(document, dict):
            return _failed_report(str(source), "not an instance document")
        schema_document = _as_document(schema) if schema is not None else None
        if schema_document is None:
            return _failed_report(str(source), "an in-memory instance needs an explicit schema")

        schema_name = schema_filename(schema_document)
        members = [(schema_filename(m), m) for m in extends if isinstance(m, dict)]
        members.append((schema_name, schema_document))

        payload = dict(document)
        payload["$schema"] = schema_name
        payload["@context"] = (
            _local_ref(payload["@context"], schema_name) if "@context" in payload else schema_name
        )
        members.append(("Document.instance.json", payload))

        with _staged(members) as base:
            return validation.validate_instance(
                base / "Document.instance.json", base / schema_name, options
            )
    except Exception as exc:
        return _failed_report(str(source), f"{type(exc).__name__}: {exc}")


def validate(target: Any, offline: bool = True):
    """Validate whatever ``target`` is: a schema, an instance, or a whole directory.

    A directory runs every schema and instance it contains. A file is classified the way the
    CLI classifies it: a ``$schema`` naming a meta-schema or a JSON Schema dialect makes it a
    schema, any other ``$schema`` makes it an instance of the document it names.
    """
    validation = _validation()
    options = validation.Options(offline=offline)
    try:
        if isinstance(target, (str, Path)) and not str(target).lstrip().startswith("{"):
            path = Path(target)
            if path.is_dir():
                return validation.validate_directory(path, options)
            if not path.exists():
                return _failed_report(str(path), f"not found: {path}")
            from oold.validation.cli import _classify

            kind = _classify(path)
            if kind == "instance":
                return validation.validate_instance(path, None, options)
            if kind == "unknown":
                return _failed_report(
                    str(path),
                    f"{path.name} has no $schema, so it cannot be classified: "
                    "call validate_schema or validate_instance directly",
                )
            return validation.validate_schema(path, options)

        document = _as_document(target)
        if not isinstance(document, dict):
            return _failed_report(str(target), "not a JSON document")
        declared = document.get("$schema")
        if isinstance(declared, str) and declared.endswith(".schema.json"):
            return _failed_report(
                str(target), "an in-memory instance needs an explicit schema: use validate_instance"
            )
        return validate_schema(document, offline=offline)
    except Exception as exc:
        return _failed_report(str(target), f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------- profiling

#: A value counted as missing. Nothing else is: "NA" and "-" mean different things in different
#: files and guessing at them would hide data the agent has to see.
_NULL = ""

_INTEGER = re.compile(r"^[+-]?\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([.,]\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_IRI = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*://|urn:|doi:|mailto:)\S+$")
_BOOLEAN = frozenset({"true", "false", "yes", "no"})

#: Names that claim to identify a row. Matched against the whole name or its last underscored
#: segment, so both ``id`` and ``measurement_id`` qualify while ``valid`` does not.
_ID_NAME = re.compile(r"(?:^|[_\-\s])(id|iri|uri|url|uuid|key|code|identifier)$", re.IGNORECASE)


def _infer_type(values: list[str]) -> str:
    """The narrowest type every non-empty value in a column fits."""
    if not values:
        return "empty"
    if all(v.lower() in _BOOLEAN for v in values):
        return "boolean"
    if all(_INTEGER.match(v) for v in values):
        return "integer"
    if all(_DATE.match(v) for v in values):
        return "date"
    if all(_DATETIME.match(v) for v in values):
        return "datetime"
    if all(_IRI.match(v) for v in values):
        return "iri"
    try:
        for value in values:
            float(value)
        return "number"
    except ValueError:
        return "string"


def _duplicate_names(header: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    for name in header:
        seen[name] = seen.get(name, 0) + 1
    return sorted(name for name, count in seen.items() if count > 1)


def _column_stats(header: list[str], body: list[list[str]], examples: int) -> list[dict[str, Any]]:
    """One description per column, from a header and a body already rendered as text.

    Shared by every profiler so a CSV and a sheet describe themselves the same way; the
    agent is taught one shape and reads it whatever the file was.
    """
    columns: list[dict[str, Any]] = []
    for index, name in enumerate(header):
        cells = [row[index].strip() if index < len(row) else _NULL for row in body]
        present = [value for value in cells if value != _NULL]
        distinct = sorted(set(present))
        columns.append(
            {
                "name": name,
                "type": _infer_type(present),
                "examples": distinct[:examples],
                "nulls": len(cells) - len(present),
                "distinct": len(distinct),
                "unique": bool(present) and len(distinct) == len(cells),
            }
        )
    return columns


def _header_names(cells: list[str]) -> tuple[list[str], list[int]]:
    """``(names, positions that had none)`` for a header row.

    An empty header cell is named by its position rather than dropped, so a sheet or a
    table with a merged title row above the real header still profiles.
    """
    header: list[str] = []
    blank: list[int] = []
    for index, cell in enumerate(cells):
        name = cell.strip()
        if not name:
            blank.append(index + 1)
            name = f"column_{index + 1}"
        header.append(name)
    return header, blank


def _blank_header_problem(header: list[str], blank: list[int]) -> str:
    positions = ", ".join(str(position) for position in blank)
    return (
        f"{len(blank)} of {len(header)} header cells are empty, at column {positions}: "
        f"a merged or title row above the real header reads this way"
    )


def _guess_identifier(columns: list[dict[str, Any]], rows: int) -> str | None:
    """Which column looks like the row identifier.

    Uniqueness and completeness are the requirement; the name only breaks a tie. A timestamp
    that happens to be unique is not an identifier, and in a narrow table it is often the
    leftmost unique column, so picking by position alone gets it wrong.
    """
    if not rows:
        return None
    unique = [c for c in columns if c["nulls"] == 0 and c["distinct"] == rows]
    if not unique:
        return None
    for column in unique:
        if _ID_NAME.search(column["name"]):
            return column["name"]
    return unique[0]["name"]


def csv_profile(path: Any, examples: int = 3, max_rows: int = 20000) -> dict[str, Any]:
    """Describe a CSV well enough to propose terms for it.

    Returns ``{"path", "ok", "delimiter", "rows", "columns", "identifier", "problems"}``. Each
    column carries its name, inferred type, example values, how many rows are empty, how many
    distinct values it holds, and whether it is unique.

    Nothing raises: a file that cannot be read or does not parse comes back with ``ok`` false
    and the reason in ``problems``, because an agent that gets a traceback cannot tell the user
    what is wrong with their data.
    """
    profile: dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "delimiter": ",",
        "rows": 0,
        "columns": [],
        "identifier": None,
        "problems": [],
    }
    problems: list[str] = profile["problems"]

    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        problems.append(f"could not be read: {exc}")
        return profile
    except UnicodeDecodeError as exc:
        problems.append(f"not UTF-8: {exc}")
        return profile

    if not text.strip():
        problems.append("file is empty")
        return profile

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    profile["delimiter"] = delimiter

    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        problems.append(f"could not be parsed: {exc}")
        return profile

    if not rows:
        problems.append("file is empty")
        return profile

    header = [name.strip() for name in rows[0]]
    if not any(header):
        problems.append("header row is empty")
        return profile

    duplicates = _duplicate_names(header)
    if duplicates:
        problems.append(f"duplicate column name(s): {', '.join(duplicates)}")

    body = rows[1 : 1 + max_rows]
    if len(rows) - 1 > max_rows:
        problems.append(f"only the first {max_rows} of {len(rows) - 1} rows were read")

    ragged = [index + 2 for index, row in enumerate(body) if len(row) != len(header)]
    if ragged:
        shown = ", ".join(str(line) for line in ragged[:5])
        more = f" (+{len(ragged) - 5} more)" if len(ragged) > 5 else ""
        problems.append(f"{len(ragged)} row(s) do not have {len(header)} fields: line {shown}{more}")

    profile["rows"] = len(body)
    columns = _column_stats(header, body, examples)
    profile["columns"] = columns
    profile["identifier"] = _guess_identifier(columns, len(body))
    profile["ok"] = not problems
    return profile


# ------------------------------------------------------------------------ spreadsheets


def _workbook(path: Any):
    """The calamine workbook for ``path``.

    The import is deferred because this module has to be importable before :func:`setup`
    has installed anything.
    """
    from python_calamine import CalamineWorkbook

    return CalamineWorkbook.from_path(str(path))


def _open_workbook(path: Any, problems: list[str]):
    """``(workbook, sheet_names)``, or ``(None, [])`` with the reason in ``problems``."""
    try:
        workbook = _workbook(path)
        return workbook, list(workbook.sheet_names)
    except ImportError:
        problems.append(CALAMINE_HINT)
    except Exception as exc:
        # calamine raises its own CalamineError subclasses for a file that is not the
        # format its suffix claims, and OSError for one that is not there. Neither type
        # is importable before setup has run, so the contract is kept by type name.
        problems.append(f"could not be read: {type(exc).__name__}: {exc}")
    return None, []


def sheets(path: Any) -> dict[str, Any]:
    """Sheet names of a workbook, in workbook order.

    Returns ``{"path", "ok", "sheets", "problems"}`` and never raises, so the answer to
    "which sheet" can be asked before anything is read.
    """
    profile: dict[str, Any] = {"path": str(path), "ok": False, "sheets": [], "problems": []}
    problems: list[str] = profile["problems"]

    _, names = _open_workbook(path, problems)
    if problems:
        return profile
    profile["sheets"] = names
    if not names:
        problems.append("workbook has no sheets")
        return profile
    profile["ok"] = True
    return profile


def _cell_text(value: Any) -> str:
    """A spreadsheet cell as the text the type inference reads.

    xlsx has one numeric type, a double, so a whole number arrives as ``1.0`` where the
    sheet shows ``1``. It is rendered as the sheet shows it, which costs a column of whole
    measurements the ``number`` type and gains every counter and year column ``integer``.
    """
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e16 else str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    return str(value).strip()


def _pick_sheet(sheet: Any, names: list[str], problems: list[str]) -> str | None:
    """Which sheet to profile, with the choice stated rather than taken silently."""
    if sheet is None:
        chosen = names[0]
        if len(names) > 1:
            problems.append(
                f"workbook has {len(names)} sheets ({', '.join(names)}); profiled "
                f"{chosen!r}. Pass sheet= to profile another."
            )
        return chosen
    if isinstance(sheet, int) and not isinstance(sheet, bool):
        if -len(names) <= sheet < len(names):
            return names[sheet]
        problems.append(f"no sheet at index {sheet}: the workbook has {len(names)}")
        return None
    if sheet in names:
        return str(sheet)
    problems.append(f"no sheet named {sheet!r}: the workbook has {', '.join(names)}")
    return None


def xlsx_profile(
    path: Any, sheet: Any = None, examples: int = 3, max_rows: int = 20000
) -> dict[str, Any]:
    """Describe one sheet of a spreadsheet well enough to propose terms for it.

    Returns what :func:`csv_profile` returns, minus ``delimiter`` and plus ``sheet`` and
    ``sheets``, so an agent reads one shape whatever the file was. ``sheet`` takes a name
    or an index; without it the first sheet is profiled and a workbook with more than one
    says so in ``problems``.

    Reading is done by ``python-calamine``: xlsx, xlsm and xlsb are the OOXML family, ods
    is OpenDocument, and legacy xls opens through its CFB parser. Nothing raises: a file
    that is not the format its suffix claims comes back with ``ok`` false and the reason
    in ``problems``.
    """
    profile: dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "sheet": None,
        "sheets": [],
        "rows": 0,
        "columns": [],
        "identifier": None,
        "problems": [],
    }
    problems: list[str] = profile["problems"]

    workbook, names = _open_workbook(path, problems)
    if workbook is None:
        return profile
    profile["sheets"] = names
    if not names:
        problems.append("workbook has no sheets")
        return profile

    chosen = _pick_sheet(sheet, names, problems)
    if chosen is None:
        return profile
    profile["sheet"] = chosen

    try:
        grid = workbook.get_sheet_by_name(chosen).to_python()
    except Exception as exc:
        problems.append(f"sheet {chosen!r} could not be read: {type(exc).__name__}: {exc}")
        return profile

    rows = [[_cell_text(cell) for cell in row] for row in grid]
    # A sheet carries its used range, and a cleared row stays in it.
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        problems.append(f"sheet {chosen!r} is empty")
        return profile

    header, blank = _header_names(rows[0])
    if len(blank) == len(header):
        problems.append(f"header row of sheet {chosen!r} is empty")
        return profile
    if blank:
        problems.append(_blank_header_problem(header, blank))

    duplicates = _duplicate_names(header)
    if duplicates:
        problems.append(f"duplicate column name(s): {', '.join(duplicates)}")

    body = rows[1 : 1 + max_rows]
    if len(rows) - 1 > max_rows:
        problems.append(f"only the first {max_rows} of {len(rows) - 1} rows were read")

    profile["rows"] = len(body)
    columns = _column_stats(header, body, examples)
    profile["columns"] = columns
    profile["identifier"] = _guess_identifier(columns, len(body))
    profile["ok"] = not problems
    return profile


# --------------------------------------------------------------------------- documents

#: First bytes that say what a file is. The suffix is checked against this and never
#: trusted for it: a report arrives named .doc as often as .docx, and a renamed file
#: handed to the wrong reader comes back as a parse error instead of its content.
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"

#: The part inside an OOXML zip that names the format. Spreadsheets are listed so one
#: can be turned away with the function that does read it rather than a parse error.
_OOXML_PARTS = (
    ("word/document.xml", "docx"),
    ("ppt/presentation.xml", "pptx"),
    ("xl/workbook.xml", "xlsx"),
)

_WHITESPACE = re.compile(r"\s+")


def _cell_line(value: Any) -> str:
    """A table cell as a single line.

    A pdf cell carries the line breaks of its layout, and a docx cell can carry several
    paragraphs. Either way the cell holds one value and is read as one.
    """
    return _WHITESPACE.sub(" ", _cell_text(value)).strip()


def _readable(text: Any) -> str:
    """Text as a reader sees it: no trailing space on a line, no run of blank ones."""
    out: list[str] = []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _ooxml_kind(path: Any, problems: list[str]) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except Exception as exc:
        problems.append(f"could not be read: {type(exc).__name__}: {exc}")
        return None
    for part, kind in _OOXML_PARTS:
        if part in names:
            return kind
    problems.append("zip archive with no word/, ppt/ or xl/ part: not an Office document")
    return None


def _document_format(path: Any, problems: list[str]) -> str | None:
    """Which of docx, pptx and pdf a file is, decided by its bytes."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError as exc:
        problems.append(f"could not be read: {exc}")
        return None

    if head.startswith(_PDF_MAGIC):
        found = "pdf"
    elif head.startswith(_ZIP_MAGIC):
        found = _ooxml_kind(path, problems)
        if found is None:
            return None
    else:
        problems.append(f"not a docx, pptx or pdf: the file starts with {head[:4]!r}")
        return None

    if found == "xlsx":
        problems.append("this is a spreadsheet, not a document: read it with xlsx_profile or sheets")
        return None

    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix != found:
        problems.append(f"suffix says {suffix or 'nothing'} and the bytes say {found}; read as {found}")
    return found


def _docx_content(path: Any, problems: list[str]):
    import docx

    document = docx.Document(str(path))
    blocks: list[dict[str, str]] = []
    headings: list[str] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = _readable(paragraph.text)
        if not text:
            continue
        blocks.append({"where": f"paragraph {index}", "text": text})
        style = getattr(paragraph.style, "name", "") or ""
        if style.startswith("Heading") or style == "Title":
            headings.append(text)
    grids = [
        (f"table {index}", [[_cell_line(cell.text) for cell in row.cells] for row in table.rows])
        for index, table in enumerate(document.tables, start=1)
    ]
    return grids, blocks, headings


def _pptx_content(path: Any, problems: list[str]):
    import pptx

    presentation = pptx.Presentation(str(path))
    grids: list[tuple[str, list[list[str]]]] = []
    blocks: list[dict[str, str]] = []
    headings: list[str] = []
    for number, slide in enumerate(presentation.slides, start=1):
        where = f"slide {number}"
        title = slide.shapes.title
        if title is not None and title.has_text_frame:
            heading = _readable(title.text_frame.text)
            if heading:
                headings.append(heading)
        spoken: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = _readable(shape.text_frame.text)
                if text:
                    spoken.append(text)
            if getattr(shape, "has_table", False):
                grids.append(
                    (where, [[_cell_line(cell.text) for cell in row.cells] for row in shape.table.rows])
                )
        if spoken:
            blocks.append({"where": where, "text": "\n".join(spoken)})
    return grids, blocks, headings


def _pdf_content(path: Any, problems: list[str]):
    import pymupdf

    grids: list[tuple[str, list[list[str]]]] = []
    blocks: list[dict[str, str]] = []
    headings: list[str] = []
    with pymupdf.open(str(path)) as document:
        try:
            headings = [str(entry[1]) for entry in document.get_toc()]
        except Exception as exc:
            problems.append(f"outline could not be read: {type(exc).__name__}: {exc}")
        for number, page in enumerate(document, start=1):
            where = f"page {number}"
            text = _readable(page.get_text())
            if text:
                blocks.append({"where": where, "text": text})
            try:
                found = page.find_tables()
            except Exception as exc:
                problems.append(f"{where}: tables could not be located: {type(exc).__name__}: {exc}")
                continue
            for table in found.tables:
                grids.append((where, [[_cell_line(cell) for cell in row] for row in table.extract()]))
    return grids, blocks, headings


#: One reader per format, each ``(path, problems) -> (grids, blocks, headings)``. Only the
#: pdf reader has a partial failure to report: an outline or a page of tables can be
#: unreadable while the rest of the document is fine. The other two read or raise.
_DOCUMENT_READERS = {"docx": _docx_content, "pptx": _pptx_content, "pdf": _pdf_content}


def _read_document(path: Any, problems: list[str]):
    """``(format, (grids, blocks, headings))``, with ``None`` content if nothing was read.

    The format is reported even when reading fails, because "the pdf is damaged" and
    "that file is not a pdf" are different answers for the caller.
    """
    found = _document_format(path, problems)
    if found is None:
        return None, None
    try:
        return found, _DOCUMENT_READERS[found](path, problems)
    except ImportError:
        problems.append(DOCUMENT_HINT)
    except Exception as exc:
        problems.append(f"could not be read: {type(exc).__name__}: {exc}")
    return found, None


def _table_entries(grids: list[tuple[str, list[list[str]]]]) -> list[dict[str, Any]]:
    """One line per table: enough to choose between them without profiling all of them."""
    return [
        {
            "index": index,
            "where": where,
            "rows": max(len(grid) - 1, 0),
            "columns": len(grid[0]) if grid else 0,
        }
        for index, (where, grid) in enumerate(grids)
    ]


def _pick_table(table: Any, entries: list[dict[str, Any]], problems: list[str]) -> int | None:
    """Which table to profile, with the choice stated rather than taken silently."""
    if table is None:
        if len(entries) > 1:
            listed = ", ".join(f"{entry['index']} at {entry['where']}" for entry in entries)
            problems.append(
                f"document has {len(entries)} tables ({listed}); profiled table 0. "
                f"Pass table= to profile another."
            )
        return 0
    if isinstance(table, int) and not isinstance(table, bool):
        if -len(entries) <= table < len(entries):
            return table % len(entries)
        problems.append(f"no table at index {table}: the document has {len(entries)}")
        return None
    problems.append(f"table= takes an index, not {table!r}: the document has {len(entries)} tables")
    return None


def tables(path: Any) -> dict[str, Any]:
    """Tables of a docx, a pptx or a pdf, in reading order.

    Returns ``{"path", "ok", "format", "tables", "problems"}``. Each entry carries its
    ``index``, ``where`` it sits (``table 2``, ``slide 3``, ``page 2``) and its ``rows``
    and ``columns`` counts, so "which table" can be answered before one is profiled.
    Never raises; this is the document counterpart of :func:`sheets`.
    """
    profile: dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "format": None,
        "tables": [],
        "problems": [],
    }
    problems: list[str] = profile["problems"]

    found, content = _read_document(path, problems)
    profile["format"] = found
    if content is None:
        return profile

    grids, _, _ = content
    profile["tables"] = _table_entries(grids)
    if not grids:
        problems.append("document has no tables; its text is in doc_text")
        return profile
    profile["ok"] = not problems
    return profile


def doc_profile(
    path: Any, table: Any = None, examples: int = 3, max_rows: int = 20000
) -> dict[str, Any]:
    """Describe one table of a document well enough to propose terms for it.

    Returns what :func:`csv_profile` returns, minus ``delimiter`` and plus ``format``,
    ``table``, ``where`` and ``tables``, so a table in a report profiles into the shape
    the agent already reads for a CSV and a sheet.

    ``table`` takes an index into :func:`tables`; a document has no names to address its
    tables by. Without it the first is profiled and a document with more than one says so
    in ``problems``. Nothing raises: a file that is not the format its suffix claims comes
    back with ``ok`` false and the reason in ``problems``.
    """
    profile: dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "format": None,
        "table": None,
        "where": None,
        "tables": [],
        "rows": 0,
        "columns": [],
        "identifier": None,
        "problems": [],
    }
    problems: list[str] = profile["problems"]

    found, content = _read_document(path, problems)
    profile["format"] = found
    if content is None:
        return profile

    grids, _, _ = content
    entries = _table_entries(grids)
    profile["tables"] = entries
    if not entries:
        problems.append("document has no tables; its text is in doc_text")
        return profile

    chosen = _pick_table(table, entries, problems)
    if chosen is None:
        return profile
    where, grid = grids[chosen]
    profile["table"] = chosen
    profile["where"] = where

    rows = list(grid)
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        problems.append(f"table {chosen} at {where} is empty")
        return profile

    header, blank = _header_names(rows[0])
    if len(blank) == len(header):
        problems.append(f"header row of table {chosen} at {where} is empty")
        return profile
    if blank:
        problems.append(_blank_header_problem(header, blank))

    duplicates = _duplicate_names(header)
    if duplicates:
        problems.append(f"duplicate column name(s): {', '.join(duplicates)}")

    body = rows[1 : 1 + max_rows]
    if len(rows) - 1 > max_rows:
        problems.append(f"only the first {max_rows} of {len(rows) - 1} rows were read")

    ragged = [index + 2 for index, row in enumerate(body) if len(row) != len(header)]
    if ragged:
        shown = ", ".join(str(position) for position in ragged[:5])
        more = f" (+{len(ragged) - 5} more)" if len(ragged) > 5 else ""
        problems.append(f"{len(ragged)} row(s) do not have {len(header)} cells: row {shown}{more}")

    profile["rows"] = len(body)
    columns = _column_stats(header, body, examples)
    profile["columns"] = columns
    profile["identifier"] = _guess_identifier(columns, len(body))
    profile["ok"] = not problems
    return profile


def doc_text(path: Any, max_chars: int = 20000) -> dict[str, Any]:
    """Readable text of a docx, a pptx or a pdf.

    Returns ``{"path", "ok", "format", "headings", "blocks", "text", "chars", "problems"}``.
    ``headings`` is the outline: docx heading paragraphs, pptx slide titles, the pdf
    bookmark tree. ``blocks`` carries the text per unit, each with ``where`` it came from,
    and ``text`` is those blocks joined. ``chars`` is the length of the whole document even
    when ``max_chars`` cut it short.

    What a column means is usually written next to the table rather than in it, so this is
    the half of an extraction that :func:`doc_profile` does not answer. Never raises.
    """
    profile: dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "format": None,
        "headings": [],
        "blocks": [],
        "text": "",
        "chars": 0,
        "problems": [],
    }
    problems: list[str] = profile["problems"]

    found, content = _read_document(path, problems)
    profile["format"] = found
    if content is None:
        return profile

    _, blocks, headings = content
    profile["headings"] = headings
    profile["chars"] = sum(len(block["text"]) for block in blocks) + max(len(blocks) - 1, 0) * 2

    kept: list[dict[str, str]] = []
    budget = max_chars
    for block in blocks:
        if len(block["text"]) > budget:
            break
        kept.append(block)
        budget -= len(block["text"]) + 2
    profile["blocks"] = kept
    profile["text"] = "\n\n".join(block["text"] for block in kept)

    if not blocks:
        problems.append("document has no text; its tables are in tables and doc_profile")
        return profile
    if len(kept) < len(blocks):
        problems.append(
            f"only {len(kept)} of {len(blocks)} blocks were read: the document is "
            f"{profile['chars']} characters and max_chars is {max_chars}"
        )
    profile["ok"] = not problems
    return profile


# ---------------------------------------------------------------------------- mappings

#: The SKOS predicate that makes a synonym interchangeable with the primary term. Other
#: predicates (``closeMatch``, ``broadMatch``, ...) record a weaker relation and are never
#: promoted or rewritten automatically.
SKOS_EXACT_MATCH = "http://www.w3.org/2004/02/skos/core#exactMatch"

_EXACT_MATCH_FORMS = frozenset({"skos:exactMatch", SKOS_EXACT_MATCH})


def set_name(iri: str) -> str:
    """Short name of a mapping set, from its identifier."""
    return iri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".sssom.tsv")


def context_of(schema: dict[str, Any]) -> dict[str, Any]:
    """The inline term definitions of a schema, with a list ``@context`` merged in order."""
    ctx = schema.get("@context")
    if isinstance(ctx, list):
        merged: dict[str, Any] = {}
        for part in ctx:
            if isinstance(part, dict):
                merged.update(part)
        return merged
    return ctx if isinstance(ctx, dict) else {}


def declared_context(schemas: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The consensus context of a chain: every schema's inline terms, base first."""
    merged: dict[str, Any] = {}
    for schema in schemas:
        merged.update(context_of(schema))
    return merged


def _set_ids(fragment: dict[str, Any]) -> list[str]:
    """The mapping sets one synonym entry belongs to (the slot may hold a list)."""
    raw = (fragment.get("x-oold-sssom") or {}).get("mapping_set_id")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return []


def is_exact_match(fragment: dict[str, Any]) -> bool:
    """Whether a synonym entry is an ``exactMatch``, which is the default when unstated."""
    predicate = (fragment.get("x-oold-sssom") or {}).get("predicate_id")
    return predicate is None or predicate in _EXACT_MATCH_FORMS


def synonyms_of(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``x-oold-context`` block of one schema, with non-dict entries dropped.

    A ``null`` entry removes an inherited mapping under composition, so it is not a synonym to
    read; it is the absence of one.
    """
    out: dict[str, dict[str, Any]] = {}
    for term, entries in (schema.get("x-oold-context") or {}).items():
        for iri, fragment in (entries or {}).items():
            if isinstance(fragment, dict):
                out.setdefault(term, {})[iri] = fragment
    return out


def mapping_sets(schemas: Iterable[dict[str, Any]]) -> list[str]:
    """Every mapping set the given schemas declare a synonym in."""
    found: set[str] = set()
    for schema in schemas:
        for entries in synonyms_of(schema).values():
            for fragment in entries.values():
                found.update(_set_ids(fragment))
    return sorted(found)


def promote(
    base_ctx: dict[str, Any], schemas: Iterable[dict[str, Any]], set_id: str | None
) -> dict[str, Any]:
    """The effective ``@context`` for a mapping set.

    Promotes the synonyms tagged with ``set_id`` and leaves every other term on its consensus
    mapping. With no set selected this is the consensus context unchanged.
    """
    ctx = dict(base_ctx)
    if not set_id:
        return ctx
    for schema in schemas:
        for term, entries in synonyms_of(schema).items():
            for iri, fragment in entries.items():
                if set_id not in _set_ids(fragment):
                    continue
                rest = {k: v for k, v in fragment.items() if k != "x-oold-sssom"}
                if "@reverse" in rest:
                    # The entry is keyed by its IRI either way, but a reverse term carries it in
                    # ``@reverse``; adding ``@id`` as well would not be a valid term definition.
                    ctx[term] = {**rest, "@reverse": iri}
                elif rest:
                    ctx[term] = {"@id": iri, **rest}
                else:
                    primary = ctx.get(term)
                    if isinstance(primary, dict):
                        # Keep the primary's type coercion and container; only the IRI moves.
                        inherited = {k: v for k, v in primary.items() if k != "@id"}
                        ctx[term] = {"@id": iri, **inherited} if inherited else iri
                    else:
                        ctx[term] = iri
                break
    return ctx


def synonym_entries(schemas: Iterable[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """``(synonym_iri, term, fragment)`` for every ``exactMatch`` synonym in the chain.

    Used for import, where the incoming graph may be written in any of the mapped vocabularies -
    possibly several at once - so a single promoted context cannot express what is wanted. The
    caller expands both sides and rewrites the graph onto the primary IRIs instead.
    Non-``exactMatch`` entries are excluded: a ``closeMatch`` is not a licence to treat two
    predicates as the same.

    The fragment is handed back because a synonym may invert the relation (``@reverse``), which
    is a different rewrite from swapping one predicate for another.
    """
    entries: list[tuple[str, str, dict[str, Any]]] = []
    for schema in schemas:
        for term, synonyms in synonyms_of(schema).items():
            for iri, fragment in synonyms.items():
                if is_exact_match(fragment):
                    entries.append((iri, term, fragment))
    return entries


def chain(
    schema: dict[str, Any],
    resolve: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """A schema's inheritance chain, base first, by following ``allOf`` ``$ref``.

    The mappings need the whole chain: a subschema inherits the terms and the synonyms of
    everything it extends, and reading its own context alone produces an empty graph.

    ``resolve`` turns a ``$ref`` into a schema document and may return ``None`` for one it
    cannot reach, in which case that branch is skipped rather than raising.
    """
    out: list[dict[str, Any]] = []
    # Guarded by $id and by the $ref string, not by object identity: a resolver that fetches
    # returns a fresh dict per call, so a cyclic chain (A extends B extends A) would never
    # revisit the same object and would recurse without bound.
    seen: set[Any] = set()
    seen_refs: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        key = node.get("$id") if isinstance(node.get("$id"), str) else id(node)
        if key in seen:
            return
        seen.add(key)
        for entry in node.get("allOf") or []:
            if not isinstance(entry, dict):
                continue
            ref = entry.get("$ref")
            if isinstance(ref, str) and resolve is not None:
                if ref in seen_refs:
                    continue
                seen_refs.add(ref)
                parent = resolve(ref)
                if parent is not None:
                    walk(parent)
            elif ref is None:
                # An inline allOf branch carries terms of its own.
                walk(entry)
        out.append(node)

    walk(schema)
    return out


# ---------------------------------------------------------------------------- rdf transform

def _as_schemas(schemas: Any) -> list[dict[str, Any]]:
    """A schema chain from one schema, a list of them, or paths to them."""
    if schemas is None:
        return []
    if isinstance(schemas, (dict, str, Path)):
        schemas = [schemas]
    out: list[dict[str, Any]] = []
    for member in schemas:
        document = _as_document(member)
        if isinstance(document, dict):
            out.append(document)
    return out


def _expand_iri(context: Any, value: str) -> str | None:
    """The absolute IRI ``value`` denotes under ``context``.

    Terms and CURIEs are resolved by expanding a probe document, so prefix definitions and
    aliases in the context are honoured rather than reimplemented here. An absolute IRI passes
    through unchanged.
    """
    from pyld import jsonld

    if not isinstance(value, str) or not value:
        return None
    try:
        expanded = jsonld.expand({"@context": context, value: "probe"})
    except Exception:
        return None
    if not expanded:
        return None
    for key in expanded[0]:
        if not key.startswith("@"):
            return key
    return None


def _rewrite_map(
    schemas: Iterable[dict[str, Any]], context: Any
) -> tuple[dict[str, str], dict[str, str]]:
    """Synonym-to-primary IRI rewrites, split by direction.

    Returns ``(swap, invert)``: ``swap`` replaces a predicate in place, ``invert`` does the same
    but also exchanges subject and object, because a ``@reverse`` synonym states the relation
    the other way round (``person worksFor org`` is ``org employs person``).

    An entry is dropped when either side does not expand to an absolute IRI: a term the context
    never defines cannot be rewritten onto anything meaningful.
    """
    swap: dict[str, str] = {}
    invert: dict[str, str] = {}
    for synonym, term, fragment in synonym_entries(schemas):
        source = _expand_iri(context, synonym)
        target = _expand_iri(context, term)
        if not source or not target:
            continue
        if "@reverse" in fragment:
            invert[source] = target
        elif source != target:
            swap[source] = target
    return swap, invert


def _dataset(data: str, format: str):
    """Parse serialized RDF into an rdflib dataset."""
    from rdflib import Dataset

    dataset = Dataset()
    dataset.parse(data=data, format=format)
    return dataset


def _apply_rewrites(nquads: str, swap: dict[str, str], invert: dict[str, str]) -> str:
    """Rewrite synonym predicates, inverted relations and ``rdf:type`` objects.

    Done in RDF rather than by choosing a context, because a graph may use several mapped
    vocabularies at once and a single context can only give each term one reading.
    """
    if not swap and not invert:
        return nquads

    from rdflib import URIRef

    dataset = _dataset(nquads, "nquads")
    forward = {URIRef(k): URIRef(v) for k, v in swap.items()}
    reverse = {URIRef(k): URIRef(v) for k, v in invert.items()}
    rdf_type = URIRef(_RDF_TYPE)

    for graph_ in list(dataset.graphs()):
        for subject, predicate, obj in list(graph_):
            if predicate in reverse:
                graph_.remove((subject, predicate, obj))
                graph_.add((obj, reverse[predicate], subject))
                continue
            new_predicate = forward.get(predicate, predicate)
            # A class synonym appears as the object of rdf:type, not as a predicate.
            new_object = forward.get(obj, obj) if predicate == rdf_type else obj
            if new_predicate == predicate and new_object == obj:
                continue
            graph_.remove((subject, predicate, obj))
            graph_.add((subject, new_predicate, new_object))

    return dataset.serialize(format="nquads")


def _strip_blank_ids(node: Any, id_keys: frozenset[str]) -> Any:
    """Drop blank-node identifiers introduced by the RDF round-trip.

    A document that named nothing comes back carrying ``_:b0``-style labels, which are an
    artefact of serializing to triples rather than anything the author wrote. Real IRIs are left
    alone, so identity the input actually declared survives.
    """
    if isinstance(node, list):
        return [_strip_blank_ids(item, id_keys) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _strip_blank_ids(value, id_keys)
        for key, value in node.items()
        if not (key in id_keys and isinstance(value, str) and value.startswith("_:"))
    }


def _id_keys(context: Any) -> frozenset[str]:
    """``@id`` and every term aliased to it, which is how a compacted node names itself."""
    keys = {"@id"}
    if isinstance(context, dict):
        keys.update(term for term, value in context.items() if value == "@id")
    return frozenset(keys)


def _property_types(subschema: Any) -> list[str] | None:
    """The declared instance ``rdf:type`` of a property's embedded objects, if any."""
    if not isinstance(subschema, dict):
        return None
    for candidate in (subschema, subschema.get("items")):
        if isinstance(candidate, dict):
            types = candidate.get("x-oold-instance-rdf-type")
            if isinstance(types, list) and types:
                return list(types)
    return None


def _materialize_types(document: dict[str, Any], schemas: list[dict[str, Any]]) -> None:
    """Give untyped nodes the declared ``rdf:type``, nested ones included.

    The same rule the validator's roundtrip applies: ``x-oold-instance-rdf-type`` is
    materialised as ``@type`` unless the node already carries one. Without it a node is
    invisible to any type-filtering frame, so a multi-node ``@graph`` import - or a person
    nested inside an organization - would come back empty rather than re-nested.

    Everything written to is copied first: the document is a shallow copy of the caller's
    instance, and writing ``@type`` into shared nodes would hand the validator a document the
    user never wrote.
    """
    try:
        from oold.validation.frame import instance_rdf_types
    except ImportError:
        return
    if not schemas:
        return
    types = instance_rdf_types(schemas[-1])
    properties: dict[str, Any] = {}
    for member in schemas:
        if isinstance(member, dict):
            properties.update(member.get("properties") or {})

    def typed(node: Any, declared: list[str] | None) -> Any:
        if not isinstance(node, dict):
            return node
        node = dict(node)
        if declared and "type" not in node and "@type" not in node:
            node["@type"] = list(declared)
        for key, value in list(node.items()):
            nested = _property_types(properties.get(key))
            if nested is None:
                continue
            if isinstance(value, list):
                node[key] = [typed(item, nested) for item in value]
            elif isinstance(value, dict):
                node[key] = typed(value, nested)
        return node

    nodes = document.get("@graph")
    if isinstance(nodes, list):
        document["@graph"] = [typed(node, types) for node in nodes]
    else:
        for key, value in typed(document, types).items():
            document[key] = value


def _is_graph_document(document: Any) -> bool:
    """Whether a JSON-LD document is a graph of nodes rather than a single node."""
    if isinstance(document, list):
        return len(document) > 1
    return isinstance(document, dict) and "@graph" in document


def to_rdf(
    instance: Any,
    schemas: Any,
    set_id: str | None = None,
    format: str = TURTLE,
    options: dict[str, Any] | None = None,
) -> str:
    """Export an instance as RDF under the schema chain's effective context.

    ``schemas`` is the inheritance chain, base first (see :func:`chain`); a single schema is
    accepted too. ``set_id`` selects a mapping set; without one the consensus context is used.
    ``format`` is :data:`TURTLE`, :data:`NQUADS` or :data:`JSON_LD`.
    """
    from pyld import jsonld

    document_in = _as_document(instance) or {}
    schemas = _as_schemas(schemas)
    context = promote(declared_context(schemas), schemas, set_id)

    document = {k: v for k, v in document_in.items() if k not in ("@context", "$schema")}
    document["@context"] = context
    _materialize_types(document, schemas)

    nquads = jsonld.to_rdf(document, {**(options or {}), "format": NQUADS})
    if format == NQUADS:
        return nquads
    if format == JSON_LD:
        back = jsonld.from_rdf(nquads, {"format": NQUADS, "useNativeTypes": True})
        return json.dumps(jsonld.compact(back, context), indent=2)
    return _dataset(nquads, "nquads").serialize(format="turtle")


def from_rdf(
    text: Any,
    schemas: Any,
    format: str = TURTLE,
    frame: dict[str, Any] | None = None,
    set_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> Any:
    """Read RDF back as an instance of the given schema chain.

    Every ``exactMatch`` synonym the chain declares is honoured, so the graph may be written in
    any of the mapped vocabularies without naming a mapping set. A graph of several nodes is
    reconstructed by framing - compaction alone never re-nests a flat graph - using ``frame``
    when given, otherwise the frame derived from the most derived schema.

    ``set_id`` is only needed to bridge document *shapes*. Renaming a term is a rewrite of the
    graph and needs no selection, but a promoted fragment may also carry ``@nest``, which
    decides where in the document a value sits rather than what it means. ``@nest`` is a term
    definition and only takes effect when the document is compacted against a context that
    contains it, so a reading that regroups the document has to be named.
    """
    from pyld import jsonld

    schemas = _as_schemas(schemas)
    context = promote(declared_context(schemas), schemas, set_id)

    if isinstance(text, (dict, list)):
        nquads = jsonld.to_rdf(text, {"format": NQUADS})
    elif format == JSON_LD:
        nquads = jsonld.to_rdf(json.loads(text), {"format": NQUADS})
    elif format == NQUADS:
        nquads = text
    else:
        nquads = _dataset(text, "turtle").serialize(format="nquads")

    # Only one of the two mechanisms may act. Without a named set the synonyms are rewritten
    # onto the primary IRIs and the declared context reads the result. With one, the promoted
    # context already maps those IRIs - and carries the ``@nest`` that decides the shape - so
    # rewriting first would move the predicates out from under it and lose both.
    if set_id is None:
        nquads = _apply_rewrites(nquads, *_rewrite_map(schemas, context))
    document = jsonld.from_rdf(nquads, {"format": NQUADS, "useNativeTypes": True})

    if frame is None and _is_graph_document(document) and schemas:
        frame = reference_preserving_frame(schemas, context)

    if frame is not None:
        result = jsonld.frame(document, frame, {**(options or {}), "omitDefault": True})
    else:
        result = jsonld.compact(document, context, {**(options or {})})
    return _strip_blank_ids(result, _id_keys(context))


def reference_preserving_frame(schemas: list[dict[str, Any]], context: Any) -> dict[str, Any] | None:
    """The schema-derived frame, with reference-valued properties kept as references.

    Framing embeds any referenced node it finds in the graph, so without ``@embed: @never`` a
    person's ``works_for`` would absorb the organization's whole node. A property whose schema
    says ``format: iri-reference`` (or ``iri``) is a reference by declaration.
    """
    try:
        from oold.validation.frame import schema_to_frame
    except ImportError:
        return None
    if not schemas:
        return None
    frame = schema_to_frame(schemas[-1], context)
    for member in schemas:
        if not isinstance(member, dict):
            continue
        for name, subschema in (member.get("properties") or {}).items():
            if isinstance(subschema, dict) and subschema.get("format") in ("iri-reference", "iri"):
                if name not in ("id", "@id") and name not in frame:
                    frame[name] = {"@embed": "@never"}
    return frame


def transform(instance: Any, source_schemas: Any, target_schemas: Any, set_id: str | None = None) -> Any:
    """Read an instance of one schema chain as an instance of another, through RDF.

    The two hops are exactly :func:`to_rdf` and :func:`from_rdf`, so a transformation and a
    hand-pasted graph take the same path and cannot diverge in behaviour.
    """
    return from_rdf(
        to_rdf(instance, source_schemas, set_id=set_id, format=NQUADS),
        target_schemas,
        format=NQUADS,
    )


# ---------------------------------------------------------------------------- graph view

_SPLIT_ON = ("#", "/")


def shorten(iri: str) -> str:
    """The last meaningful segment of an IRI, for a label that fits in a node."""
    text = iri.rstrip("/")
    for separator in _SPLIT_ON:
        if separator in text:
            candidate = text.rsplit(separator, 1)[-1]
            if candidate:
                return candidate
    return text


def graph(data: str, format: str = NQUADS) -> dict[str, list[dict[str, Any]]]:
    """``{"nodes": [...], "edges": [...]}`` for a network view of a graph.

    Only relations between resources become edges. A literal is a property *of* a node, not a
    thing in its own right, so drawing it as a node would double the size of the picture and
    bury the structure the view exists to show; literals are collected into the node's title
    instead.
    """
    if not (data or "").strip():
        return {"nodes": [], "edges": []}

    from rdflib import Literal, URIRef

    dataset = _dataset(data, "nquads" if format == NQUADS else "turtle")

    literals: dict[str, list[str]] = {}
    resources: set[str] = set()
    edges: list[dict[str, Any]] = []

    for graph_ in dataset.graphs():
        for subject, predicate, obj in graph_:
            source = str(subject)
            resources.add(source)
            if isinstance(obj, Literal):
                literals.setdefault(source, []).append(f"{shorten(str(predicate))}: {obj}")
            elif isinstance(obj, URIRef) or hasattr(obj, "n3"):
                target = str(obj)
                resources.add(target)
                edges.append(
                    {
                        "from": source,
                        "to": target,
                        "label": shorten(str(predicate)),
                        "arrows": "to",
                        "title": str(predicate),
                    }
                )

    nodes = []
    for iri in sorted(resources):
        detail = literals.get(iri, [])
        nodes.append(
            {
                "id": iri,
                "label": shorten(iri),
                "title": "\n".join([iri, *detail]),
                "shape": "box" if detail else "ellipse",
            }
        )
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------- terms table

#: Context entries that alias a JSON-LD keyword rather than naming a vocabulary term.
_KEYWORDS = {"@id", "@type", "@value", "@language", "@graph", "@list", "@set", "@none"}


def _is_prefix(value: Any) -> bool:
    """Whether an entry defines a namespace prefix rather than a term."""
    return isinstance(value, str) and value.endswith(("/", "#", ":"))


def _iri_of(definition: Any) -> str:
    """The IRI a term definition points at, whichever slot carries it."""
    if isinstance(definition, str):
        return definition
    if isinstance(definition, dict):
        for slot in ("@id", "@reverse"):
            if isinstance(definition.get(slot), str):
                return definition[slot]
    return ""


def _kind(term: str, definition: Any, required: set[str], properties: dict[str, Any]) -> str:
    if _is_prefix(definition):
        return "prefix"
    if _iri_of(definition) in _KEYWORDS:
        return "keyword alias"
    if isinstance(definition, dict) and "@reverse" in definition:
        return "reverse property"
    if term in properties:
        return "property*" if term in required else "property"
    if term and term[:1].isupper():
        return "class"
    return "term"


def _alternatives(term: str, synonyms: dict[str, dict[str, Any]]) -> str:
    """The synonym column: each other IRI, its mapping predicate and its set."""
    entries = synonyms.get(term) or {}
    if not entries:
        return "<span style='color:#999'>-</span>"
    parts = []
    for iri, fragment in entries.items():
        sssom = fragment.get("x-oold-sssom") or {}
        predicate = sssom.get("predicate_id", "skos:exactMatch")
        marker = "" if is_exact_match(fragment) else f" <em>{html.escape(str(predicate))}</em>"
        raw_sets = sssom.get("mapping_set_id")
        sets = [raw_sets] if isinstance(raw_sets, str) else list(raw_sets or [])
        labels = ", ".join(set_name(s) for s in sets)
        suffix = f" <small>({html.escape(labels)})</small>" if labels else ""
        parts.append(f"<code>{html.escape(iri)}</code>{marker}{suffix}")
    return "<br>".join(parts)


def terms(schemas: Any) -> str:
    """An HTML table of the terms of a schema and everything it extends.

    The JSON tab shows what the schema *is*; this shows what it *means*: for each term, the IRI
    it maps to, and the other IRIs it is also known by. That second column is the whole point of
    ``x-oold-context`` and is invisible in the raw document, where the synonyms sit in a separate
    block from the terms they belong to.
    """
    schemas = _as_schemas(schemas)
    if not schemas:
        return "<p style='color:#999'>No schema to read.</p>"

    merged = declared_context(schemas)
    sections = []

    for schema in schemas:
        own_context = context_of(schema)
        if not own_context:
            continue
        synonyms = synonyms_of(schema)
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        title = schema.get("title") or schema.get("$id") or "schema"

        rows = []
        for term, definition in own_context.items():
            if term.startswith("@"):
                continue
            kind = _kind(term, definition, required, properties)
            iri = _iri_of(definition) or (definition if isinstance(definition, str) else "")
            description = ""
            if term in properties and isinstance(properties[term], dict):
                description = properties[term].get("description") or ""
            label = html.escape(term)
            if kind == "property*":
                label += " <sup title='required'>*</sup>"
            rows.append(
                "<tr>"
                f"<td><code>{label}</code>"
                + (f"<br><small>{html.escape(description)}</small>" if description else "")
                + "</td>"
                f"<td><small>{html.escape(kind.rstrip('*'))}</small></td>"
                f"<td><code>{html.escape(str(iri))}</code></td>"
                f"<td>{_alternatives(term, synonyms)}</td>"
                "</tr>"
            )

        if rows:
            sections.append(
                f"<tr><th colspan='4' style='text-align:left;background:rgba(127,127,127,.12)'>"
                f"{html.escape(str(title))}</th></tr>" + "".join(rows)
            )

    if not sections:
        return "<p style='color:#999'>This schema declares no <code>@context</code>.</p>"

    unmapped = [name for name in (schemas[-1].get("properties") or {}) if name not in merged]
    note = ""
    if unmapped:
        note = (
            "<p><small>Structural only, with no term mapping: "
            + ", ".join(f"<code>{html.escape(n)}</code>" for n in unmapped)
            + "</small></p>"
        )

    return (
        "<table style='width:100%;border-collapse:collapse;font-size:12px'>"
        "<thead><tr>"
        "<th style='text-align:left'>Term</th>"
        "<th style='text-align:left'>Kind</th>"
        "<th style='text-align:left'>Maps to</th>"
        "<th style='text-align:left'>Also known as</th>"
        "</tr></thead><tbody>" + "".join(sections) + "</tbody></table>"
        "<p><small><code>*</code> marks a required property.</small></p>" + note
    )
