import pytest

import fetcher as fetcher_module
from config import Settings
from fetcher import FetchError, Fetcher, make_session


def make_fetcher(**settings):
    settings = {"request_delay": 0, "timeout": 5, "user_agent": "TestBot/1.0", **settings}
    s = Settings(**settings)
    return Fetcher(s, session=make_session(s.user_agent, backoff_factor=0))  # без пауз между повторами


def test_get_returns_body_and_sends_user_agent(site):
    site.add("/robots.txt", "", status=404)
    site.add("/page", "<p>hello</p>")
    assert make_fetcher().get(site.base + "/page") == b"<p>hello</p>"
    assert ("/page", "TestBot/1.0") in site.hits


def test_http_error_becomes_fetch_error(site):
    site.add("/robots.txt", "", status=404)
    with pytest.raises(FetchError, match="HTTP 404"):
        make_fetcher().get(site.base + "/missing")


def test_temporary_503_is_retried(site):
    site.add("/robots.txt", "", status=404)
    site.add("/flaky", "finally", flaky=2)
    assert make_fetcher().get(site.base + "/flaky") == b"finally"
    assert site.paths().count("/flaky") == 3     # два сбоя + успех


def test_permanent_503_gives_up_with_a_readable_error(site):
    site.add("/robots.txt", "", status=404)
    site.add("/down", "x", flaky=99)
    with pytest.raises(FetchError, match="продолжает отвечать ошибкой"):
        make_fetcher().get(site.base + "/down")


def test_connection_refused_becomes_fetch_error():
    with pytest.raises(FetchError, match="не удалось подключиться"):
        make_fetcher(respect_robots=False).get("http://127.0.0.1:1/")


# ---------------------------------------------------------------- robots.txt

def test_robots_disallow_blocks_the_page_without_requesting_it(site):
    site.add("/robots.txt", "User-agent: *\nDisallow: /private\n")
    site.add("/private/list", "secret")
    site.add("/public/list", "ok")
    f = make_fetcher()
    assert f.get(site.base + "/public/list") == b"ok"
    with pytest.raises(FetchError, match="robots.txt"):
        f.get(site.base + "/private/list")
    assert "/private/list" not in site.paths()


def test_robots_rules_for_our_user_agent_apply(site):
    site.add("/robots.txt", "User-agent: TestBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    site.add("/page", "x")
    with pytest.raises(FetchError, match="robots.txt"):
        make_fetcher().get(site.base + "/page")


def test_robots_can_be_switched_off_for_own_sites(site):
    site.add("/robots.txt", "User-agent: *\nDisallow: /\n")
    site.add("/page", "mine")
    assert make_fetcher(respect_robots=False).get(site.base + "/page") == b"mine"
    assert "/robots.txt" not in site.paths()


def test_missing_robots_means_everything_is_allowed(site):
    site.add("/page", "ok")   # /robots.txt -> 404
    assert make_fetcher().get(site.base + "/page") == b"ok"


def test_unreachable_robots_means_we_do_not_risk_it(site):
    site.add("/robots.txt", "x", flaky=99)   # всегда 503
    site.add("/page", "ok")
    with pytest.raises(FetchError):
        make_fetcher().get(site.base + "/page")
    assert "/page" not in site.paths()


def test_robots_server_error_without_retries_is_also_not_a_green_light(site):
    # 501 нет в списке кодов, при которых requests делает повтор, поэтому проверяем ветку «5xx» отдельно от 503
    site.add("/robots.txt", "oops", status=501)
    site.add("/page", "ok")
    with pytest.raises(FetchError, match="HTTP 501"):
        make_fetcher().get(site.base + "/page")
    assert "/page" not in site.paths()


def test_robots_is_downloaded_once_per_site(site):
    site.add("/a", "a")
    site.add("/b", "b")
    f = make_fetcher()
    f.get(site.base + "/a")
    f.get(site.base + "/b")
    assert site.paths().count("/robots.txt") == 1


# ---------------------------------------------------------------- вежливость

def test_pause_between_requests_to_the_same_host(site):
    site.add("/a", "a")
    site.add("/b", "b")
    sleeps, now = [], [100.0]
    f = Fetcher(Settings(request_delay=2.0, respect_robots=False),
                session=make_session("t", 0), sleep=sleeps.append, clock=lambda: now[0])
    f.get(site.base + "/a")
    assert sleeps == []              # первый запрос — без ожидания
    now[0] += 0.5
    f.get(site.base + "/b")
    assert sleeps == [pytest.approx(1.5)]   # досыпаем до 2 секунд
    now[0] += 10
    f.get(site.base + "/a")
    assert len(sleeps) == 1          # прошло достаточно времени — ждать не надо


def test_oversized_response_is_refused(site, monkeypatch):
    monkeypatch.setattr(fetcher_module, "MAX_BYTES", 1000)
    site.add("/robots.txt", "", status=404)
    site.add("/huge", "x" * 5000)
    with pytest.raises(FetchError, match="больше"):
        make_fetcher().get(site.base + "/huge")
