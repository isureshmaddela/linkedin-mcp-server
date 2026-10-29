"""Stand-ins for the services Sites talks to, on one local port.

* Model APIs, each with its real streaming format: Ollama ``/api/chat`` (NDJSON) and ``/api/tags``; OpenAI-compatible
  ``/v1/chat/completions`` (SSE) and ``/v1/models``; Anthropic ``/v1/messages`` (SSE events) and ``/v1/models``;
  Gemini ``/v1beta/models/<m>:streamGenerateContent?alt=sse`` and ``/v1beta/models``.
* Deploy APIs: GitHub (``/gh``: user, repos, contents, git blobs/trees/commits/refs, pages), Netlify (``/netlify``:
  sites, zip deploys) and Vercel (``/vercel``: v13 deployments).

Every request is recorded (method, path, headers, JSON body) so tests can check keys, models and what was pushed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SITE_ANSWER = """<plan>A bright one-page site for Stub Bakery with a hero, a menu and a contact form.</plan>
<file path="index.html">
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Stub Bakery</title><link rel="stylesheet" href="/styles.css"></head>
<body><h1 id="title">Stub Bakery</h1><p>Fresh bread daily.</p><script src="app.js"></script></body>
</html>
</file>
<file path="styles.css">
body { font-family: system-ui; color: #222; }
h1 { color: rgb(200, 80, 20); }
</file>
<file path="app.js">
document.getElementById("title").dataset.ready = "yes";
</file>
<summary>Built Stub Bakery. Next, add a gallery of loaves.</summary>"""

EDIT_ANSWER = """<plan>Make the heading green and add an about page.</plan>
<file path="styles.css">
body { font-family: system-ui; color: #222; }
h1 { color: rgb(20, 160, 60); }
</file>
<file path="about.html">
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>About</title></head><body><h1>About us</h1></body></html>
</file>
<delete path="app.js"/>
<summary>The heading is green now and there is an About page.</summary>"""


class SitesStub:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.answer = SITE_ANSWER
        self.chunk = 40
        self.delay = 0.0
        self.fail_status = 0  # answer every model call with this status
        self.repos: dict[
            str, dict[str, Any]
        ] = {}  # "owner/name" -> {"files": {path: bytes}, "commits": [...], "pages": bool}
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, list[dict[str, Any]]] = {}
        self.commits: dict[str, dict[str, Any]] = {}
        self.netlify_sites: dict[str, dict[str, Any]] = {}
        self.netlify_deploys: list[dict[str, Any]] = []
        self.vercel_deploys: list[dict[str, Any]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> SitesStub:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def model_calls(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.requests
            if r["method"] == "POST"
            and any(
                k in r["path"]
                for k in (
                    "/api/chat",
                    "/chat/completions",
                    "/v1/messages",
                    "streamGenerateContent",
                )
            )
        ]

    def pieces(self) -> list[str]:
        text = self.answer
        return [text[i : i + self.chunk] for i in range(0, len(text), self.chunk)]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def _body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _json(self, status: int, data: Any) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _stream(self, content_type: str, lines: list[bytes]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for line in lines:
                    if stub.delay:
                        time.sleep(stub.delay)
                    self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")

            def _record(self, raw: bytes) -> Any:
                try:
                    data = (
                        json.loads(raw)
                        if raw
                        and self.headers.get("Content-Type", "").startswith(
                            "application/json"
                        )
                        else None
                    )
                except ValueError:
                    data = None
                stub.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "json": data,
                        "raw": raw,
                    }
                )
                return data

            def do_GET(self) -> None:
                self._record(b"")
                self._route("GET", None, b"")

            def do_POST(self) -> None:
                raw = self._body()
                self._route("POST", self._record(raw), raw)

            def do_PUT(self) -> None:
                raw = self._body()
                self._route("PUT", self._record(raw), raw)

            def do_PATCH(self) -> None:
                raw = self._body()
                self._route("PATCH", self._record(raw), raw)

            # -------------------------------------------------------------- routing
            def _route(self, method: str, data: Any, raw: bytes) -> None:
                path = self.path.split("?", 1)[0]
                if path.startswith("/gh/"):
                    return self._github(method, path[3:], data)
                if path.startswith("/netlify/"):
                    return self._netlify(method, path[8:], raw)
                if path.startswith("/vercel/"):
                    return self._vercel(method, path[7:], data)
                if stub.fail_status and method == "POST":
                    return self._json(
                        stub.fail_status, {"error": {"message": "stub refused"}}
                    )
                if path == "/api/tags":
                    return self._json(
                        200,
                        {
                            "models": [
                                {"name": "stub-coder:7b"},
                                {"name": "llama-stub:1b"},
                            ]
                        },
                    )
                if path == "/api/chat":
                    lines = [
                        json.dumps(
                            {
                                "message": {"role": "assistant", "content": p},
                                "done": False,
                            }
                        ).encode()
                        + b"\n"
                        for p in stub.pieces()
                    ]
                    lines.append(
                        json.dumps({"message": {"content": ""}, "done": True}).encode()
                        + b"\n"
                    )
                    return self._stream("application/x-ndjson", lines)
                if path == "/v1/chat/completions":
                    lines = [
                        b"data: "
                        + json.dumps({"choices": [{"delta": {"content": p}}]}).encode()
                        + b"\n\n"
                        for p in stub.pieces()
                    ]
                    return self._stream(
                        "text/event-stream", [*lines, b"data: [DONE]\n\n"]
                    )
                if path == "/v1/messages":
                    events = [
                        b'event: message_start\ndata: {"type":"message_start","message":{"id":"m1"}}\n\n'
                    ]
                    events += [
                        b"event: content_block_delta\ndata: "
                        + json.dumps(
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": p},
                            }
                        ).encode()
                        + b"\n\n"
                        for p in stub.pieces()
                    ]
                    events.append(
                        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
                    )
                    events.append(
                        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
                    )
                    return self._stream("text/event-stream", events)
                if path.startswith("/v1beta/models/") and path.endswith(
                    ":streamGenerateContent"
                ):
                    lines = [
                        b'data: {"candidates":[{"content":{"parts":[{"text":"thinking...","thought":true}]}}]}\n\n'
                    ]
                    lines += [
                        b"data: "
                        + json.dumps(
                            {
                                "candidates": [
                                    {
                                        "content": {
                                            "parts": [{"text": p}],
                                            "role": "model",
                                        }
                                    }
                                ]
                            }
                        ).encode()
                        + b"\n\n"
                        for p in stub.pieces()
                    ]
                    lines.append(
                        b'data: {"candidates":[{"content":{"parts":[{"text":""}]},"finishReason":"STOP"}]}\n\n'
                    )
                    return self._stream("text/event-stream", lines)
                if path == "/v1beta/models":
                    return self._json(
                        200,
                        {
                            "models": [
                                {
                                    "name": "models/gemini-stub-flash",
                                    "supportedGenerationMethods": ["generateContent"],
                                },
                                {
                                    "name": "models/embed",
                                    "supportedGenerationMethods": ["embedContent"],
                                },
                            ]
                        },
                    )
                if path in ("/v1/models", "/models"):
                    return self._json(
                        200, {"data": [{"id": "stub-model-a"}, {"id": "stub-model-b"}]}
                    )
                return self._json(404, {"error": {"message": f"no route {path}"}})

            # -------------------------------------------------------------- GitHub
            def _github(self, method: str, path: str, data: Any) -> None:
                if self.headers.get("Authorization") != "Bearer ghp_stubtoken1234":
                    return self._json(401, {"message": "Bad credentials"})
                if path == "/user":
                    return self._json(200, {"login": "octo"})
                if path == "/user/repos" and method == "POST":
                    name = f"octo/{data['name']}"
                    stub.repos[name] = {
                        "files": {},
                        "commits": [],
                        "pages": False,
                        "private": data.get("private"),
                        "head": "",
                    }
                    if data.get("auto_init"):
                        self._commit(
                            name, {"README.md": b"# init\n"}, [], "Initial commit"
                        )
                    return self._json(201, {"full_name": name})
                parts = path.split("/")
                if len(parts) < 4 or parts[1] != "repos":
                    return self._json(404, {"message": "Not Found"})
                repo = f"{parts[2]}/{parts[3]}"
                rest = "/".join(parts[4:])
                if repo not in stub.repos:
                    return self._json(404, {"message": "Not Found"})
                found = stub.repos[repo]
                if not rest and method == "GET":
                    return self._json(200, {"full_name": repo})
                if rest.startswith("git/ref/heads/"):
                    if not found["head"]:
                        return self._json(409, {"message": "Git Repository is empty."})
                    return self._json(200, {"object": {"sha": found["head"]}})
                if rest == "git/blobs":
                    content = base64.b64decode(data["content"])
                    sha = hashlib.sha1(content).hexdigest()  # noqa: S324 - a stand-in's ids
                    stub.blobs[sha] = content
                    return self._json(201, {"sha": sha})
                if rest == "git/trees":
                    sha = hashlib.sha1(json.dumps(data["tree"]).encode()).hexdigest()  # noqa: S324
                    stub.trees[sha] = data["tree"]
                    return self._json(201, {"sha": sha})
                if rest == "git/commits":
                    sha = hashlib.sha1(json.dumps(data).encode()).hexdigest()  # noqa: S324
                    stub.commits[sha] = data
                    return self._json(201, {"sha": sha})
                if rest.startswith("git/refs"):
                    commit = stub.commits[data["sha"]]
                    found["files"] = {
                        item["path"]: stub.blobs[item["sha"]]
                        for item in stub.trees[commit["tree"]]
                    }
                    found["commits"].append(commit["message"])
                    found["head"] = data["sha"]
                    return self._json(
                        200 if method == "PATCH" else 201,
                        {"object": {"sha": data["sha"]}},
                    )
                if rest.startswith("contents/") and method == "PUT":
                    self._commit(
                        repo,
                        {rest[9:]: base64.b64decode(data["content"])},
                        [],
                        data["message"],
                    )
                    return self._json(201, {})
                if rest == "pages":
                    found["pages"] = True
                    return self._json(
                        201, {"html_url": f"https://octo.github.io/{parts[3]}/"}
                    )
                return self._json(404, {"message": "Not Found"})

            def _commit(
                self,
                repo: str,
                files: dict[str, bytes],
                parents: list[str],
                message: str,
            ) -> None:
                found = stub.repos[repo]
                found["files"].update(files)
                found["commits"].append(message)
                found["head"] = hashlib.sha1(
                    message.encode() + str(len(found["commits"])).encode()
                ).hexdigest()  # noqa: S324

            # -------------------------------------------------------------- Netlify and Vercel
            def _netlify(self, method: str, path: str, raw: bytes) -> None:
                if self.headers.get("Authorization") != "Bearer nfp_stubtoken1234":
                    return self._json(401, {"message": "Access Denied"})
                if path == "/sites" and method == "POST":
                    site_id = f"site{len(stub.netlify_sites) + 1}"
                    stub.netlify_sites[site_id] = {
                        "id": site_id,
                        "ssl_url": f"https://{site_id}.netlify.app",
                    }
                    return self._json(201, stub.netlify_sites[site_id])
                parts = path.split("/")
                if len(parts) >= 3 and parts[1] == "sites":
                    site = stub.netlify_sites.get(parts[2])
                    if not site:
                        return self._json(404, {"message": "Not Found"})
                    if len(parts) == 3:
                        return self._json(200, site)
                    if parts[3] == "deploys":
                        names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
                        stub.netlify_deploys.append(
                            {
                                "site": parts[2],
                                "files": names,
                                "type": self.headers.get("Content-Type"),
                            }
                        )
                        return self._json(
                            200,
                            {
                                "id": f"d{len(stub.netlify_deploys)}",
                                "state": "uploaded",
                            },
                        )
                return self._json(404, {"message": "Not Found"})

            def _vercel(self, method: str, path: str, data: Any) -> None:
                if self.headers.get("Authorization") != "Bearer vc_stubtoken1234":
                    return self._json(403, {"error": {"message": "Not authorized"}})
                if path == "/v13/deployments" and method == "POST":
                    stub.vercel_deploys.append(
                        {
                            "name": data["name"],
                            "files": [f["file"] for f in data["files"]],
                            "target": data.get("target"),
                        }
                    )
                    return self._json(
                        200,
                        {
                            "id": "dpl_1",
                            "url": f"{data['name']}-abc.vercel.app",
                            "readyState": "QUEUED",
                            "alias": [f"{data['name']}.vercel.app"],
                        },
                    )
                return self._json(404, {"error": {"message": "Not Found"}})

        return Handler
