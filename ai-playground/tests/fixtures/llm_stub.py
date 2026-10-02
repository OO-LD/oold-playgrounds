"""OpenAI-compatible server replaying a scripted transcript, for the agent loop.

jupyterlite-ai talks to a model over ``POST /v1/chat/completions``. Pointing the
``generic`` provider at this server makes the loop deterministic: the replies are
fixed, so a test asserts on behaviour of the agent rather than on what a model
happened to say that day.

    python tests/fixtures/llm_stub.py --port 8921

Routes:

    POST   /v1/chat/completions      replay the next step of the active transcript
    GET    /v1/models                one model, for clients that probe

    GET    /__requests               every recorded request as JSON
    GET    /__state                  transcript name, turn, step count
    GET    /__transcripts            the built-in transcript names
    POST   /__script?name=NAME       select a built-in transcript, reset the turn
    POST   /__script                 body {"name": ..., "steps": [...]} for an ad-hoc one

A transcript is a list of steps, one per model turn. A step is either

    {"kind": "tool", "tool": "execute_command", "args": {...}}
    {"kind": "text", "text": "..."}

and the args of a tool step may carry placeholders that are filled from the tool
result of the previous turn, which is how a scripted transcript can name a kernel
id it could not know in advance:

    "{kernelId}"   the kernel the last tool result reported
    "{stdout}"     the stream output the last execute-in-kernel returned

Every request is recorded with the system prompt, the full message list and the
tool results parsed out of it, so a test can assert what the model was actually
sent rather than what the chat shows.

The kernel and the chat run on the origin the site is served from, so every
response carries permissive CORS headers and OPTIONS is answered for the preflight
that a JSON POST with an Authorization header triggers.
"""

import argparse
import http.server
import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

MODEL = "oold-stub"

LIST_KERNELS = "jupyterlab-ai-commands:list-kernels"
EXECUTE_IN_KERNEL = "jupyterlab-ai-commands:execute-in-kernel"
MISSING_COMMAND = "jupyterlab-ai-commands:no-such-command"


def tool_step(command_id, args=None):
    return {
        "kind": "tool",
        "tool": "execute_command",
        "args": {"commandId": command_id, "args": args or {}},
    }


def kernel_step(code):
    return tool_step(EXECUTE_IN_KERNEL, {"kernelId": "{kernelId}", "code": code})


# The nonce is planted in the kernel by the test before the run and is unknown to
# this server, so it can only reach a later request by travelling through the
# kernel and back into the conversation.
SCHEMA_OK_CODE = """import oold_agent
_r = oold_agent.validate_schema("fixture_Measurement.schema.json")
print("AGENT nonce " + OOLD_LOOP_NONCE)
print("AGENT passed " + str(_r.passed))
print("AGENT ok " + str(_r.counts["ok"]))
print("AGENT failures " + ",".join(sorted(c.id for c in _r.failures())) + "|")
"""

SCHEMA_BROKEN_CODE = """import oold_agent
_s = oold_agent.validate_schema("fixture_Broken.schema.json")
print("AGENT passed " + str(_s.passed))
for _c in _s.failures():
    print("AGENT check " + _c.id + " " + (_c.rule or "-"))
_i = oold_agent.validate_instance("fixture_bad.instance.json", "fixture_Measurement.schema.json")
print("AGENT instance passed " + str(_i.passed))
for _c in _i.failures():
    print("AGENT check " + _c.id + " " + _c.message)
"""

KERNEL_ERROR_CODE = """import oold_agent
print(oold_agent.no_such_entry_point())
"""

TRANSCRIPTS = {
    # The good path: find the kernel, validate a schema that passes, report it.
    "schema-ok": [
        tool_step(LIST_KERNELS),
        kernel_step(SCHEMA_OK_CODE),
        {"kind": "text", "text": "Report: fixture_Measurement.schema.json passes.\n{stdout}"},
    ],
    # The failing path: the checks come back as ids, not as prose.
    "schema-broken": [
        tool_step(LIST_KERNELS),
        kernel_step(SCHEMA_BROKEN_CODE),
        {"kind": "text", "text": "Report: the schema fails its checks.\n{stdout}"},
    ],
    # Negative control: a command id that does not exist must come back as a
    # failed tool result rather than as a success nobody looked at.
    "unknown-command": [
        tool_step(MISSING_COMMAND),
        {"kind": "text", "text": "Report: that command does not exist."},
    ],
    # Negative control: a raising cell must reach the model as an error.
    "kernel-error": [
        tool_step(LIST_KERNELS),
        kernel_step(KERNEL_ERROR_CODE),
        {"kind": "text", "text": "Report: the cell raised."},
    ],
}

DEFAULT_TRANSCRIPT = "schema-ok"


class Script:
    def __init__(self):
        self.name = DEFAULT_TRANSCRIPT
        self.steps = list(TRANSCRIPTS[DEFAULT_TRANSCRIPT])
        self.turn = 0
        self.requests = []

    def select(self, name, steps=None):
        self.name = name
        self.steps = list(steps if steps is not None else TRANSCRIPTS[name])
        self.turn = 0
        self.requests = []

    def next_step(self):
        if self.turn < len(self.steps):
            return self.steps[self.turn]
        # Past the end of the script the model simply stops asking for tools.
        return {"kind": "text", "text": "Report: the transcript is finished."}


SCRIPT = Script()


def tool_results_of(messages):
    """Every tool result in the conversation, newest last, parsed where possible."""
    out = []
    for message in messages or []:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        parsed = None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
        out.append(
            {
                "tool_call_id": message.get("tool_call_id"),
                "raw": content if isinstance(content, str) else json.dumps(content),
                "parsed": parsed,
            }
        )
    return out


def stream_text_of(result):
    """The stdout an execute-in-kernel result carries, as one string."""
    if not isinstance(result, dict):
        return ""
    outputs = result.get("outputs")
    if not isinstance(outputs, list):
        return ""
    chunks = []
    for output in outputs:
        if isinstance(output, dict) and output.get("output_type") == "stream":
            chunks.append(output.get("text") or "")
    return "".join(chunks)


def bindings_of(results):
    """Placeholder values taken from the most recent usable tool result."""
    kernel_id = ""
    stdout = ""
    for entry in results:
        payload = entry.get("parsed")
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        kernels = result.get("kernels")
        if isinstance(kernels, list) and kernels:
            first = kernels[0]
            if isinstance(first, dict) and first.get("id"):
                kernel_id = first["id"]
        if result.get("kernelId"):
            kernel_id = result["kernelId"]
        text = stream_text_of(result)
        if text:
            stdout = text
    return {"kernelId": kernel_id, "stdout": stdout}


def fill(value, bindings):
    if isinstance(value, str):
        for key, replacement in bindings.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, dict):
        return {k: fill(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, bindings) for v in value]
    return value


def chunk(payload):
    return "data: " + json.dumps(payload) + "\n\n"


def completion_id():
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def sse_body(step, bindings):
    """The step as an OpenAI streaming chat completion."""
    created = int(time.time())
    ident = completion_id()
    head = {"id": ident, "object": "chat.completion.chunk", "created": created, "model": MODEL}
    parts = [chunk({**head, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})]

    if step["kind"] == "tool":
        args = json.dumps(fill(step["args"], bindings))
        parts.append(
            chunk(
                {
                    **head,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_" + uuid.uuid4().hex[:16],
                                        "type": "function",
                                        "function": {"name": step["tool"], "arguments": args},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
        )
        finish = "tool_calls"
    else:
        text = fill(step["text"], bindings)
        parts.append(
            chunk({**head, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})
        )
        finish = "stop"

    parts.append(chunk({**head, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}))
    parts.append(
        chunk(
            {
                **head,
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
    )
    parts.append("data: [DONE]\n\n")
    return "".join(parts)


def json_body(step, bindings):
    """The same step as a non-streaming chat completion."""
    message = {"role": "assistant", "content": None}
    if step["kind"] == "tool":
        message["tool_calls"] = [
            {
                "id": "call_" + uuid.uuid4().hex[:16],
                "type": "function",
                "function": {
                    "name": step["tool"],
                    "arguments": json.dumps(fill(step["args"], bindings)),
                },
            }
        ]
        finish = "tool_calls"
    else:
        message["content"] = fill(step["text"], bindings)
        finish = "stop"
    return {
        "id": completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llm-stub/1.0"

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/__requests":
            self._send(200, SCRIPT.requests)
            return
        if path == "/__state":
            self._send(
                200,
                {
                    "transcript": SCRIPT.name,
                    "turn": SCRIPT.turn,
                    "steps": len(SCRIPT.steps),
                    "recorded": len(SCRIPT.requests),
                },
            )
            return
        if path == "/__transcripts":
            self._send(200, sorted(TRANSCRIPTS))
            return
        if path in ("/v1/models", "/models"):
            self._send(
                200,
                {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "oold"}]},
            )
            return
        self._send(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body = self._read_json()

        if parsed.path == "/__script":
            payload = body or {}
            name = query.get("name", [payload.get("name", DEFAULT_TRANSCRIPT)])[0]
            steps = payload.get("steps")
            if steps is None and name not in TRANSCRIPTS:
                self._send(400, {"error": {"message": f"no transcript named {name}"}})
                return
            SCRIPT.select(name, steps)
            self._send(200, {"ok": True, "transcript": SCRIPT.name, "steps": len(SCRIPT.steps)})
            return

        if parsed.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return

        request = body or {}
        messages = request.get("messages") or []
        system = next(
            (m.get("content") for m in messages if m.get("role") == "system"),
            "",
        )
        results = tool_results_of(messages)
        bindings = bindings_of(results)
        tool_names = sorted(
            t.get("function", {}).get("name", "") for t in request.get("tools") or []
        )
        # The chat asks the model for a conversation title through a transient
        # call that declares no tools. Letting it consume a step would shift the
        # whole transcript by one, so it is answered off-script.
        scripted = bool(tool_names)
        step = SCRIPT.next_step() if scripted else {"kind": "text", "text": "oold session"}
        filled = fill(step, bindings)
        streaming = bool(request.get("stream"))

        SCRIPT.requests.append(
            {
                "at": time.time(),
                "turn": SCRIPT.turn if scripted else None,
                "scripted": scripted,
                "transcript": SCRIPT.name,
                "stream": streaming,
                "model": request.get("model"),
                # The value is never stored: a recording is one more place a
                # pasted key could end up.
                "authorization": bool(self.headers.get("Authorization")),
                "system": system if isinstance(system, str) else json.dumps(system),
                "roles": [m.get("role") for m in messages],
                "messages": messages,
                "tool_names": tool_names,
                "tool_results": results,
                "bindings": bindings,
                "emitted": filled,
            }
        )
        if scripted:
            SCRIPT.turn += 1

        if streaming:
            self._send(200, sse_body(step, bindings).encode(), "text/event-stream; charset=utf-8")
        else:
            self._send(200, json_body(step, bindings))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8921)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"llm stub on http://{args.host}:{args.port}/v1", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
