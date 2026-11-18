"""Shopify Admin GraphQL client for THEME PREVIEWS only (read themes, duplicate the live theme into a new unpublished one, read and upsert files).

Reads env SHOPIFY_STORE_DOMAIN and SHOPIFY_THEME_TOKEN. The token is only ever put in the request header: it is never logged, printed, stored or returned, and every error text is scrubbed of it.
This module has NO call that publishes a theme and NO call that deletes one or deletes a file (a test greps the source for both). Mutation names and shapes were read from shopify.dev (API
version 2026-10): themeDuplicate(id, name), themeFilesUpsert(themeId, files[{filename, body{type, value}}]), theme(id){role files(filenames, first, after)}. NOT proven against a real store.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

API_VERSION = "2026-10"
TIMEOUT_S = 25
GID = "gid://shopify/OnlineStoreTheme/"
MAIN = "MAIN"


class ShopifyError(Exception):
    """A scrubbed, plain-English failure. Safe to show to a person."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def configured() -> bool:
    return bool(_env("SHOPIFY_STORE_DOMAIN") and _env("SHOPIFY_THEME_TOKEN"))


def store_domain() -> str:
    return _env("SHOPIFY_STORE_DOMAIN")


def preview_url(theme_id) -> str:
    return f"https://{store_domain()}/?preview_theme_id={numeric_id(theme_id)}"


def numeric_id(theme_id) -> str:
    return str(theme_id).rsplit("/", 1)[-1]


def gid(theme_id) -> str:
    return GID + numeric_id(theme_id)


def scrub(text: str) -> str:
    t = _env("SHOPIFY_THEME_TOKEN")
    out = str(text)
    if t:
        out = out.replace(t, "[hidden]")
    return re.sub(r"shpat_[A-Za-z0-9]+", "[hidden]", out)


class ShopifyThemes:
    """The one seam. Tests replace this class with a fake that has the same methods."""

    def _post(self, query: str, variables: dict) -> dict:
        domain, token = store_domain(), _env("SHOPIFY_THEME_TOKEN")
        if not domain or not token:
            raise ShopifyError("Shopify is not connected yet.")
        req = urllib.request.Request(f"https://{domain}/admin/api/{API_VERSION}/graphql.json", data=json.dumps({"query": query, "variables": variables}).encode(),
                                     headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise ShopifyError(f"Shopify answered with an error ({e.code}).") from None
        except Exception as e:                                                      # network, timeout, bad JSON
            raise ShopifyError("Could not reach Shopify: " + scrub(type(e).__name__)) from None
        if data.get("errors"):
            raise ShopifyError("Shopify rejected the request: " + scrub(json.dumps(data["errors"]))[:300])
        return data.get("data") or {}

    @staticmethod
    def _user_errors(payload: dict | None) -> None:
        errs = (payload or {}).get("userErrors") or []
        if errs:
            raise ShopifyError("Shopify said: " + scrub("; ".join(str(e.get("message") or e.get("code") or e) for e in errs))[:300])

    def get_theme(self, theme_id) -> dict | None:
        d = self._post("query($id: ID!) { theme(id: $id) { id name role } }", {"id": gid(theme_id)})
        t = d.get("theme")
        return {"id": numeric_id(t["id"]), "name": t["name"], "role": t["role"]} if t else None

    def get_live_theme(self) -> dict:
        d = self._post("query { themes(first: 5, roles: [MAIN]) { nodes { id name role } } }", {})
        nodes = (d.get("themes") or {}).get("nodes") or []
        if not nodes:
            raise ShopifyError("Could not find the live theme.")
        t = nodes[0]
        return {"id": numeric_id(t["id"]), "name": t["name"], "role": t["role"]}

    def duplicate_theme(self, source_id, name: str) -> dict:
        d = self._post("mutation($id: ID!, $name: String) { themeDuplicate(id: $id, name: $name) { newTheme { id name role } userErrors { field message } } }",
                       {"id": gid(source_id), "name": name})
        p = d.get("themeDuplicate")
        self._user_errors(p)
        t = (p or {}).get("newTheme")
        if not t:
            raise ShopifyError("Shopify did not return the new theme.")
        return {"id": numeric_id(t["id"]), "name": t["name"], "role": t["role"]}

    def list_files(self, theme_id) -> list[dict]:
        out, after = [], None
        for _ in range(20):
            d = self._post("query($id: ID!, $after: String) { theme(id: $id) { files(first: 250, after: $after) { nodes { filename size } pageInfo { hasNextPage endCursor } } } }",
                           {"id": gid(theme_id), "after": after})
            f = ((d.get("theme") or {}).get("files")) or {}
            out += [{"key": n["filename"], "size": n.get("size")} for n in f.get("nodes") or []]
            if not (f.get("pageInfo") or {}).get("hasNextPage"):
                break
            after = f["pageInfo"]["endCursor"]
        return out

    def read_file(self, theme_id, key: str) -> str | None:
        d = self._post("query($id: ID!, $names: [String!]) { theme(id: $id) { files(filenames: $names, first: 1) { nodes { filename body { ... on OnlineStoreThemeFileBodyText { content } } } } } }",
                       {"id": gid(theme_id), "names": [key]})
        nodes = ((((d.get("theme") or {}).get("files")) or {}).get("nodes")) or []
        if not nodes:
            return None
        return (nodes[0].get("body") or {}).get("content")

    def upsert_file(self, theme_id, key: str, content: str) -> None:
        d = self._post("mutation($themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) { themeFilesUpsert(themeId: $themeId, files: $files) { upsertedThemeFiles { filename } userErrors { field message } } }",
                       {"themeId": gid(theme_id), "files": [{"filename": key, "body": {"type": "TEXT", "value": content}}]})
        self._user_errors(d.get("themeFilesUpsert"))


def client() -> ShopifyThemes:
    """The seam the routes call; tests monkeypatch this to return a fake."""
    return ShopifyThemes()
