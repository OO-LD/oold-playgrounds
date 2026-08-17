"""URL-backed configuration for Panel apps using Pydantic models.

Provides :class:`UrlConfig`, which serializes a Pydantic model to and from browser URL query
parameters via ``pn.state.location``. That makes an app session a link: whatever the user
has loaded and selected travels in the URL, so a playground state can be shared or bookmarked
without a server-side store.

Three encodings are read - one parameter per flattened field, the model's JSON in a single
parameter, or that JSON zlib-compressed and base64url-encoded - and the compressed one is
written back by default. Accepting all three matters because a hand-written link is far easier
to type in plain form, while a generated link should stay short.

Ported from ``opensemantic.base`` (``src/opensemantic/base/view/url_config.py``,
https://github.com/OpenSemanticWorld-Packages/opensemantic.base-python). It lives here rather
than being imported because that package already depends on ``oold``; importing it back would
invert the layering, and its view extra pulls in dependencies a browser (Pyodide) build cannot
carry. Keep the two in step, or let the upstream copy import this one.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.parse
import zlib
from enum import Enum
from typing import Any, Dict, Generic, Type, TypeVar

import panel as pn
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class UrlConfigMode(Enum):
    """Encoding mode for URL config parameters.

    - ``PLAIN_KEYS``: human-readable, one dot-flattened param per leaf (each a JSON-encoded
      value). Round-trips arbitrary typed content; the one caveat is dict *keys* that
      themselves contain a dot.
    - ``JSON``: the model's JSON as a single (URL-encoded) param - readable but compact, no
      key-with-dot caveat.
    - ``COMPRESSED_BASE64`` (default): zlib-compressed, base64url-encoded JSON in a single
      param - shortest, opaque.
    """

    PLAIN_KEYS = "plain_keys"
    JSON = "json"
    COMPRESSED_BASE64 = "compressed_base64"


def _flatten_dict(data: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    """Flatten a nested dict into dot-separated keys with JSON-encoded leaves.

    Lists use numeric indices (e.g. ``items.0.name``). Each scalar leaf - and each *empty*
    container - is stored as ``json.dumps(value)`` so types (bool/int/float/None and empty
    ``[]`` / ``{}``) round-trip losslessly.
    """
    result: Dict[str, str] = {}

    def _walk(key: str, value: Any) -> None:
        if isinstance(value, dict) and value:
            for k, v in value.items():
                _walk(f"{key}.{k}", v)
        elif isinstance(value, list) and value:
            for i, item in enumerate(value):
                _walk(f"{key}.{i}", item)
        else:
            result[key] = json.dumps(value)

    for key, value in data.items():
        _walk(f"{prefix}.{key}" if prefix else key, value)
    return result


def _unflatten_dict(flat: Dict[str, str]) -> Dict[str, Any]:
    """Rebuild a nested dict from dot-separated flat keys.

    Numeric path segments produce lists; all others produce dicts. Leaves are
    ``json.loads``-decoded back to their original type (falling back to the raw string if a
    value is not valid JSON).
    """
    root: Dict[str, Any] = {}
    for compound_key in sorted(flat):
        raw = flat[compound_key]
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            value = raw
        parts = compound_key.split(".")
        current: Any = root
        for i, part in enumerate(parts[:-1]):
            next_is_index = parts[i + 1].isdigit()
            if isinstance(current, list):
                idx = int(part)
                while len(current) <= idx:
                    current.append([] if next_is_index else {})
                if current[idx] is None:
                    current[idx] = [] if next_is_index else {}
                current = current[idx]
            else:
                if part not in current:
                    current[part] = [] if next_is_index else {}
                current = current[part]

        leaf = parts[-1]
        if isinstance(current, list):
            idx = int(leaf)
            while len(current) <= idx:
                current.append(None)
            current[idx] = value
        else:
            current[leaf] = value
    return root


def _compress_config(config: BaseModel) -> str:
    """Serialize a Pydantic model to zlib-compressed, URL-safe base64."""
    json_bytes = config.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(json_bytes)).decode("ascii")


def _decompress_config(encoded: str, model_class: Type[T]) -> T:
    """Deserialize a compressed base64url string back into a Pydantic model."""
    try:
        compressed = base64.urlsafe_b64decode(encoded)
        return model_class.model_validate(json.loads(zlib.decompress(compressed)))
    except Exception as exc:
        raise ValueError(f"Failed to decompress config: {exc}") from exc


def _location() -> Any:
    """The browser location, or ``None`` outside a served session.

    A seam rather than a direct attribute read: ``pn.state.location`` is a read-only property,
    so this is the only place a test can stand in for it. (Addition to the upstream copy.)
    """
    return pn.state.location


def _read_query_params() -> Dict[str, str]:
    """Read current URL query parameters as a flat string dict."""
    location = _location()
    if location is None or not location.search:
        return {}
    parsed = urllib.parse.parse_qs(location.search.lstrip("?"), keep_blank_values=True)
    return {k: v[-1] for k, v in parsed.items()}


def _write_query_params(params: Dict[str, str]) -> None:
    """Write query parameters to the URL, replacing the full query string."""
    location = _location()
    if location is None:
        logger.warning("pn.state.location is None; cannot write URL params.")
        return
    location.search = "?" + urllib.parse.urlencode(params) if params else ""


class UrlConfig(Generic[T]):
    """Generic URL-backed configuration manager for Pydantic models.

    Example::

        class MySettings(BaseModel):
            theme: str = "light"

        url_cfg = UrlConfig(MySettings, param_name="settings")
        settings = url_cfg.get_config()   # reads from URL or returns defaults
        url_cfg.set_config(settings)      # writes to URL, compressed
    """

    def __init__(self, model_class: Type[T], param_name: str = "config") -> None:
        self.model_class = model_class
        self.param_name = param_name

    def has_config(self) -> bool:
        """Whether the URL currently carries params for this config."""
        params = _read_query_params()
        prefix = f"{self.param_name}."
        return self.param_name in params or any(k.startswith(prefix) for k in params)

    def get_config(self) -> T:
        """Read configuration from URL query parameters.

        Auto-detects the encoding: a key matching ``param_name`` is tried as compressed and
        then as plain JSON; keys under ``{param_name}.`` are read as flattened fields;
        anything else yields a default instance.
        """
        params = _read_query_params()
        if not params:
            return self.model_class()

        if self.param_name in params:
            raw = params[self.param_name]
            try:
                return _decompress_config(raw, self.model_class)
            except ValueError:
                pass
            try:
                return self.model_class.model_validate_json(raw)
            except Exception:
                logger.debug(
                    "Key '%s' found but single-param decode failed; trying PLAIN_KEYS.",
                    self.param_name,
                )

        prefix = f"{self.param_name}."
        plain = {k[len(prefix) :]: v for k, v in params.items() if k.startswith(prefix)}
        if plain:
            try:
                return self.model_class.model_validate(_unflatten_dict(plain))
            except Exception as exc:
                logger.warning("PLAIN_KEYS decode failed: %s. Returning default.", exc)
                return self.model_class()

        return self.model_class()

    def set_config(
        self,
        config: T,
        mode: UrlConfigMode = UrlConfigMode.COMPRESSED_BASE64,
    ) -> None:
        """Write configuration to URL query parameters.

        Preserves existing query parameters that do not belong to this config.
        """
        existing = _read_query_params()
        prefix = f"{self.param_name}."
        preserved = {
            k: v
            for k, v in existing.items()
            if k != self.param_name and not k.startswith(prefix)
        }

        if mode is UrlConfigMode.COMPRESSED_BASE64:
            preserved[self.param_name] = _compress_config(config)
        elif mode is UrlConfigMode.JSON:
            preserved[self.param_name] = config.model_dump_json()
        elif mode is UrlConfigMode.PLAIN_KEYS:
            # mode="json" so enums serialize to their values and datetimes to ISO strings.
            preserved.update(
                _flatten_dict(config.model_dump(mode="json"), prefix=self.param_name)
            )
        else:
            raise ValueError(f"Unknown UrlConfigMode: {mode}")

        _write_query_params(preserved)

    def clear_config(self) -> None:
        """Remove this config's parameters from the URL, preserving others."""
        existing = _read_query_params()
        prefix = f"{self.param_name}."
        _write_query_params(
            {
                k: v
                for k, v in existing.items()
                if k != self.param_name and not k.startswith(prefix)
            }
        )

    def bind(
        self, mode: UrlConfigMode = UrlConfigMode.COMPRESSED_BASE64
    ) -> "BoundConfig[T]":
        """Read config from the URL and return an auto-syncing proxy.

        The model class should use ``ConfigDict(validate_assignment=True)`` so Pydantic
        allows field mutation.
        """
        return BoundConfig(self.get_config(), self, mode)


class BoundConfig(Generic[T]):
    """Proxy that auto-syncs Pydantic model field changes to URL params."""

    def __init__(self, model: T, url_config: UrlConfig[T], mode: UrlConfigMode) -> None:
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_url_config", url_config)
        object.__setattr__(self, "_mode", mode)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._model, name, value)
        if name in type(self._model).model_fields:
            self._url_config.set_config(self._model, self._mode)
