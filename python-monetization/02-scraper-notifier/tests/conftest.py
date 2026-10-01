"""Общие заготовки для тестов: фейковый сайт и фейковый Telegram на localhost."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from config import parse_config


@pytest.fixture(autouse=True)
def _local_requests_skip_proxy(monkeypatch):
    # В некоторых окружениях весь исходящий трафик идёт через прокси; к localhost он не нужен.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def listing_html(items, next_href=None, charset="utf-8"):
    """Страница в духе books.toscrape.com. items: [(заголовок, href, цена-текстом)]."""
    cards = "\n".join(
        f'<li><article class="product_pod"><h3><a href="{href}" title="{title}">{title[:12]}…</a></h3>'
        f'<div class="product_price"><p class="price_color">{price}</p></div></article></li>'
        for title, href, price in items
    )
    pager = f'<ul class="pager"><li class="next"><a href="{next_href}">next</a></li></ul>' if next_href else ""
    return (
        f'<html><head><meta charset="{charset}"><title>Shop</title></head>'
        f"<body><ol class='row'>{cards}</ol>{pager}</body></html>"
    )


def make_config(**overrides):
    """Конфиг с одним источником «Shop» в стиле books.toscrape.com; overrides меняют поля источника."""
    source = {
        "name": "Shop",
        "url": "http://shop.test/",
        "item": "article.product_pod",
        "title": "h3 a",
        "title_attr": "title",
        "link": "h3 a",
        "price": "p.price_color",
    }
    settings = {"request_delay": 0, "notify_on_first_run": False}
    for key in ("interval_minutes", "request_delay", "timeout", "respect_robots", "notify_on_first_run", "db_path"):
        if key in overrides:
            settings[key] = overrides.pop(key)
    source.update(overrides)
    source = {k: v for k, v in source.items() if v is not None}  # None = «убрать ключ» (в TOML None не бывает)
    return parse_config({**settings, "sources": [source]})


# ---------------------------------------------------------------- фейковый сайт

class Site:
    def __init__(self):
        self.routes = {}      # path -> (status, body, headers)
        self.flaky = {}       # path -> сколько первых ответов вернуть 503
        self.hits = []        # [(path, user-agent)]
        self.base = ""

    def add(self, path, body, status=200, content_type="text/html; charset=utf-8", flaky=0):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.routes[path] = (status, data, {"Content-Type": content_type})
        if flaky:
            self.flaky[path] = flaky

    def paths(self):
        return [path for path, _ in self.hits]


@pytest.fixture
def site():
    state = Site()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state.hits.append((self.path, self.headers.get("User-Agent", "")))
            if state.flaky.get(self.path, 0) > 0:
                state.flaky[self.path] -= 1
                status, body, headers = 503, b"try later", {"Content-Type": "text/plain"}
            else:
                status, body, headers = state.routes.get(self.path, (404, b"not found", {"Content-Type": "text/plain"}))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------- фейковый Telegram

class FakeTelegram:
    def __init__(self):
        self.requests = []    # [(метод, json)]
        self.updates = []     # что отдавать на getUpdates
        self.base = ""

    def messages(self):
        return [payload["text"] for method, payload in self.requests if method == "sendMessage"]


@pytest.fixture
def telegram():
    state = FakeTelegram()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            method = self.path.rsplit("/", 1)[-1]
            state.requests.append((method, payload))
            result = state.updates if method == "getUpdates" else {"message_id": 1}
            body = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()
