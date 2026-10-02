"""GitHub access for the JupyterLite kernel, over the REST API.

The kernel is the only tool the agent has, so repository access has to be
reachable from Python. ``isomorphic-git`` would need a CORS proxy, a LightningFS
bridge to the contents drive and a frontend extension; the REST API needs none of
that, because GitHub sends CORS headers and the browser can call it directly.
What is given up: GitHub only, no local history, no merge.

    import piplite
    await piplite.install(["requests", "pyodide-http"])

    import oold_git
    oold_git.pull("SINTEF/physmet-data-documentation-templates", paths=["shared/*.csv"])
    oold_git.configure(token=getpass.getpass("PAT: "))
    oold_git.commit(["schemas/thing.json"], "feat: add thing schema")
    oold_git.push()
    oold_git.open_pr("Add thing schema", "generated from shared/equipments.csv", head="bot/schemas")

No function in this module raises. Every call returns a dict carrying ``ok``, and
on failure a ``reason`` and a ``message``. That is deliberate: a token lives in
the request headers, and a traceback printed under ``%xmode verbose`` lists the
locals of every frame, including the ones inside ``requests``. Returning data
instead of raising means no traceback is ever produced from a frame that can see
the credential.

Token handling
--------------
``configure(token=...)`` is the only entry point for the PAT. It is kept in
module state and in the session headers, never written to disk and never part of
a return value. Strings that come back from this module are passed through a
redaction step, so a server that echoes the header cannot leak it either. The one
thing this module cannot protect is the cell that calls it: pasting the literal
into ``configure("ghp_...")`` puts it in the notebook source. Use ``getpass``.

Rate limits
-----------
Unauthenticated reads are capped at 60 requests per hour per IP, and ``pull``
spends one request for the tree plus one per file. Every result carries a
``rate`` block with what the API reported, so the budget is visible. Configure a
token to raise the cap.
"""

import base64
import fnmatch
import os
import time

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"

# Configuration, not a constant: configure(repos=[...]) replaces it. Both are
# public and were checked to be readable without a token.
DEFAULT_REPOS = [
    "OO-LD/oold-reference-schemas",
    "SINTEF/physmet-data-documentation-templates",
]

# Holder for the PAT. Module state on purpose: a value bound to a frame local is
# printed by verbose tracebacks, a module global is not.
_TOKEN = []

_state = {
    "api_base": GITHUB_API,
    "repo": None,
    "branch": "main",
    "repos": list(DEFAULT_REPOS),
    "pending": None,
    "last_pull": None,
}

_session = [None]

MAX_CONTENTS_BYTES = 1024 * 1024

__all__ = [
    "configure",
    "default_repos",
    "pull",
    "commit",
    "push",
    "open_pr",
    "status",
]


# --------------------------------------------------------------- redaction


def _redact(text):
    if not isinstance(text, str):
        return text
    if _TOKEN:
        text = text.replace(_TOKEN[0], "<token>")
    return text


def _err(reason, message, status=None, rate=None, **extra):
    out = {"ok": False, "reason": reason, "message": _redact(message), "status": status}
    if rate is not None:
        out["rate"] = rate
    out.update(extra)
    return out


# ------------------------------------------------------------------- http


def _http():
    if _session[0] is None:
        import requests

        try:
            import pyodide_http
        except ImportError:
            pyodide_http = None
        if pyodide_http is not None:
            # Patches requests and urllib. httpx is never patched, so this module
            # stays on requests.
            pyodide_http.patch_all()
        session = requests.Session()
        session.headers.update(
            {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
        )
        _session[0] = session
    return _session[0]


def _apply_auth(session):
    if _TOKEN:
        session.headers["Authorization"] = "Bearer " + _TOKEN[0]
    else:
        session.headers.pop("Authorization", None)


def _rate_of(response):
    get = response.headers.get
    rate = {
        "limit": _as_int(get("X-RateLimit-Limit")),
        "remaining": _as_int(get("X-RateLimit-Remaining")),
        "used": _as_int(get("X-RateLimit-Used")),
        "reset": _as_int(get("X-RateLimit-Reset")),
    }
    if rate["reset"]:
        rate["reset_in_seconds"] = max(0, rate["reset"] - int(time.time()))
    return rate


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classify(status, rate, body):
    if status == 401:
        return "auth", "the token was rejected, check that it is current and has the needed scope"
    if status in (403, 429) and rate.get("remaining") == 0:
        return "rate_limit", "no requests left in this window"
    if status == 403:
        return "forbidden", "the token is valid but not allowed to do this"
    if status == 404:
        return "not_found", "no such repository, branch or path, or it is private and the token cannot see it"
    if status == 409:
        return "conflict", "the branch moved since it was read"
    if status == 422:
        return "unprocessable", "the request was understood but rejected"
    return "http_error", "unexpected status"


def _request(method, path, payload=None, params=None, timeout=60):
    try:
        session = _http()
    except ImportError as exc:
        return _err(
            "missing_dependency",
            "%s; run: await piplite.install(['requests', 'pyodide-http'])" % exc,
        )
    _apply_auth(session)
    url = _state["api_base"].rstrip("/") + path
    try:
        response = session.request(method, url, json=payload, params=params, timeout=timeout)
    except Exception as exc:
        # Under Pyodide a failed fetch surfaces as pyodide.ffi.JsException rather
        # than requests.exceptions.ConnectionError, so this catch has to be wide.
        return _err("network", "%s: %s" % (type(exc).__name__, exc), url=url)

    rate = _rate_of(response)
    try:
        data = response.json()
    except ValueError:
        data = None

    if response.status_code >= 400:
        reason, hint = _classify(response.status_code, rate, data)
        message = ""
        if isinstance(data, dict):
            message = data.get("message") or ""
        if not message:
            message = response.text[:300]
        out = _err(reason, message, status=response.status_code, rate=rate, hint=hint, url=url)
        if reason == "rate_limit":
            out["retry_in_seconds"] = rate.get("reset_in_seconds")
            if not _TOKEN:
                out["hint"] = (
                    "unauthenticated reads are capped at 60 per hour per IP; "
                    "configure(token=...) raises the cap"
                )
        if reason == "auth":
            out["hint"] = hint
        if isinstance(data, dict) and data.get("errors"):
            out["errors"] = data["errors"]
        return out

    return {"ok": True, "status": response.status_code, "data": data, "rate": rate}


# -------------------------------------------------------------- configure


def configure(token=None, api_base=None, repo=None, branch=None, repos=None):
    """Set the credential and the endpoint.

    ``token`` is held in memory only. ``api_base`` exists so the test suite can
    point at a fixture server instead of github.com. Passing ``token=""`` drops
    the stored credential.
    """
    if token is not None:
        # Nothing between the store and the rebind can raise, so the argument
        # never survives in a frame that an exception could expose.
        del _TOKEN[:]
        if str(token).strip():
            _TOKEN.append(str(token).strip())
        token = None

    if api_base is not None:
        _state["api_base"] = str(api_base).rstrip("/")
    if repo is not None:
        _state["repo"] = str(repo)
    if branch is not None:
        _state["branch"] = str(branch)
    if repos is not None:
        _state["repos"] = [str(r) for r in repos]
    if _session[0] is not None:
        _apply_auth(_session[0])
    return _public_state()


def default_repos():
    """Repositories the playground offers to clone."""
    return list(_state["repos"])


def _public_state():
    return {
        "ok": True,
        "api_base": _state["api_base"],
        "repo": _state["repo"],
        "branch": _state["branch"],
        "authenticated": bool(_TOKEN),
        "default_repos": list(_state["repos"]),
    }


# ------------------------------------------------------------------- pull


def _wanted(path, patterns):
    if not patterns:
        return True
    for pattern in patterns:
        pattern = str(pattern)
        if path == pattern:
            return True
        if path.startswith(pattern.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def _safe_target(into, repo_path):
    if repo_path.startswith("/") or ".." in repo_path.split("/"):
        return None
    return os.path.normpath(os.path.join(into, *repo_path.split("/")))


def pull(repo=None, ref="main", into=".", paths=None):
    """Fetch a tree and write its blobs onto the contents drive.

    ``paths`` accepts exact paths, directory prefixes and fnmatch patterns such
    as ``shared/*.csv``. Without it the whole tree is fetched, which costs one
    request per file.
    """
    repo = repo or _state["repo"] or (default_repos() or [None])[0]
    if not repo:
        return _err("no_repo", "no repository given and no default configured")

    tree = _request("GET", "/repos/%s/git/trees/%s" % (repo, ref), params={"recursive": "1"})
    if not tree["ok"]:
        return dict(tree, repo=repo, ref=ref)

    data = tree["data"] or {}
    blobs = [e for e in data.get("tree", []) if e.get("type") == "blob"]
    selected = [e for e in blobs if _wanted(e.get("path", ""), paths)]

    files = []
    skipped = []
    total = 0
    rate = tree["rate"]
    for entry in selected:
        repo_path = entry["path"]
        target = _safe_target(into, repo_path)
        if target is None:
            skipped.append({"path": repo_path, "reason": "unsafe_path"})
            continue
        if (entry.get("size") or 0) > MAX_CONTENTS_BYTES:
            skipped.append({"path": repo_path, "reason": "too_large_for_contents_api", "size": entry.get("size")})
            continue

        got = _request("GET", "/repos/%s/contents/%s" % (repo, repo_path), params={"ref": ref})
        if not got["ok"]:
            return dict(
                got,
                repo=repo,
                ref=ref,
                failed_on=repo_path,
                files=files,
                partial=True,
            )
        rate = got["rate"]
        body = got["data"] or {}
        if body.get("encoding") != "base64":
            skipped.append({"path": repo_path, "reason": "encoding_%s" % body.get("encoding")})
            continue
        blob = base64.b64decode(body.get("content", ""))

        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(blob)
        total += len(blob)
        files.append({"path": repo_path, "local": target, "size": len(blob), "sha": entry.get("sha")})

    _state["repo"] = repo
    _state["branch"] = ref
    _state["last_pull"] = {"repo": repo, "ref": ref, "into": into, "paths": [f["path"] for f in files]}

    return {
        "ok": True,
        "repo": repo,
        "ref": ref,
        "into": into,
        "files": files,
        "count": len(files),
        "bytes": total,
        "skipped": skipped,
        "in_tree": len(blobs),
        "truncated": bool(data.get("truncated")),
        "rate": rate,
    }


# ----------------------------------------------------------------- commit


def _repo_path_for(local_path):
    """Strip the directory the last pull wrote into, so round trips line up."""
    last = _state["last_pull"]
    normalised = local_path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    if last and last.get("into") not in (None, "", "."):
        root = last["into"].replace("\\", "/").strip("/")
        if normalised.startswith(root + "/"):
            return normalised[len(root) + 1 :]
    return normalised


def _require_write(repo):
    if not _TOKEN:
        return _err(
            "no_token",
            "no token configured, and writing needs one; "
            "call configure(token=...) with a fine grained PAT carrying 'Contents: read and write' "
            "(and 'Pull requests: read and write' for open_pr)",
        )
    if not repo:
        return _err("no_repo", "no repository configured; pull one first or pass repo=")
    return None


def commit(paths, message, branch=None, repo=None):
    """Create blobs, a tree and a commit object. The branch is not moved.

    ``paths`` is a list of local paths, or a dict mapping local path to the path
    it should take in the repository. Call ``push`` to move the ref.
    """
    repo = repo or _state["repo"]
    branch = branch or _state["branch"]
    blocked = _require_write(repo)
    if blocked:
        return blocked

    if isinstance(paths, dict):
        mapping = dict(paths)
    else:
        mapping = {p: _repo_path_for(p) for p in paths}
    if not mapping:
        return _err("nothing_to_commit", "no paths given")

    missing = [p for p in mapping if not os.path.isfile(p)]
    if missing:
        return _err("missing_file", "not on the contents drive: %s" % ", ".join(sorted(missing)))

    head = _request("GET", "/repos/%s/git/ref/heads/%s" % (repo, branch))
    if not head["ok"]:
        return dict(head, repo=repo, branch=branch)
    parent = ((head["data"] or {}).get("object") or {}).get("sha")
    if not parent:
        return _err("bad_response", "the branch head carried no commit sha", status=head["status"])

    base = _request("GET", "/repos/%s/git/trees/%s" % (repo, parent))
    if not base["ok"]:
        return dict(base, repo=repo, branch=branch)
    base_tree = (base["data"] or {}).get("sha")

    entries = []
    files = []
    for local_path in sorted(mapping):
        repo_path = mapping[local_path]
        with open(local_path, "rb") as handle:
            blob = handle.read()
        try:
            payload = {"content": blob.decode("utf-8"), "encoding": "utf-8"}
        except UnicodeDecodeError:
            payload = {"content": base64.b64encode(blob).decode(), "encoding": "base64"}
        created = _request("POST", "/repos/%s/git/blobs" % repo, payload=payload)
        if not created["ok"]:
            return dict(created, repo=repo, branch=branch, failed_on=local_path)
        sha = (created["data"] or {}).get("sha")
        entries.append({"path": repo_path, "mode": "100644", "type": "blob", "sha": sha})
        files.append({"local": local_path, "path": repo_path, "sha": sha, "size": len(blob)})

    tree = _request(
        "POST",
        "/repos/%s/git/trees" % repo,
        payload={"base_tree": base_tree, "tree": entries},
    )
    if not tree["ok"]:
        return dict(tree, repo=repo, branch=branch)
    tree_sha = (tree["data"] or {}).get("sha")

    made = _request(
        "POST",
        "/repos/%s/git/commits" % repo,
        payload={"message": message, "tree": tree_sha, "parents": [parent]},
    )
    if not made["ok"]:
        return dict(made, repo=repo, branch=branch)
    commit_sha = (made["data"] or {}).get("sha")

    _state["repo"] = repo
    _state["pending"] = {
        "repo": repo,
        "branch": branch,
        "sha": commit_sha,
        "tree": tree_sha,
        "parent": parent,
        "message": message,
        "files": files,
    }
    return {
        "ok": True,
        "repo": repo,
        "branch": branch,
        "commit": commit_sha,
        "tree": tree_sha,
        "parent": parent,
        "message": message,
        "files": files,
        "pushed": False,
        "rate": made["rate"],
    }


def push():
    """Move the branch ref to the commit that ``commit`` built."""
    pending = _state["pending"]
    if not pending:
        return _err("nothing_to_push", "no commit is waiting; call commit(...) first")
    blocked = _require_write(pending["repo"])
    if blocked:
        return blocked

    moved = _request(
        "PATCH",
        "/repos/%s/git/refs/heads/%s" % (pending["repo"], pending["branch"]),
        payload={"sha": pending["sha"]},
    )
    if not moved["ok"]:
        return dict(moved, repo=pending["repo"], branch=pending["branch"], commit=pending["sha"])

    _state["pending"] = None
    return {
        "ok": True,
        "repo": pending["repo"],
        "branch": pending["branch"],
        "commit": pending["sha"],
        "ref": ((moved["data"] or {}).get("ref")),
        "files": [f["path"] for f in pending["files"]],
        "pushed": True,
        "rate": moved["rate"],
    }


def open_pr(title, body, head, base="main", repo=None):
    """Open a pull request from ``head`` into ``base``."""
    repo = repo or _state["repo"]
    blocked = _require_write(repo)
    if blocked:
        return blocked

    made = _request(
        "POST",
        "/repos/%s/pulls" % repo,
        payload={"title": title, "body": body, "head": head, "base": base},
    )
    if not made["ok"]:
        return dict(made, repo=repo, head=head, base=base)
    data = made["data"] or {}
    return {
        "ok": True,
        "repo": repo,
        "number": data.get("number"),
        "url": data.get("html_url"),
        "title": data.get("title"),
        "head": head,
        "base": base,
        "state": data.get("state"),
        "rate": made["rate"],
    }


def status(remote=True):
    """What is staged locally and where the remote branch points."""
    out = _public_state()
    pending = _state["pending"]
    out["pending"] = (
        None
        if not pending
        else {
            "commit": pending["sha"],
            "message": pending["message"],
            "branch": pending["branch"],
            "paths": [f["path"] for f in pending["files"]],
        }
    )
    out["last_pull"] = _state["last_pull"]
    out["remote_head"] = None
    if remote and _state["repo"]:
        head = _request(
            "GET", "/repos/%s/git/ref/heads/%s" % (_state["repo"], _state["branch"])
        )
        if head["ok"]:
            out["remote_head"] = ((head["data"] or {}).get("object") or {}).get("sha")
            out["rate"] = head["rate"]
        else:
            out["remote_error"] = {"reason": head["reason"], "message": head["message"]}
    if pending and out["remote_head"] and pending["parent"] != out["remote_head"]:
        out["stale"] = True
    return out
