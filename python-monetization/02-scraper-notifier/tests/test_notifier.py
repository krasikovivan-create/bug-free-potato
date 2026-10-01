import pytest
import requests

from listing import Item
from notifier import NotifyError, TelegramNotifier, format_items

TOKEN = "123456:SECRET-token"


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeSession:
    """Записывает запросы и отвечает заранее заготовленными ответами (или бросает исключение)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(result=True):
    return FakeResponse(200, {"ok": True, "result": result})


def item(title="Title", url="http://x.test/1", price_text="£5"):
    return Item("Shop", url, title, 5.0, price_text)


def test_format_items():
    text = format_items("Shop", [item("A book", "http://x.test/a"), item("No price", "http://x.test/b", None)])
    lines = text.split("\n")
    assert lines[0] == "🆕 <b>Shop</b> — new: 2"
    assert lines[1] == '• <a href="http://x.test/a">A book</a> — £5'
    assert lines[2] == '• <a href="http://x.test/b">No price</a>'


def test_format_items_escapes_everything_user_controlled():
    nasty = item(title='<b>Hack</b> & "co"', url='http://x.test/?a=1&b="2"', price_text="<i>5</i>")
    text = format_items("<Shop & Co>", [nasty])
    assert "<Shop" not in text and "&lt;Shop &amp; Co&gt;" in text
    assert "<b>Hack</b>" not in text and "&lt;b&gt;Hack&lt;/b&gt; &amp; &quot;co&quot;" in text
    assert 'href="http://x.test/?a=1&amp;b=&quot;2&quot;"' in text
    assert "<i>5</i>" not in text


def test_long_title_and_price_are_shortened():
    text = format_items("Shop", [item(title="T" * 500, price_text="9" * 500)])
    assert len(text) < 400
    assert "…" in text


def test_send_posts_the_expected_payload():
    session = FakeSession(ok())
    TelegramNotifier(TOKEN, "42", "https://tg.test/", session).send("hello")
    url, payload, timeout = session.calls[0]
    assert url == f"https://tg.test/bot{TOKEN}/sendMessage"
    assert payload == {"chat_id": "42", "text": "hello", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    assert timeout > 0


def test_telegram_error_description_is_reported():
    session = FakeSession(FakeResponse(400, {"ok": False, "description": "Bad Request: chat not found"}))
    with pytest.raises(NotifyError, match="chat not found"):
        TelegramNotifier(TOKEN, "42", session=session).send("x")


def test_non_json_reply_is_an_error_not_a_crash():
    with pytest.raises(NotifyError, match="502"):
        TelegramNotifier(TOKEN, "42", session=FakeSession(FakeResponse(502))).send("x")


def test_network_error_does_not_leak_the_token():
    boom = requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: /bot{TOKEN}/sendMessage")
    with pytest.raises(NotifyError) as exc:
        TelegramNotifier(TOKEN, "42", session=FakeSession(boom)).send("x")
    assert TOKEN not in str(exc.value)
    assert "<token>" in str(exc.value)


def test_api_error_text_does_not_leak_the_token():
    session = FakeSession(FakeResponse(401, {"ok": False, "description": f"Unauthorized {TOKEN}"}))
    with pytest.raises(NotifyError) as exc:
        TelegramNotifier(TOKEN, "42", session=session).send("x")
    assert TOKEN not in str(exc.value)


def test_rate_limit_waits_and_retries():
    sleeps = []
    session = FakeSession(FakeResponse(429, {"ok": False, "parameters": {"retry_after": 3}}), ok())
    TelegramNotifier(TOKEN, "42", session=session, sleep=sleeps.append).send("x")
    assert len(session.calls) == 2
    assert sleeps == [4]


def test_rate_limit_gives_up_after_three_attempts():
    limited = FakeResponse(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 1}})
    session = FakeSession(limited, limited, limited)
    with pytest.raises(NotifyError, match="429"):
        TelegramNotifier(TOKEN, "42", session=session, sleep=lambda s: None).send("x")
    assert len(session.calls) == 3


def test_absurd_retry_after_is_not_obeyed():
    session = FakeSession(FakeResponse(429, {"ok": False, "description": "slow down", "parameters": {"retry_after": 99999}}))
    sleeps = []
    with pytest.raises(NotifyError):
        TelegramNotifier(TOKEN, "42", session=session, sleep=sleeps.append).send("x")
    assert sleeps == []


def test_find_chats_returns_only_private_chats_once_each():
    updates = [
        {"message": {"chat": {"id": 7, "type": "private", "first_name": "Ivan", "last_name": "K"}}},
        {"message": {"chat": {"id": 7, "type": "private", "first_name": "Ivan"}}},
        {"message": {"chat": {"id": -100, "type": "group", "title": "Team"}}},
        {"edited_message": {"chat": {"id": 9, "type": "private"}}},
        {"message": {"chat": {"id": 8, "type": "private", "username": "anna"}}},
    ]
    chats = TelegramNotifier(TOKEN, "", session=FakeSession(ok(updates))).find_chats()
    assert chats == [(7, "Ivan"), (8, "anna")]
