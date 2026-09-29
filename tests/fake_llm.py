"""A fake OpenAI-compatible server (chat completions + embeddings) for exercising the real
model code paths without a key.

Behaviour is scripted from the prompts:
  planner prompt   -> a valid JSON plan derived from keywords in the question; the marker
                      INVALID_PLAN in the question returns JSON that fails the schema, NOT_JSON returns prose
  verifier prompt  -> "no" when the sentence contains "invented", else "yes"
  answer prompt    -> one cited sentence per <doc> in the prompt (first sentence of each doc);
                      markers in the question add misbehaviour:
                        HALLUCINATE   an invented number, cited to the first doc
                        FOREIGN_CITE  a sentence citing confluence:9001 (never in the prompt for jdoe)
                        UNCITED       an uncited claim
  /embeddings      -> deterministic 64-dim hashed vectors

Run in-process on a random port with `serve()`.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request

from internal_brain.core.embeddings import HashEmbedder

DOC_RE = re.compile(r'<doc id="([^"]+)"[^>]*>\n(.*?)\n</doc>', re.S)
SENT_RE = re.compile(r"(?<=[.!?])\s+")

app = FastAPI()
calls: list[dict] = []


def _plan(question: str) -> str:
    q = question.lower()
    if "INVALID_PLAN" in question:
        return json.dumps({"platforms": ["mars"], "subqueries": []})
    if "NOT_JSON" in question:
        return "I would rather write prose than JSON."
    platforms = ["confluence", "jira", "slack", "gdrive"]
    window = 7 if "last week" in q else None
    subqueries = [
        {"platform": p, "query": re.sub(r"[^a-z0-9 ]", " ", q)[:120].strip() or "status", "window_days": window if p == "slack" else None, "container": None}
        for p in platforms
    ]
    intent = "status" if "status" in q else "root_cause" if "root cause" in q else "lookup"
    return json.dumps({"platforms": platforms, "subqueries": subqueries, "intent": intent, "window_days": window})


def _answer(system: str, question: str) -> str:
    docs = DOC_RE.findall(system)
    if not docs:
        return "NO_ANSWER"
    sentences = []
    for doc_id, text in docs[:4]:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        body = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
        first = SENT_RE.split(body)[0].rstrip(" .")
        sentences.append(f"{first} [doc:{doc_id}].")
    first_doc = docs[0][0]
    if "HALLUCINATE" in question:
        sentences.append(f"The invented figure is 999 [doc:{first_doc}].")
    if "FOREIGN_CITE" in question:
        sentences.append("There is a secret report about the breach [doc:confluence:9001].")
    if "UNCITED" in question:
        sentences.append("Also, the admin password is hunter2.")
    return " ".join(sentences)


@app.post("/v1/chat/completions")
@app.post("/hunyuan/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    model = body.get("model")
    calls.append({"model": model, "system": system[:80], "user": user[:80], "keys": sorted(body), "body_extra": {k: v for k, v in body.items() if k not in ("messages",)}})
    if model == "no-json-mode" and "response_format" in body:
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": {"message": "response_format is not supported", "type": "invalid_request_error"}}, status_code=400)
    if "JSON retrieval plan" in system:
        content = _plan(user)
        if model == "thinker":
            content = f"<think>the user wants a plan</think>Here is the plan:\n```json\n{content}\n```"
    elif "supported by an excerpt" in system:
        sentence = user.split("Sentence:")[-1]
        content = "no" if "invented" in sentence.lower() else "yes"
    else:
        content = _answer(system, user)
        if model == "thinker":
            content = "<think>I could mention [doc:confluence:9001] but it is not in the documents.</think>" + content
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": body.get("model", "fake"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": len(system) // 4, "completion_tokens": len(content) // 4, "total_tokens": (len(system) + len(content)) // 4},
    }


_embedder = HashEmbedder(dim=64)


@app.post("/v1/embeddings")
@app.post("/hunyuan/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    vectors = _embedder.embed(inputs)
    return {
        "object": "list",
        "model": body.get("model", "fake-embed"),
        "data": [{"object": "embedding", "index": i, "embedding": vectors[i].tolist()} for i in range(len(inputs))],
        "usage": {"prompt_tokens": sum(len(t) // 4 for t in inputs), "total_tokens": sum(len(t) // 4 for t in inputs)},
    }


class FakeServer:
    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> "FakeServer":
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("fake LLM server did not start")
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def serve() -> FakeServer:
    return FakeServer().start()
