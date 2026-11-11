"""tests/_servers.py: the test HTTP server must keep connections alive (werkzeug's closes every one, which exhausts the ephemeral ports in a parallel run)."""
import http.client
import threading

from flask import Flask

from tests._servers import free_port, make_server


def _app():
    app = Flask(__name__)

    @app.post("/p")
    def p():
        return "posted"                 # never reads the request body: the next request on the same connection must still parse

    @app.get("/g")
    def g():
        return "got"
    return app


def test_one_connection_serves_many_requests_even_after_an_unread_body():
    srv = make_server("127.0.0.1", 0, _app())
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        c.connect()
        local = c.sock.getsockname()
        for method, path, body, want in (("GET", "/g", None, b"got"), ("POST", "/p", b"x" * 200_000, b"posted"), ("GET", "/g", None, b"got"), ("GET", "/g", None, b"got")):
            c.request(method, path, body=body)
            r = c.getresponse()
            assert r.status == 200 and r.read() == want
            assert c.sock is not None and c.sock.getsockname() == local, "the server closed the connection"
        c.close()
    finally:
        srv.shutdown()
    t.join(5)
    assert not t.is_alive()


def test_free_port_is_free_and_outside_the_ephemeral_range():
    p = free_port()
    assert 20000 <= p < 45000
