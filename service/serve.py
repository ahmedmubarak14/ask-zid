"""Local test server for اسأل زد.

Deliberately the standard library and one HTML file. The point of this stage
is to find out whether the answers are any good; a framework, a database and
a deployment pipeline are all things to add once that question is settled,
and every one of them added now is something to debug instead of the answers.

Usage:
    python serve.py --corpus ../data/corpus.jsonl --vectors ../data/vectors.npz
    open http://localhost:8000

The key can be pasted into the page instead of exported; it is sent per
request, kept in the browser only, and never written to disk by the server.
"""

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import answer as answer_mod
from config import resolve_key
from search import Index

HERE = pathlib.Path(__file__).parent
INDEX: Index | None = None


def embed_query(text: str, key: str) -> np.ndarray:
    """One embedding call for the question, with the same model as the corpus."""
    request = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": "text-embedding-3-small", "input": text}).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        vector = json.loads(response.read())["data"][0]["embedding"]
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/ask":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        question = (request.get("question") or "").strip()
        if not question:
            self._send(400, b'{"error":"empty question"}', "application/json")
            return

        # The key may arrive from the UI field, the environment, or a .env.
        # It is used and discarded - never logged, stored, or echoed back.
        try:
            key = resolve_key(self.headers.get("X-OpenAI-Key"))
        except KeyError as exc:
            # KeyError's str() wraps the message in quotes; args[0] is clean.
            self._send(400, json.dumps({"error": exc.args[0]}).encode(),
                       "application/json; charset=utf-8")
            return

        country = request.get("country") or None
        try:
            passages = INDEX.search(question, embed_query(question, key),
                                    k=6, country=country)
        except urllib.error.HTTPError as exc:
            detail = "invalid API key" if exc.code == 401 else f"HTTP {exc.code}"
            self._send(400, json.dumps({"error": f"embedding failed: {detail}"}).encode(),
                       "application/json; charset=utf-8")
            return
        result = answer_mod.answer(question, passages, key)
        # Returned so the test UI can show what was retrieved. When an answer
        # is wrong, the passages say whether retrieval or the model failed —
        # which is the only way to know what to fix.
        result["passages"] = [
            {"n": i + 1, "title": p.get("doc_title"), "url": p.get("source_file"),
             "country": p.get("country"), "score": round(p["score"], 4),
             "text": p["text"][:600]}
            for i, p in enumerate(passages)
        ]
        self._send(200, json.dumps(result, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--vectors", type=pathlib.Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    global INDEX
    INDEX = Index(args.corpus, args.vectors)
    print(f"{len(INDEX.rows):,} chunks loaded — http://localhost:{args.port}")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
