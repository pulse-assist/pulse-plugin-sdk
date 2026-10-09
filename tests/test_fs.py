"""pulse_plugin.fs: хранилище плагина по протоколу /fs/… — как его вызывает ядро."""

from fastapi.testclient import TestClient

from pulse_plugin import Plugin, dev
from pulse_plugin.fs import Exists, FileProvider, NotFound, entry


class Box(FileProvider):
    def __init__(self):
        self.files = {"a.txt": b"0123456789"}

    def list(self, path):
        if path:
            raise NotFound(path)
        return [entry(name, "file", len(data)) for name, data in self.files.items()]

    def read(self, path, offset=0, length=None):
        if path not in self.files:
            raise NotFound(path)
        data = self.files[path][offset:]
        return data if length is None else iter([data[:length]])

    def write(self, path, stream, overwrite=True):
        if path in self.files and not overwrite:
            raise Exists(path)
        self.files[path] = stream.read()
        return entry(path, "file", len(self.files[path]))


def test_provider_protocol():
    plugin = Plugin()
    box = Box()
    plugin.provide_files(box)
    http = TestClient(plugin.app)
    assert http.get("/fs/list").json() == {"items": [entry("a.txt", "file", 10)], "readonly": False}
    assert http.get("/fs/stat", params={"path": "a.txt"}).json()["size"] == 10
    assert http.get("/fs/stat", params={"path": "нет"}).status_code == 404
    assert http.get("/fs/read", params={"path": "a.txt"}).content == b"0123456789"
    part = http.get("/fs/read", params={"path": "a.txt"}, headers={"Range": "bytes=2-4"})
    assert part.status_code == 206 and part.content == b"234" and part.headers["content-range"] == "bytes 2-4/10"
    tail = http.get("/fs/read", params={"path": "a.txt"}, headers={"Range": "bytes=-3"})
    assert tail.content == b"789"
    assert http.put("/fs/write", params={"path": "b.bin"}, content=b"xyz").json()["size"] == 3
    assert http.put("/fs/write", params={"path": "b.bin", "overwrite": "false"}, content=b"x").status_code == 409
    assert http.post("/fs/mkdir", json={"path": "d"}).status_code == 403          # не умеет — «только чтение»
    assert http.get("/fs/list", params={"path": "../etc"}).status_code == 400


def test_check_requires_provide_permission(tmp_path):
    import json

    (tmp_path / "backend").mkdir()
    (tmp_path / "INSTALL.md").write_text("#", encoding="utf-8")
    manifest = {"id": "box", "name": "Box", "version": "0.1.0", "permissions": [],
                "runtime": {"kind": "python", "entry": "x:app", "path": "backend"},
                "files": {"provider": {"title": "Box"}}}
    (tmp_path / "pulse-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, problems = dev.check(tmp_path)
    assert any("files:provide" in p for p in problems)
