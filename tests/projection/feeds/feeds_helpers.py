"""Test doubles: a FakeSession standing in for requests.Session and small builders for API payloads."""
from __future__ import annotations

import json
from typing import Any, Callable


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, text: str | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """routes: list of (predicate(call) -> bool, response | callable(call) -> response | list of responses used in order)."""

    def __init__(self, routes=None):
        self.routes = list(routes or [])
        self.calls: list[dict] = []

    def on(self, pred: Callable[[dict], bool], resp):
        self.routes.append((pred, resp))
        return self

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None):
        call = {"method": method, "url": url, "headers": dict(headers or {}), "params": params, "json": json, "data": data}
        self.calls.append(call)
        for pred, resp in self.routes:
            if pred(call):
                if isinstance(resp, list):
                    r = resp.pop(0) if len(resp) > 1 else resp[0]
                elif callable(resp):
                    r = resp(call)
                else:
                    r = resp
                if isinstance(r, Exception):
                    raise r
                return r
        return FakeResponse(404, {"error": "no route"}, text="no route")


def path_is(suffix: str):
    return lambda c: c["url"].endswith(suffix)


def upstack_session(**payloads) -> FakeSession:
    """payloads: query (list of primary rows), channel (results list), attribution (callable day->primary rows)."""
    s = FakeSession()
    s.on(path_is("/api/query"), lambda c: FakeResponse(200, {"results": [{"data": {"primary": payloads["query"](c["json"]) if callable(payloads["query"]) else payloads["query"]}}]}))
    s.on(path_is("/api/query-channel-details"), lambda c: FakeResponse(200, {"results": payloads["channel"]}))
    s.on(path_is("/api/query-attribution"), lambda c: FakeResponse(200, {"results": [{"data": {"primary": payloads["attribution"](c["json"])}}]}))
    return s
