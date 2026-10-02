"""Fixture server speaking the slice of the GitHub REST API that oold_git uses.

Writes against real GitHub are not testable in CI, so the suite points
``oold_git.configure(api_base=...)`` here instead. Every request is recorded so a
test can assert the blob contents, tree entries and commit message that actually
arrived, not just the status codes.

    python tests/fixtures/github_api.py --port 8913

Routes:

    GET    /repos/{o}/{r}/git/trees/{sha}       tree, ``?recursive=1`` honoured
    GET    /repos/{o}/{r}/contents/{path}       base64 blob, ``?ref=`` honoured
    GET    /repos/{o}/{r}/git/ref/heads/{b}     branch head
    POST   /repos/{o}/{r}/git/blobs             create blob
    POST   /repos/{o}/{r}/git/trees             create tree
    POST   /repos/{o}/{r}/git/commits           create commit
    PATCH  /repos/{o}/{r}/git/refs/heads/{b}    move branch
    POST   /repos/{o}/{r}/pulls                 open pull request

    GET    /__requests                          recorded requests as JSON
    GET    /__state                             refs, blobs, trees, commits, pulls
    POST   /__reset                             drop the recording and reseed
    POST   /__reset?recording=1                 drop the recording, keep the objects

Two repository names answer with errors instead of data:
``fixture/unauthorized`` returns 401, ``fixture/ratelimited`` returns 403 with
the rate limit headers set.

The kernel runs on a different port than this server, so every response carries
permissive CORS headers and OPTIONS is answered for the preflight that a JSON
POST with an Authorization header triggers.
"""

import argparse
import base64
import hashlib
import http.server
import json
import time
from urllib.parse import parse_qs, unquote, urlparse

API_VERSION = "2022-11-28"
DOC_URL = "https://docs.github.com/rest"

UNAUTHORIZED_REPO = "fixture/unauthorized"
RATELIMITED_REPO = "fixture/ratelimited"
DEMO_REPO = "fixture/demo"

SEED_FILES = {
    "README.md": "# fixture demo\n",
    "data/sample_a.csv": "id,value\n1,2\n",
    "data/sample_b.csv": "id,value\n3,4\n",
    "context/context.json": '{"@context": {}}\n',
}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def object_sha(kind: str, payload) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha1(kind.encode() + b"\0" + raw).hexdigest()


class Store:
    def __init__(self):
        self.reset()

    def reset(self):
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.refs = {}
        self.pulls = []
        self.requests = []
        self.seed(DEMO_REPO, SEED_FILES)

    def seed(self, repo, files):
        entries = []
        for path, text in sorted(files.items()):
            data = text.encode()
            sha = blob_sha(data)
            self.blobs[sha] = data
            entries.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": sha, "size": len(data)}
            )
        tree_sha = object_sha("tree", entries)
        self.trees[tree_sha] = entries
        commit = {
            "message": "seed",
            "tree": tree_sha,
            "parents": [],
            "author": {"name": "fixture", "email": "fixture@example.invalid"},
        }
        commit_sha = object_sha("commit", commit)
        self.commits[commit_sha] = commit
        self.refs[(repo, "main")] = commit_sha

    def tree_entries(self, sha):
        return self.trees.get(sha)

    def resolve(self, repo, ref):
        """Accept a branch name, a commit sha or a tree sha, return a tree sha."""
        commit_sha = self.refs.get((repo, ref), ref)
        commit = self.commits.get(commit_sha)
        if commit is not None:
            return commit["tree"]
        if commit_sha in self.trees:
            return commit_sha
        return None

    def blob_at(self, repo, ref, path):
        tree_sha = self.resolve(repo, ref)
        if tree_sha is None:
            return None
        for entry in self.trees.get(tree_sha, []):
            if entry["path"] == path:
                return entry
        return None


STORE = Store()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "github-fixture/1.0"

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------- plumbing

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-GitHub-Api-Version", API_VERSION)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message, extra_headers=None):
        self._send(status, {"message": message, "documentation_url": DOC_URL}, extra_headers)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"__raw__": raw.decode("utf-8", "replace")}

    def _record(self, method, parsed, query, body):
        STORE.requests.append(
            {
                "method": method,
                "path": parsed.path,
                "query": {k: v[0] for k, v in query.items()},
                # The value is never stored: a recording file is one more place a
                # pasted token could end up.
                "authorization": bool(self.headers.get("Authorization")),
                "accept": self.headers.get("Accept"),
                "json": body,
                "at": time.time(),
            }
        )

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    # ------------------------------------------------------------- routing

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body = self._read_json() if method in ("POST", "PATCH") else None

        if parsed.path == "/__requests" and method == "GET":
            self._send(200, STORE.requests)
            return
        if parsed.path == "/__state" and method == "GET":
            self._send(
                200,
                {
                    "refs": {f"{repo}#{ref}": sha for (repo, ref), sha in STORE.refs.items()},
                    "blobs": {sha: data.decode("utf-8", "replace") for sha, data in STORE.blobs.items()},
                    "trees": STORE.trees,
                    "commits": STORE.commits,
                    "pulls": STORE.pulls,
                },
            )
            return
        if parsed.path == "/__reset" and method == "POST":
            # Recording only, so a test can assert on the requests of one step
            # without rewinding the refs an earlier step moved.
            if query.get("recording", ["0"])[0] in ("1", "true"):
                STORE.requests = []
            else:
                STORE.reset()
            self._send(200, {"ok": True})
            return

        self._record(method, parsed, query, body)

        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        if len(parts) < 3 or parts[0] != "repos":
            self._error(404, "Not Found")
            return

        repo = f"{parts[1]}/{parts[2]}"
        rest = parts[3:]

        if repo == UNAUTHORIZED_REPO:
            self._error(401, "Bad credentials")
            return
        if repo == RATELIMITED_REPO:
            reset_at = int(time.time()) + 1800
            self._error(
                403,
                "API rate limit exceeded for 203.0.113.7. (But here's the good news: "
                "Authenticated requests get a higher rate limit.)",
                {
                    "X-RateLimit-Limit": "60",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Used": "60",
                    "X-RateLimit-Reset": str(reset_at),
                    "X-RateLimit-Resource": "core",
                },
            )
            return

        handler = self._route(method, rest)
        if handler is None:
            self._error(404, "Not Found")
            return
        handler(repo, rest, query, body)

    def _route(self, method, rest):
        if method == "GET":
            if rest[:2] == ["git", "trees"] and len(rest) == 3:
                return self._get_tree
            if rest[:3] == ["git", "ref", "heads"] and len(rest) >= 4:
                return self._get_ref
            if rest[:1] == ["contents"] and len(rest) >= 2:
                return self._get_contents
        if method == "POST":
            if rest == ["git", "blobs"]:
                return self._post_blob
            if rest == ["git", "trees"]:
                return self._post_tree
            if rest == ["git", "commits"]:
                return self._post_commit
            if rest == ["pulls"]:
                return self._post_pull
        if method == "PATCH":
            if rest[:3] == ["git", "refs", "heads"] and len(rest) >= 4:
                return self._patch_ref
        return None

    # ------------------------------------------------------------ handlers

    def _get_tree(self, repo, rest, query, body):
        tree_sha = STORE.resolve(repo, rest[2])
        if tree_sha is None:
            self._error(404, "Not Found")
            return
        entries = STORE.tree_entries(tree_sha) or []
        if query.get("recursive", ["0"])[0] not in ("1", "true"):
            entries = [e for e in entries if "/" not in e["path"]]
        self._send(
            200,
            {"sha": tree_sha, "url": self.path, "tree": entries, "truncated": False},
        )

    def _get_ref(self, repo, rest, query, body):
        branch = "/".join(rest[3:])
        sha = STORE.refs.get((repo, branch))
        if sha is None:
            self._error(404, "Not Found")
            return
        self._send(
            200,
            {
                "ref": f"refs/heads/{branch}",
                "node_id": "fixture",
                "object": {"sha": sha, "type": "commit"},
            },
        )

    def _get_contents(self, repo, rest, query, body):
        path = "/".join(rest[1:])
        ref = query.get("ref", ["main"])[0]
        entry = STORE.blob_at(repo, ref, path)
        if entry is None:
            self._error(404, "Not Found")
            return
        data = STORE.blobs[entry["sha"]]
        self._send(
            200,
            {
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "sha": entry["sha"],
                "size": len(data),
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
            },
        )

    def _require_auth(self):
        if self.headers.get("Authorization"):
            return True
        self._error(401, "Requires authentication")
        return False

    def _post_blob(self, repo, rest, query, body):
        if not self._require_auth():
            return
        body = body or {}
        content = body.get("content", "")
        if body.get("encoding") == "base64":
            data = base64.b64decode(content)
        else:
            data = content.encode()
        sha = blob_sha(data)
        STORE.blobs[sha] = data
        self._send(201, {"sha": sha, "url": self.path})

    def _post_tree(self, repo, rest, query, body):
        if not self._require_auth():
            return
        body = body or {}
        entries = {}
        base = body.get("base_tree")
        if base:
            for entry in STORE.trees.get(base, []):
                entries[entry["path"]] = dict(entry)
        for entry in body.get("tree", []):
            entries[entry["path"]] = {
                "path": entry["path"],
                "mode": entry.get("mode", "100644"),
                "type": entry.get("type", "blob"),
                "sha": entry.get("sha"),
                "size": len(STORE.blobs.get(entry.get("sha"), b"")),
            }
        ordered = [entries[k] for k in sorted(entries)]
        sha = object_sha("tree", ordered)
        STORE.trees[sha] = ordered
        self._send(201, {"sha": sha, "tree": ordered, "truncated": False})

    def _post_commit(self, repo, rest, query, body):
        if not self._require_auth():
            return
        body = body or {}
        commit = {
            "message": body.get("message", ""),
            "tree": body.get("tree"),
            "parents": body.get("parents", []),
        }
        sha = object_sha("commit", {**commit, "nonce": len(STORE.commits)})
        STORE.commits[sha] = commit
        self._send(
            201,
            {
                "sha": sha,
                "message": commit["message"],
                "tree": {"sha": commit["tree"]},
                "parents": [{"sha": p} for p in commit["parents"]],
            },
        )

    def _patch_ref(self, repo, rest, query, body):
        if not self._require_auth():
            return
        branch = "/".join(rest[3:])
        body = body or {}
        sha = body.get("sha")
        if sha not in STORE.commits:
            self._error(422, "Object does not exist")
            return
        STORE.refs[(repo, branch)] = sha
        self._send(
            200,
            {"ref": f"refs/heads/{branch}", "object": {"sha": sha, "type": "commit"}},
        )

    def _post_pull(self, repo, rest, query, body):
        if not self._require_auth():
            return
        body = body or {}
        number = len(STORE.pulls) + 1
        pull = {
            "number": number,
            "title": body.get("title", ""),
            "body": body.get("body", ""),
            "head": {"ref": body.get("head", "")},
            "base": {"ref": body.get("base", "")},
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "state": "open",
        }
        STORE.pulls.append(pull)
        self._send(201, pull)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8913)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"github fixture on http://{args.host}:{args.port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
