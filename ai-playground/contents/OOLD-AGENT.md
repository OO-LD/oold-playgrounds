# OO-LD agent brief

You work in a JupyterLite notebook under a Pyodide kernel. The user arrives with a folder
of CSV files and no schema. Your job: profile the data, propose terms, validate, fix what
the validator reports, repeat. Commit only when the user asks.

Run Python with the `jupyterlab-ai-commands:execute-in-kernel` command. There is one kernel
per notebook and it keeps its state between calls, so import once and reuse.

## Start of a session

```python
import oold_agent
await oold_agent.setup()
```

Import `oold_agent` before anything touches `oold`: it sets `OOLD_DESCRIPTOR_BINDING` at
import time and the binding is chosen when `oold.model` is first imported. `setup` is the
only coroutine in the module; everything else is an ordinary call.

## Command line

`!oold ...` does not work. Emscripten has no subprocess. Use

```python
oold_agent.oold_cli("validate", "Thing.schema.json")
```

It returns everything the CLI printed plus a final `exit code N` line, and never raises.
`--offline` is appended for you, because the kernel reaches only the origin serving the site.

## Reasoning about results

Use the wrappers, not the CLI text, when you have to act on a finding:
`validate_schema`, `validate_instance`, `validate`. Each returns a `Report` with `.passed`,
`.counts`, `.failures()` and `.to_dict()`. Read `check.id` and `check.rule`, never prose.

Ids you will see: `schema.meta`, `schema.refs`, `context.predicates`, `context.coverage`,
`instance.schema`, `roundtrip.generated`, `roundtrip.instance`, `generate.satisfiable`,
`lint.*`, `rule.*`.

Status vocabulary:

- `fail` is a problem in the document.
- `warn` is a SHOULD and does not fail the document.
- `skip` means the check could not run.
- `fault` is a bug in oold: the question was never answered, so do not read the result as
  a pass or a failure of the document.

`passed` is false on `fail` or `fault`, never on `warn`.

All four validate entry points take a dict as well as a path, so validate a draft before
you write it to disk.

## Data and schemas

- `csv_profile(path)` returns columns with type, examples, nulls, distinct and unique, plus
  `rows`, `identifier` and `problems`. It never raises; a traceback out of it is a bug worth
  reporting rather than working around.
- `to_rdf(instance, schemas)` exports under the schema chain's effective context.
- `graph(nquads)` gives nodes and edges for a network view.
- `terms(schemas)` renders the terms of a schema and everything it extends.
- `transform(instance, source, target)` reads an instance of one chain as an instance of another.
- `chain(schema, resolve)` returns the inheritance chain, base first.

Fixtures on the drive to try things on: `fixture_Measurement.schema.json`,
`fixture_Measurement.instance.json`, `fixture_Broken.schema.json`, `fixture_bad.instance.json`,
`fixture_measurements.csv`, `fixture_malformed.csv`.

## GitHub

```python
import piplite
await piplite.install(["requests", "pyodide-http"])
import oold_git
```

The module is already on the drive and the working directory is `/drive`.

Nothing in `oold_git` raises. Check `result["ok"]` on every call; on a failure read `reason`,
`message` and `hint`. Do not wrap these calls in try/except.

- `pull(repo, ref, into, paths)`. Always pass `paths`. Without it the whole tree is fetched
  at one request per file, and unauthenticated GitHub allows 60 per hour. Read `rate` before
  a second pull.
- Reads need no token. Writes need a fine-grained PAT with Contents read and write, plus
  Pull requests read and write for `open_pr`. Never print the token and never write it into
  a cell.
- `commit` and `push` are two steps. `status()` between them reports `pending` and
  `remote_head`, and adds `stale` when the remote moved under the staged commit.
- `open_pr` needs `head` to exist on the remote already. This module cannot create a branch.
- There is no local history, no merge and no diff. A `conflict` reason means re-read the file
  and redo the change.

Demo pull:

```python
oold_git.pull("SINTEF/physmet-data-documentation-templates", into="gh",
              paths=["shared/*.csv", "context/context.json"])
```

## Pyodide trap

The kernel loads a distribution package only when the literal import statement appears in the
cell source it is about to run. An import inside a module on the drive is invisible to that
scan. If you need a package, write the import in the cell you execute.

## How to answer

Report what the checks said: the ids, the counts, the file. Propose the next edit. Do not
claim a document is valid without a `Report` that says so.
