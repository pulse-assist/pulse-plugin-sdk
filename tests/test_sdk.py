"""SDK без ядра: проверка манифеста, воспроизводимая сборка, запись в каталог, подменный API."""

import json
from pathlib import Path

import pytest

from pulse_plugin import Plugin, PulseApiError, dev
from pulse_plugin.testing import fake_api


def make_plugin(root: Path, **changes) -> Path:
    (root / "backend" / "demo_plugin").mkdir(parents=True)
    (root / "backend" / "demo_plugin" / "app.py").write_text("from pulse_plugin import Plugin\nplugin = Plugin()\napp = plugin.app\n",
                                                             encoding="utf-8")
    (root / "ui").mkdir()
    (root / "ui" / "index.html").write_text("<p>demo</p>", encoding="utf-8")
    (root / "commands.json").write_text(json.dumps([{"name": "sync", "description": "Синхронизация"}]), encoding="utf-8")
    (root / "INSTALL.md").write_text("# Установка\n", encoding="utf-8")
    manifest = {"id": "demo", "name": "Демо", "version": "0.1.0", "pulse": ">=1.1", "permissions": ["cards:read"],
                "runtime": {"kind": "python", "entry": "demo_plugin.app:app", "path": "backend", "health": "/health"},
                "ui": {"dist": "ui"}, "menu": [{"id": "main", "title": "Демо", "page": "index.html#main"}],
                "commands": "commands.json", "jobs": [{"name": "Синхронизация", "cron": "0 * * * *", "command": "sync"}],
                **changes}
    (root / "pulse-plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def test_check_pack_publish(tmp_path):
    root = make_plugin(tmp_path / "demo")
    assert dev.check(root)[1] == []
    a = dev.pack(root, tmp_path / "d1", "https://example.org/r")
    b = dev.pack(root, tmp_path / "d2", "https://example.org/r")
    assert a["entry"]["sha256"] == b["entry"]["sha256"] and a["entry"]["channel"] == "stable"
    (tmp_path / "catalog" / "plugins").mkdir(parents=True)
    target = dev.publish(root, tmp_path / "catalog", tmp_path / "d1", "Первая версия", "me", "MIT", "https://example.org/src")
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["id"] == "demo" and record["versions"][0]["changes"] == "Первая версия"


def test_check_finds_problems(tmp_path):
    root = make_plugin(tmp_path / "demo", permissions=["everything"], menu=[{"id": "x", "title": "X", "page": "nope.html"}])
    problems = dev.check(root)[1]
    assert any("everything" in p for p in problems) and any("nope.html" in p for p in problems)
    with pytest.raises(dev.PluginError):
        dev.pack(root, tmp_path / "out")


def test_check_menu_app(tmp_path):
    """Контракт 1.9: пункт меню со встроенной страницей — путь от «/», право embed и runtime."""
    menu = [{"id": "main", "title": "Доски", "page": "index.html", "app": "/editor"}]
    assert dev.check(make_plugin(tmp_path / "ok", menu=menu, permissions=["embed"]))[1] == []
    problems = dev.check(make_plugin(tmp_path / "bad", menu=[{**menu[0], "app": "//evil.example"}], runtime=None))[1]
    assert any("от «/»" in p for p in problems) and any("право embed" in p for p in problems)
    assert any("нужен runtime" in p for p in problems)

def test_fake_api(monkeypatch):
    plugin = Plugin()
    api = fake_api(plugin, monkeypatch)
    api.on("GET", "/api/cards", lambda params, body: {"total": 1, "type": params.get("type")})
    assert plugin.api.get("/api/cards", type="task") == {"total": 1, "type": "task"}
    plugin.signal("Сигнал", priority=90)
    assert api.signals[0]["priority"] == 90
    with pytest.raises(PulseApiError):
        plugin.api.post("/api/notify", {"text": "x"})
