"""Тесты плагина без Пульса: подменный API платформы.

    from fastapi.testclient import TestClient
    from pulse_plugin.testing import fake_api
    from my_plugin import app as module

    def test_check(monkeypatch):
        api = fake_api(module.plugin, monkeypatch)
        api.on("GET", "/api/cards", lambda params, body: {"total": 3, "items": []})
        http = TestClient(module.app)
        assert http.post("/commands/check", json={}).status_code == 200
        assert api.signals[0]["priority"] == 75           # plugin.signal(...) — POST /api/cards type=signal
"""

from collections.abc import Callable
from typing import Any

from pulse_plugin import PulseApiError

Handler = Callable[[dict, Any], Any]


class FakePulseApi:
    """Вместо plugin.api: отвечает заданными обработчиками и запоминает запросы. Неизвестный путь — 404, как у
    Пульса на путь без прав, чтобы тест не прошёл молча."""

    def __init__(self):
        self.routes: dict[tuple[str, str], Handler] = {}
        self.calls: list[dict] = []

    def on(self, method: str, path: str, handler: Handler | Any) -> "FakePulseApi":
        """Ответ на METHOD path: функция (params, body) -> JSON или готовое значение."""
        self.routes[(method.upper(), path)] = handler if callable(handler) else (lambda params, body, value=handler: value)
        return self

    def request(self, method: str, path: str, **kwargs) -> Any:
        params, body = kwargs.get("params") or {}, kwargs.get("json")
        self.calls.append({"method": method.upper(), "path": path, "params": params, "body": body})
        handler = self.routes.get((method.upper(), path))
        if handler is None:
            if method.upper() == "POST" and path == "/api/cards" and (body or {}).get("type") == "signal":
                return {"id": f"signal-{len(self.signals)}", **body}
            raise PulseApiError(f"404: в тесте не задан ответ на {method.upper()} {path} (api.on(...))", 404)
        return handler(params, body)

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, json=body)

    def patch(self, path: str, body: Any = None) -> Any:
        return self.request("PATCH", path, json=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    @property
    def signals(self) -> list[dict]:
        """Сигналы, которые плагин завёл (plugin.signal)."""
        return [c["body"] for c in self.calls
                if c["method"] == "POST" and c["path"] == "/api/cards" and (c["body"] or {}).get("type") == "signal"]


def fake_api(plugin, monkeypatch) -> FakePulseApi:
    """Подменить plugin.api на время теста (plugin.api — cached_property: меняем значение в __dict__)."""
    fake = FakePulseApi()
    monkeypatch.setitem(plugin.__dict__, "api", fake)
    return fake
