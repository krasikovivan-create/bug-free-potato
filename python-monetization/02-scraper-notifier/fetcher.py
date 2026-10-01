"""Скачивание страниц — вежливо.

Что делает «вежливый» скрейпер:
  * представляется (User-Agent) и ждёт паузу между запросами к одному сайту;
  * читает robots.txt и не лезет туда, куда владелец сайта просит не ходить;
  * при временных сбоях (429, 502, 503 …) повторяет запрос с нарастающей паузой;
  * не качает файлы гигантского размера.
"""
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings

log = logging.getLogger(__name__)

MAX_BYTES = 5_000_000  # страница каталога весит сотни КБ; 5 МБ — с большим запасом


class FetchError(Exception):
    """Страницу не удалось получить (сеть, HTTP-ошибка, robots.txt запрещает)."""


def _describe(exc: requests.RequestException) -> str:
    """Короткая причина сбоя для лога и уведомления (без километровых текстов urllib3)."""
    if isinstance(exc, requests.Timeout):
        return "сайт не ответил вовремя"
    if isinstance(exc, requests.exceptions.RetryError):
        return "сайт продолжает отвечать ошибкой (429/5xx) даже после повторов"
    if isinstance(exc, requests.ConnectionError):
        return "не удалось подключиться к сайту"
    return type(exc).__name__


def make_session(user_agent: str, backoff_factor: float = 1.0) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
    retry = Retry(
        total=3,
        backoff_factor=backoff_factor,          # при 1.0 паузы: 0с, 2с, 4с …
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,        # если сайт сам просит подождать — ждём
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class Fetcher:
    def __init__(self, settings: Settings, session: requests.Session | None = None,
                 sleep=time.sleep, clock=time.monotonic):
        self.settings = settings
        self.session = session or make_session(settings.user_agent)
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}   # хост -> когда обращались в последний раз
        self._robots: dict[str, RobotFileParser | None] = {}  # origin -> правила (None = ограничений нет)

    def get(self, url: str) -> bytes:
        """Возвращает тело страницы. Бросает FetchError, если получить страницу нельзя."""
        self._check_robots(url)
        response = self._request(url, stream=True)
        try:
            if response.status_code >= 400:
                raise FetchError(f"{url}: HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise FetchError(f"{url}: страница больше {MAX_BYTES // 1_000_000} МБ — это точно список объявлений?")
            return bytes(body)
        except requests.RequestException as exc:
            raise FetchError(f"{url}: {_describe(exc)}") from exc
        finally:
            response.close()

    # ------------------------------------------------------------ внутренности

    def _throttle(self, host: str) -> None:
        """Выдерживает паузу request_delay между запросами к одному хосту."""
        last = self._last_request.get(host)
        if last is not None:
            wait = self.settings.request_delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)

    def _request(self, url: str, stream: bool = False) -> requests.Response:
        host = urlsplit(url).netloc
        self._throttle(host)
        try:
            return self.session.get(url, timeout=self.settings.timeout, stream=stream)
        except requests.RequestException as exc:
            raise FetchError(f"{url}: {_describe(exc)}") from exc
        finally:
            self._last_request[host] = self._clock()

    def _check_robots(self, url: str) -> None:
        if not self.settings.respect_robots:
            return
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        rules = self._robots[origin]
        if rules is not None and not rules.can_fetch(self.settings.user_agent, url):
            raise FetchError(f"{url}: запрещено правилами robots.txt (чтобы отключить: respect_robots = false — только для своих сайтов)")

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        url = f"{origin}/robots.txt"
        response = self._request(url)
        try:
            # Правило из RFC 9309: файла нет (4xx) — ограничений нет; сайт недоступен (5xx) — не рискуем.
            if 400 <= response.status_code < 500:
                return None
            if response.status_code >= 500:
                raise FetchError(f"{url}: HTTP {response.status_code}")
            rules = RobotFileParser()
            rules.parse(response.text.splitlines())
            return rules
        finally:
            response.close()
