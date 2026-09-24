"""Fuzz: geen enkele API-route mag op rare invoer met een ongevangen exceptie
(Flask-HTML-500) antwoorden. Een nette JSON-fout (400/401/403/404/409/5xx-met-JSON)
is prima; een traceback is dat niet.

Slaat routes over die hardware/langlopende acties starten of een stream openen."""
import pytest

import config

# chat- en routine-routes doen nu ook mee: met deze bodies bereiken ze het model/de hardware nooit
# (ongeldige of ontbrekende invoer wordt eerder geweigerd) -- juist daar zaten typefouten (S3-25)
SKIP = {"/video_feed", "/api/speedtest", "/api/camera_snapshot"}

BODIES = [
    None, "{}", "[]", '"str"', "null", "123", "{not json", '{"x": null}',
    ('{"lamp": "abc", "id": 123, "note": 5, "time": 5, "message": 5, "station": 5, "volume": "loud", '
     '"position_ms": "x", "device_id": 5, "query": 5, "city": 5, "target": [], "brightness": [], '
     '"color": [], "mode": {}, "code": {}, "password": [], "redirect_url": 5, "playlist_id": 5, '
     '"track_id": 5, "routine": 5, "name": 5, "steps": "x", "settings": 5, "keys": 5}'),
    '{"steps": [1, "x", null, {"action": 5}], "id": "ok", "name": "n"}',
    '{"volume": Infinity, "position_ms": NaN, "target": Infinity}',
    '{"id": "' + "A" * 50_000 + '"}',
]
QUERIES = ["", "?limit=abc", "?limit=-1", "?lamp=abc", "?lamp=-5", "?lamp=99999", "?city=%00%ff",
           "?query=", "?q=%E2%98%83", "?message=", "?sid=" + "x" * 5000]


@pytest.fixture()
def fuzz_client():
    from Dashboard.backend import services as S
    from Dashboard.backend.main import app

    config.set("devices.lamps", [])          # geen echte lamp-verbindingen
    config.set("presence.enabled", False)
    old = dict(app.config)
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)   # zoals productie: exceptie -> 500-pagina
    try:
        with app.test_client() as c:
            yield c, app
    finally:
        app.config.update(old)
    S.reset_services()


def _cases(app):
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint == "static" or rule.rule in SKIP:
            continue
        url = rule.rule
        for arg in rule.arguments:
            url = url.replace(f"<int:{arg}>", "abc").replace(f"<{arg}>", "abc")
        for method in sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS")):
            if method == "GET":
                for q in QUERIES:
                    yield method, url + q, None
            else:
                for b in BODIES:
                    yield method, url, b
                if "<int:" in rule.rule:
                    for n in ("-1", "99999999999999999999"):
                        yield method, rule.rule.replace("<int:index>", n), None


def test_no_route_crashes_on_malformed_input(fuzz_client):
    from Dashboard.backend import main as _main

    client, app = fuzz_client
    _main.unhandled.clear()
    crashes = []
    for method, url, body in _cases(app):
        kw = {"method": method}
        if body is not None:
            kw.update(data=body, content_type="application/json")
        try:
            r = client.open(url, **kw)
        except Exception as exc:  # noqa: BLE001
            crashes.append((method, url[:60], f"exception {exc!r}"[:120]))
            continue
        if r.status_code >= 500 and "json" not in (r.content_type or ""):
            crashes.append((method, url[:60], (body or "")[:50], r.status_code))
    assert not crashes, "\n".join(map(str, crashes[:20]))
    # de globale foutafhandeling maakt van een crash een JSON-500: die mag hier nooit optreden
    assert not _main.unhandled, "\n".join(_main.unhandled)


KEYS = ["brightness", "code", "color", "color_temp", "desc", "device_id", "id", "lamp", "message", "name", "note",
        "password", "playlist_id", "position_ms", "redirect_url", "routine", "sid", "station", "steps", "target",
        "time", "track_id", "volume", "query", "city", "text", "settings", "keys", "state", "mode", "value"]
WRONG = [None, 5, -1, 1.5, True, [], ["a"], {}, {"a": 1}, "", "x" * 300, "\x00", "../../etc/passwd", 10 ** 30]


def test_no_route_crashes_on_a_wrongly_typed_value_for_any_known_key(fuzz_client):
    """Elke bekende body-sleutel met elk verkeerd type, een sleutel tegelijk, op elke schrijf-route: geen
    onverwachte fout (de globale foutafhandeling zou die als JSON-500 verbergen; ``unhandled`` vangt ze)."""
    import json

    from Dashboard.backend import main as _main

    client, app = fuzz_client
    _main.unhandled.clear()
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint == "static" or rule.rule in SKIP:
            continue
        methods = [m for m in rule.methods if m in ("POST", "PUT", "DELETE", "PATCH")]
        if not methods:
            continue
        url = rule.rule
        for arg in rule.arguments:
            url = url.replace(f"<int:{arg}>", "1").replace(f"<{arg}>", "abc")
        routes += [(m, url) for m in methods]
    assert len(routes) > 20
    for method, url in routes:
        for key in KEYS:
            for value in WRONG:
                try:
                    client.open(url, method=method, data=json.dumps({key: value}), content_type="application/json")
                except Exception as exc:  # noqa: BLE001
                    pytest.fail(f"{method} {url} {key}={value!r}: {exc!r}")
    assert not _main.unhandled, "\n".join(sorted(set(_main.unhandled))[:20])
