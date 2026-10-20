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

def test_check_files_mount_and_isolated(tmp_path):
    """Контракт 1.11: files:mount нужен runtime; network/memory/cpus — только у Docker."""
    docker = {"kind": "docker", "network": "isolated", "memory": "4g", "cpus": 2}
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    root = make_plugin(tmp_path / "ok", permissions=["files:mount"], runtime=docker)
    assert dev.check(root)[1] == []
    problems = dev.check(make_plugin(tmp_path / "bad", permissions=["files:mount"], runtime={
        "kind": "python", "entry": "demo_plugin.app:app", "network": "isolated", "memory": "4 GB"}))[1]
    assert any("memory" in p and "512m" in p for p in problems) and any("kind = docker" in p for p in problems)
    assert any("files:mount" in p for p in dev.check(make_plugin(tmp_path / "none", permissions=["files:mount"],
                                                                     runtime=None))[1])


def test_fs_path(tmp_path, monkeypatch):
    """Файл владельца по пути «Файлов» или по абсолютному пути агента; за пределы дерева — нельзя."""
    from pulse_plugin import CommandError

    monkeypatch.delenv("PULSE_PLUGIN_FS_DIR", raising=False)
    with pytest.raises(CommandError, match="files:mount"):
        Plugin().fs_path("a.csv")
    monkeypatch.setenv("PULSE_PLUGIN_FS_DIR", str(tmp_path / "fs"))
    monkeypatch.setenv("PULSE_PLUGIN_FS_HOST_DIR", "/root/pulse_data/fs")
    plugin = Plugin()
    assert plugin.fs_dir == tmp_path / "fs"
    assert plugin.fs_path("Документы/a.csv") == tmp_path / "fs" / "Документы" / "a.csv"
    assert plugin.fs_path("/root/pulse_data/fs/Документы/a.csv") == tmp_path / "fs" / "Документы" / "a.csv"
    assert plugin.fs_path("/Документы/./a.csv") == tmp_path / "fs" / "Документы" / "a.csv"
    with pytest.raises(CommandError):
        plugin.fs_path("../etc/passwd")


def test_fake_api(monkeypatch):
    plugin = Plugin()
    api = fake_api(plugin, monkeypatch)
    api.on("GET", "/api/cards", lambda params, body: {"total": 1, "type": params.get("type")})
    assert plugin.api.get("/api/cards", type="task") == {"total": 1, "type": "task"}
    plugin.signal("Сигнал", priority=90)
    assert api.signals[0]["priority"] == 90
    with pytest.raises(PulseApiError):
        plugin.api.post("/api/notify", {"text": "x"})
