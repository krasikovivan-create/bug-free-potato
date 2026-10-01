"""Отправка уведомлений в Telegram через Bot API (обычные HTTP-запросы, без библиотеки бота).

Для одностороннего «прислать сообщение» библиотека python-telegram-bot не нужна:
достаточно одного POST-запроса на https://api.telegram.org/bot<TOKEN>/sendMessage.
"""
import logging
import time
from html import escape

import requests

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.telegram.org"
MAX_TITLE_LEN = 150
MAX_PRICE_LEN = 40


class NotifyError(Exception):
    """Не удалось отправить сообщение в Telegram."""


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_items(source_name: str, items) -> str:
    """Одно сообщение со списком новых объявлений (HTML-разметка Telegram)."""
    lines = [f"🆕 <b>{escape(source_name)}</b> — new: {len(items)}"]
    for item in items:
        price = f" — {escape(_shorten(item.price_text, MAX_PRICE_LEN))}" if item.price_text else ""
        # quote=True экранирует кавычки и & — иначе странная ссылка сломает разметку сообщения.
        link = f'<a href="{escape(item.url, quote=True)}">{escape(_shorten(item.title, MAX_TITLE_LEN))}</a>'
        lines.append(f"• {link}{price}")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, api_base: str = DEFAULT_API_BASE,
                 session: requests.Session | None = None, sleep=time.sleep):
        self.token = token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")
        self.session = session or requests.Session()
        self._sleep = sleep

    def _redact(self, text: str) -> str:
        # Тексты сетевых ошибок содержат полный URL запроса, а в нём — токен бота.
        # Токен — это пароль, в логи и сообщения он попадать не должен.
        return text.replace(self.token, "<token>")

    def _call(self, method: str, payload: dict):
        url = f"{self.api_base}/bot{self.token}/{method}"
        for attempt in range(1, 4):
            try:
                response = self.session.post(url, json=payload, timeout=15)
            except requests.RequestException as exc:
                raise NotifyError(self._redact(f"сеть: {type(exc).__name__}: {exc}")) from None
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code == 429 and attempt < 3:
                # Слишком часто шлём: Telegram сам говорит, сколько подождать.
                wait = int(data.get("parameters", {}).get("retry_after", 5))
                if wait <= 60:
                    log.warning("Telegram просит подождать %s с", wait)
                    self._sleep(wait + 1)
                    continue
            if not data.get("ok"):
                raise NotifyError(self._redact(f"Telegram ответил {response.status_code}: {data.get('description', 'без описания')}"))
            return data.get("result")
        raise NotifyError("Telegram не принял сообщение после трёх попыток")

    def send(self, text: str) -> None:
        self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},  # без громоздких превью ссылок
        })

    def find_chats(self) -> list[tuple[int, str]]:
        """Личные чаты, которые писали боту: [(chat_id, имя)]. Для команды `chat-id`."""
        updates = self._call("getUpdates", {"timeout": 0}) or []
        chats: dict[int, str] = {}
        for update in updates:
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and "id" in chat:
                name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username", "")
                chats[chat["id"]] = name
        return list(chats.items())
