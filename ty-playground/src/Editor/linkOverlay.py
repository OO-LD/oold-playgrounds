"""Playground-side instrumentation of the oold link resolution boundary.

Hooks ``_batch_resolve`` and the registered resolvers instead of tracing frames.
The hooks fire once per dereference, so frame inspection is paid per backend
call rather than per executed line, and the recorded payload is exactly the data
that never appears in the edited source.

Nothing here modifies the installed oold package: the originals are kept and
restored by ``uninstall()``.
"""

import inspect
import json
import time

USER_PREFIX = "/playground/"

_events = []
_calls = []
_field_stack = []
_state = {"installed": False}
_originals = {}
_resolver_originals = {}


def _user_location():
    frame = inspect.currentframe()
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename.startswith(USER_PREFIX):
            return filename[len(USER_PREFIX) :], frame.f_lineno
        frame = frame.f_back
    return None, None


def _linked_field():
    """Field name for the LinkedBaseModel path.

    ``_resolve`` is reached from ``__getattribute__``, whose ``name`` local is
    the field being dereferenced. Reading it costs a frame walk per backend
    call, not per attribute access.
    """
    frame = inspect.currentframe()
    while frame is not None:
        if frame.f_code.co_name == "__getattribute__":
            return frame.f_locals.get("name")
        frame = frame.f_back
    return None


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _wrap_resolver(cls):
    if cls in _resolver_originals:
        return
    original = cls.__dict__.get("resolve_iris")
    if original is None:
        original = cls.resolve_iris

    def resolve_iris(self, iris):
        started = time.perf_counter()
        fetched = original(self, iris)
        _calls.append(
            {
                "resolver": type(self).__name__,
                "iris": list(iris),
                "values": {key: _jsonable(value) for key, value in (fetched or {}).items()},
                "ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return fetched

    _resolver_originals[cls] = original
    cls.resolve_iris = resolve_iris


def install():
    if _state["installed"]:
        return "already installed"

    from oold.backend import interface
    from oold.model import _descriptor

    original_batch = _descriptor._batch_resolve

    def _batch_resolve(refs, target):
        for resolver in list(interface._resolvers.values()):
            _wrap_resolver(type(resolver))

        known = [ref for ref in refs if ref is not None and ref.iri]
        pending = [ref for ref in known if ref._obj is None]

        before = len(_calls)
        started = time.perf_counter()
        result = original_batch(refs, target)
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        calls = _calls[before:]

        values = {}
        for call in calls:
            for iri, payload in call["values"].items():
                values[iri] = payload

        filename, lineno = _user_location()
        _events.append(
            {
                "field": _field_stack[-1] if _field_stack else None,
                "file": filename,
                "line": lineno,
                "target": getattr(target, "__name__", str(target)),
                "iris": [ref.iri for ref in known],
                "fetchedIris": [ref.iri for ref in pending],
                "cached": len(known) - len(pending),
                "backendCalls": len(calls),
                "resolvers": sorted({call["resolver"] for call in calls}),
                "values": values,
                "ms": elapsed,
            }
        )
        return result

    original_get = _descriptor._AutoLink.__dict__["__get__"]

    def __get__(self, obj, objtype=None):
        if obj is None:
            return original_get(self, obj, objtype)
        _field_stack.append(self.name)
        try:
            return original_get(self, obj, objtype)
        finally:
            _field_stack.pop()

    from oold.model import LinkedBaseModel

    original_linked = LinkedBaseModel.__dict__["_resolve"]
    linked_fn = getattr(original_linked, "__func__", original_linked)

    def _linked_resolve(iris):
        for resolver in list(interface._resolvers.values()):
            _wrap_resolver(type(resolver))

        before = len(_calls)
        started = time.perf_counter()
        result = linked_fn(iris)
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        calls = _calls[before:]

        values = {}
        for call in calls:
            for iri, payload in call["values"].items():
                values[iri] = payload

        filename, lineno = _user_location()
        _events.append(
            {
                "field": _linked_field(),
                "file": filename,
                "line": lineno,
                "target": "LinkedBaseModel",
                "iris": list(iris),
                "fetchedIris": list(iris),
                "cached": 0,
                "backendCalls": len(calls),
                "resolvers": sorted({call["resolver"] for call in calls}),
                "values": values,
                "ms": elapsed,
            }
        )
        return result

    _originals["batch"] = original_batch
    _originals["get"] = original_get
    _originals["linked"] = original_linked
    _descriptor._batch_resolve = _batch_resolve
    _descriptor._AutoLink.__get__ = __get__
    LinkedBaseModel._resolve = staticmethod(_linked_resolve)
    for resolver in list(interface._resolvers.values()):
        _wrap_resolver(type(resolver))
    _state["installed"] = True
    return "installed"


def uninstall():
    if not _state["installed"]:
        return "not installed"

    from oold.model import LinkedBaseModel, _descriptor

    _descriptor._batch_resolve = _originals["batch"]
    _descriptor._AutoLink.__get__ = _originals["get"]
    LinkedBaseModel._resolve = _originals["linked"]
    for cls, original in _resolver_originals.items():
        cls.resolve_iris = original
    _resolver_originals.clear()
    _originals.clear()
    _state["installed"] = False
    return "uninstalled"


def reset():
    del _events[:]
    del _calls[:]
    del _field_stack[:]


def drain():
    payload = json.dumps({"events": list(_events), "backendCalls": list(_calls)}, default=str)
    reset()
    return payload


def installed():
    return _state["installed"]
