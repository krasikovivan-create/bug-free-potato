"""Сквозные тесты: настоящий `python scraper.py ...` против локального сайта и фейкового Telegram."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import listing_html

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "999:TOP-SECRET-TOKEN"


@pytest.fixture
def project(tmp_path, site, telegram):
    """Папка с config.toml для фейкового сайта + функция запуска CLI."""
    site.add("/robots.txt", "User-agent: *\nAllow: /\n")
    site.add("/", listing_html([("Old book", "/old", "£5.00")]))

    def write_config(extra_settings="", extra_source=""):
        (tmp_path / "config.toml").write_text(f'''
request_delay = 0
{extra_settings}

[[sources]]
name = "Demo shop"
url = "{site.base}/"
item = "article.product_pod"
title = "h3 a"
title_attr = "title"
link = "h3 a"
price = "p.price_color"
{extra_source}
''', encoding="utf-8")

    write_config()

    def run(*args, token=TOKEN, chat_id="42", api_base=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
        env.update(DB_PATH=str(tmp_path / "scraper.db"), PYTHONIOENCODING="utf-8")
        if token:
            env["TELEGRAM_BOT_TOKEN"] = token
        if chat_id:
            env["TELEGRAM_CHAT_ID"] = chat_id
        env["TELEGRAM_API_BASE"] = api_base or telegram.base
        proc = subprocess.run([sys.executable, str(ROOT / "scraper.py"), *args],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        return proc

    run.write_config = write_config
    run.path = tmp_path
    return run


def test_check_shows_what_was_found_and_touches_neither_db_nor_telegram(project, telegram):
    proc = project("check")
    assert proc.returncode == 0, proc.stderr
    assert "Demo shop" in proc.stdout and "Old book" in proc.stdout and "£5.00" in proc.stdout
    assert telegram.requests == []
    assert not (project.path / "scraper.db").exists()


def test_once_first_run_is_silent_then_new_listing_is_delivered_exactly_once(project, site, telegram):
    assert project("once").returncode == 0
    assert telegram.messages() == []                       # первый проход — только запомнили
    assert (project.path / "scraper.db").exists()

    site.add("/", listing_html([("Fresh book", "/fresh?id=1&ref=a", "£7.50"), ("Old book", "/old", "£5.00")]))
    proc = project("once")
    assert proc.returncode == 0, proc.stderr
    (message,) = telegram.messages()
    assert "Fresh book" in message and "£7.50" in message and "Old book" not in message
    assert f'href="{site.base}/fresh?id=1&amp;ref=a"' in message
    _, payload = telegram.requests[-1]
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"

    project("once")
    assert len(telegram.messages()) == 1                   # повторный запуск ничего не дублирует
    assert TOKEN not in proc.stdout + proc.stderr


def test_filters_from_config_are_applied(project, site, telegram):
    project.write_config(extra_source='include = ["python"]\nmax_price = 20')
    project("once")
    site.add("/", listing_html([
        ("Python basics", "/p1", "£10.00"),
        ("Python for experts", "/p2", "£99.00"),
        ("Gardening", "/g", "£3.00"),
    ]))
    project("once")
    (message,) = telegram.messages()
    assert "Python basics" in message and "experts" not in message and "Gardening" not in message


def test_robots_txt_is_respected_by_the_real_cli(project, site):
    site.add("/robots.txt", "User-agent: *\nDisallow: /\n")
    proc = project("check")
    assert proc.returncode == 1
    assert "robots.txt" in proc.stdout


def test_missing_credentials_give_a_clear_message(project):
    proc = project("once", token="")
    assert proc.returncode == 2
    assert "TELEGRAM_BOT_TOKEN" in proc.stderr and "Traceback" not in proc.stderr


def test_broken_config_gives_a_clear_message(project):
    (project.path / "config.toml").write_text('[[sources]]\nname = "x"\nurl = "not-a-url"\nitem = "li"\ntitle = "a"\nlink = "a"\n')
    proc = project("check")
    assert proc.returncode == 2
    assert "Ошибка в конфиге" in proc.stderr and "http://" in proc.stderr and "Traceback" not in proc.stderr


def test_missing_config_file(project):
    proc = project("check", "--config", "nope.toml")
    assert proc.returncode == 2 and "config.example.toml" in proc.stderr


def test_chat_id_command(project, telegram):
    telegram.updates = [{"message": {"chat": {"id": 12345, "type": "private", "first_name": "Ivan"}}}]
    proc = project("chat-id", chat_id="")
    assert proc.returncode == 0
    assert "TELEGRAM_CHAT_ID=12345" in proc.stdout and "Ivan" in proc.stdout


def test_chat_id_without_any_messages_explains_what_to_do(project, telegram):
    proc = project("chat-id", chat_id="")
    assert proc.returncode == 1 and "/start" in proc.stdout


def test_unreachable_telegram_never_leaks_the_token_into_logs(project):
    project.write_config(extra_settings="notify_on_first_run = true")
    proc = project("once", api_base="http://127.0.0.1:1")      # порт закрыт: сеть недоступна
    out = proc.stdout + proc.stderr
    assert "не удалось отправить в Telegram" in out
    assert TOKEN not in out and "TOP-SECRET" not in out
    assert "Traceback" not in out
