"""HTTP servers for the test suite.

Why this exists: werkzeug's development server answers every request with `Connection: close` (it says so in serving.py), so a page that loads
forty files opens forty TCP connections. A parallel run of the UI tests then makes thousands of connections per second, and on macOS each
closed one holds its client port in TIME_WAIT for 30 s while only 16,384 ephemeral ports exist: the machine runs out
(`net::ERR_ADDRESS_INVALID`, `EADDRNOTAVAIL`). waitress keeps connections alive, so a browser reuses a handful of them.

`make_server(host, port, app)` is a drop-in for `werkzeug.serving.make_server(host, port, app, threaded=True)`:
`.server_port`, `.serve_forever()`, `.shutdown()`. If waitress is not installed it falls back to werkzeug (slower, same behaviour).
`free_port()` picks a port that is free right now, below the ephemeral range, so a run never assumes a fixed port or meets another session's server.
"""
from __future__ import annotations

import random
import socket

try:
    import waitress
    from waitress import wasyncore
except ImportError:                                  # pragma: no cover - dev requirement missing: fall back to the slow server
    waitress = None


def free_port(low: int = 20000, high: int = 45000) -> int:
    """a port nothing is bound to right now (random, outside the OS ephemeral range 49152+ that outgoing connections use)"""
    for _ in range(200):
        p = random.randrange(low, high)
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise RuntimeError("no free port found")


class _WaitressServer:
    def __init__(self, app, host: str, port: int, threads: int):
        self._s = waitress.create_server(app, host=host, port=port, threads=threads, ident="test", channel_timeout=300, connection_limit=2000, backlog=1024)
        self.server_port = self._s.effective_port
        self._closing = False

    def serve_forever(self) -> None:
        try:
            self._s.run()
        except OSError:
            if not self._closing:                      # closing the sockets from shutdown() makes the running select() fail once: that is the normal end
                raise

    def shutdown(self) -> None:
        self._closing = True
        self._s.close()
        wasyncore.close_all(self._s._map)             # keep-alive connections still open would keep the event loop (and its thread) alive


def make_server(host: str, port: int, app, threaded: bool = True, threads: int = 16):
    if waitress is not None:
        return _WaitressServer(app, host, port, threads)
    from tests._servers import make_server as werkzeug_make_server
    return werkzeug_make_server(host, port, app, threaded=True)


def start_mock(script, env: dict | None = None, what: str = "mock server", timeout: float = 90.0):
    """start `python <script> <port>` on a free port of our own and wait until /healthz answers -> (process, base url).
    Never reuses a server that is already listening (it could be another session's, with other data). A mock that dies at once (the port
    was taken between the check and the bind) is retried on another port; a slow start on a loaded machine is waited out."""
    import os
    import subprocess
    import sys
    import time
    import urllib.request

    last = ""
    for _ in range(5):
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen([sys.executable, str(script), str(port)], env={**os.environ, **(env or {})}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                last = f"{what} exited with code {proc.returncode} before it answered"
                break
            try:
                with urllib.request.urlopen(url + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        return proc, url
            except Exception:
                pass
            time.sleep(0.1)
        else:
            proc.terminate()
            raise RuntimeError(f"{what} did not start within {timeout:.0f}s")
    raise RuntimeError(last)
