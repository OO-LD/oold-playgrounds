"""What the playground is showing, in a form that fits in a URL.

Every editable field holds *either* a remote URL or the document itself. That single rule is
what lets a link carry a session: a short link points at published schemas, a self-contained
link carries the text. :func:`resolve_source` decides which it is and reports a failed fetch
instead of silently falling back, because a playground that quietly shows the default when a
URL is wrong is worse than one that says so.

The defaults are a worked example rather than an empty page: a `Person` written in schema.org
terms, read back as an `Employee` written in FOAF and ORG terms. Nothing about the instance
changes; the two schemas simply declare the same relations under different IRIs and say so in
``x-oold-context``.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

TURTLE = "text/turtle"
JSON_LD = "application/ld+json"

#: The set the source schema tags its FOAF synonyms with, offered in the mapping-set dropdown.
FOAF_SET = "https://example.org/mapping-sets/foaf"

SOURCE_SCHEMA = json.dumps(
    {
        "$schema": "https://oo-ld.org/latest/meta/oold-meta-schema.json",
        "$id": "Person.schema.json",
        "title": "Person",
        "type": "object",
        "@context": {
            "schema": "https://schema.org/",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "org": "http://www.w3.org/ns/org#",
            "id": "@id",
            "type": "@type",
            "name": "schema:name",
            "email": "schema:email",
            "works_for": {"@id": "schema:worksFor", "@type": "@id"},
            "homepage": {"@id": "schema:url", "@type": "@id"},
            "birth_date": "schema:birthDate",
            "Person": "schema:Person",
        },
        "x-oold-instance-rdf-type": ["schema:Person"],
        "x-oold-context": {
            "Person": {
                "foaf:Person": {
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": FOAF_SET,
                    }
                }
            },
            "name": {
                "foaf:name": {
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": FOAF_SET,
                    }
                }
            },
            "email": {
                "foaf:mbox": {
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": FOAF_SET,
                    }
                }
            },
            "works_for": {
                "org:memberOf": {
                    "@type": "@id",
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": FOAF_SET,
                    },
                }
            },
            "homepage": {
                "foaf:homepage": {
                    "@type": "@id",
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": FOAF_SET,
                    },
                }
            },
        },
        "properties": {
            "id": {"type": "string", "format": "iri", "description": "IRI identifying this person"},
            "name": {"type": "string", "description": "Full name"},
            "email": {"type": "string", "format": "email", "description": "Contact email address"},
            "works_for": {
                "type": "string",
                "format": "iri-reference",
                "description": "Organization the person works for",
            },
            "homepage": {
                "type": "string",
                "format": "uri",
                "description": "Personal or professional homepage",
            },
            "birth_date": {"type": "string", "format": "date", "description": "Date of birth"},
        },
    },
    indent=2,
)

SOURCE_INSTANCE = json.dumps(
    {
        # An OO-LD instance is itself a JSON-LD document, so it names the schema twice: as its
        # remote context (what the terms mean) and as its `$schema` (what shape it must have).
        "@context": "Person.schema.json",
        "$schema": "Person.schema.json",
        "id": "https://example.org/people/jane",
        "name": "Jane Doe",
        "email": "jane@example.org",
        "works_for": "https://example.org/orgs/acme",
    },
    indent=2,
)

TARGET_SCHEMA = json.dumps(
    {
        "$schema": "https://oo-ld.org/latest/meta/oold-meta-schema.json",
        "$id": "Employee.schema.json",
        "title": "Employee",
        "type": "object",
        "@context": {
            "foaf": "http://xmlns.com/foaf/0.1/",
            "org": "http://www.w3.org/ns/org#",
            "schema": "https://schema.org/",
            "id": "@id",
            "type": "@type",
            "full_name": "foaf:name",
            "mbox": "foaf:mbox",
            "employer": {"@id": "org:memberOf", "@type": "@id"},
            "homepage": {"@id": "foaf:homepage", "@type": "@id"},
            "Employee": "foaf:Person",
        },
        "x-oold-instance-rdf-type": ["foaf:Person"],
        "x-oold-context": {
            "Employee": {
                "schema:Person": {"x-oold-sssom": {"predicate_id": "skos:exactMatch"}}
            },
            "full_name": {"schema:name": {"x-oold-sssom": {"predicate_id": "skos:exactMatch"}}},
            "mbox": {"schema:email": {"x-oold-sssom": {"predicate_id": "skos:exactMatch"}}},
            "employer": {
                "schema:worksFor": {
                    "@type": "@id",
                    "x-oold-sssom": {"predicate_id": "skos:exactMatch"},
                }
            },
            "homepage": {
                "schema:url": {
                    "@type": "@id",
                    "x-oold-sssom": {"predicate_id": "skos:exactMatch"},
                }
            },
        },
        "properties": {
            "id": {"type": "string", "format": "iri", "description": "IRI identifying this person"},
            "full_name": {"type": "string", "description": "Full name, FOAF style"},
            "mbox": {"type": "string", "format": "email", "description": "Mailbox address"},
            "employer": {
                "type": "string",
                "format": "iri-reference",
                "description": "Organization this person is a member of",
            },
            "homepage": {
                "type": "string",
                "format": "uri",
                "description": "Personal or professional homepage",
            },
        },
    },
    indent=2,
)


SIMPLE_SCHEMA = json.dumps(
    {
        "$schema": "https://oo-ld.org/latest/meta/oold-meta-schema.json",
        "$id": "Person.schema.json",
        "title": "Person",
        "type": "object",
        "@context": {
            "schema": "https://schema.org/",
            "id": "@id",
            "name": "schema:name",
        },
        "properties": {
            "id": {"type": "string", "format": "iri", "description": "IRI identifying this person"},
            "name": {"type": "string", "description": "Full name"},
        },
    },
    indent=2,
)

SIMPLE_INSTANCE = json.dumps(
    {
        "@context": "Person.schema.json",
        "$schema": "Person.schema.json",
        "id": "https://example.org/people/jane",
        "name": "Jane Doe",
    },
    indent=2,
)

ORG_SOURCE_SCHEMA = json.dumps(
    {
        "$schema": "https://oo-ld.org/latest/meta/oold-meta-schema.json",
        "$id": "Organization.schema.json",
        "title": "Organization",
        "type": "object",
        "@context": {
            "schema": "https://schema.org/",
            "id": "@id",
            "type": "@type",
            "name": "schema:name",
        },
        "x-oold-instance-rdf-type": ["schema:Organization"],
        "properties": {
            "id": {"type": "string", "format": "iri"},
            "name": {"type": "string", "description": "Organization name"},
        },
    },
    indent=2,
)

TEAM_TARGET_SCHEMA = json.dumps(
    {
        "$schema": "https://oo-ld.org/latest/meta/oold-meta-schema.json",
        "$id": "Team.schema.json",
        "title": "Team",
        "type": "object",
        "@context": {
            "schema": "https://schema.org/",
            "id": "@id",
            "type": "@type",
            "name": "schema:name",
            "members": {"@reverse": "schema:worksFor", "@type": "@id"},
        },
        "x-oold-instance-rdf-type": ["schema:Organization"],
        "properties": {
            "id": {"type": "string", "format": "iri"},
            "name": {"type": "string"},
            "members": {
                "type": "array",
                "description": "Everyone working for this organization, nested",
                "items": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
        },
    },
    indent=2,
)


def _person_doc(identifier: str, name: str) -> str:
    return json.dumps(
        {
            "@context": "Person.schema.json",
            "$schema": "Person.schema.json",
            "id": identifier,
            "name": name,
            "works_for": "https://example.org/orgs/acme",
        },
        indent=2,
    )


ORG_DOC = json.dumps(
    {
        "@context": "Organization.schema.json",
        "$schema": "Organization.schema.json",
        "id": "https://example.org/orgs/acme",
        "name": "ACME",
    },
    indent=2,
)


class PlaygroundConfig(BaseModel):
    """The whole session, small enough to travel in a query parameter.

    ``validate_assignment`` is required by :meth:`schema_playground.url_config.UrlConfig.bind`, which
    writes the URL on every field assignment.
    """

    model_config = ConfigDict(validate_assignment=True)

    source_schemas: list[str] = [SOURCE_SCHEMA]
    source_instances: list[str] = [SOURCE_INSTANCE]
    target_schemas: list[str] = [TARGET_SCHEMA]
    source_schema_idx: int = 0
    source_instance_idx: int = 0
    target_schema_idx: int = 0
    pasted_rdf: str = ""
    #: Whether the paste field owns the bus. When true the source columns are collapsed,
    #: because they no longer feed anything.
    use_paste: bool = False
    paste_format: str = TURTLE
    rdf_format: str = TURTLE
    #: Empty means the consensus reading, which is what an instance means unmapped.
    mapping_set: str = ""
    #: Pane titles currently collapsed, so a shared link reproduces the layout too.
    collapsed_panes: list[str] = []


#: The reference examples the toolbar switches between. Each is a complete session config;
#: applying one is the same as loading its URL.
EXAMPLES: dict[str, "PlaygroundConfig"] = {}


def _define_examples() -> None:
    EXAMPLES["Simple"] = PlaygroundConfig(
        source_schemas=[SIMPLE_SCHEMA],
        source_instances=[SIMPLE_INSTANCE],
        target_schemas=[SIMPLE_SCHEMA],
        collapsed_panes=["Target schemas"],
    )
    EXAMPLES["Transform"] = PlaygroundConfig()
    EXAMPLES["Multi-doc"] = PlaygroundConfig(
        source_schemas=[SOURCE_SCHEMA, ORG_SOURCE_SCHEMA],
        source_instances=[
            _person_doc("https://example.org/people/jane", "Jane Doe"),
            _person_doc("https://example.org/people/joe", "Joe Bloggs"),
            ORG_DOC,
        ],
        target_schemas=[TARGET_SCHEMA, TEAM_TARGET_SCHEMA],
    )


_define_examples()


def looks_like_url(value: str) -> bool:
    """Whether a field holds a URL to fetch rather than a document to read.

    Only http(s) counts. A JSON document starts with ``{`` and a Turtle one with ``@prefix``,
    so there is no realistic overlap, but requiring a scheme keeps the test explicit rather
    than heuristic.
    """
    text = value.strip()
    if "\n" in text or len(text) > 2048:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


#: Documents already fetched this session. A schema is re-read on every recompute - which a
#: keystroke triggers - and a published schema does not change underneath an editing session,
#: so without this the app would refetch the same files continuously.
_CACHE: dict[str, str] = {}


def clear_cache() -> None:
    """Forget fetched documents, so a reload picks up a changed remote file."""
    _CACHE.clear()


def fetch(url: str, timeout: float = 10.0) -> str:
    """Read a remote document as text, remembering it for the rest of the session.

    Kept as a seam: a browser build patches ``urllib`` through ``pyodide-http`` rather than
    replacing this function.
    """
    if url in _CACHE:
        return _CACHE[url]

    from urllib.request import urlopen

    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - scheme checked by caller
        text = response.read().decode("utf-8")
    _CACHE[url] = text
    return text


def resolve_source(value: str) -> tuple[str, str | None]:
    """``(text, error)`` for a field that may hold a URL or a document.

    A failed fetch returns the reason and an empty document, so the caller can show the error
    where the user typed it instead of rendering a stale or default document as if it had
    been asked for.
    """
    if not value or not value.strip():
        return "", None
    if not looks_like_url(value):
        return value, None
    try:
        return fetch(value.strip()), None
    except Exception as exc:
        logger.warning("could not fetch %s: %s", value.strip(), exc)
        return "", f"could not fetch {value.strip()}: {exc}"


def source_base(value: str) -> str | None:
    """The URL a field's relative references resolve against, if it named one.

    Published schemas reference their neighbours by bare filename
    (``"$ref": "Length.schema.json"``), so a schema loaded from a URL only has a resolvable
    chain when that URL is remembered.
    """
    return value.strip() if looks_like_url(value) else None


def parse_document(text: str) -> tuple[Any, str | None]:
    """``(document, error)`` for text that may be JSON or YAML.

    JSON is tried first because it is the canonical notation and the common case; YAML is
    accepted because it is what people paste when they are writing by hand.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None, None
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as json_error:
        try:
            import yaml

            documents = list(yaml.safe_load_all(stripped))
        except Exception:
            # Report the JSON failure: it names a line and column, where the YAML parser
            # tends to fail much later with a message about the JSON-shaped text.
            return None, f"not valid JSON or YAML: {json_error}"
        if len(documents) == 1:
            return documents[0], None
        # A YAML document stream has no JSON counterpart; the JSON-LD container for several
        # entities in one document is @graph, so that is what a stream means here.
        return {"@graph": [doc for doc in documents if doc is not None]}, None
