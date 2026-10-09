"""pulse-plugin — утилита разработчика плагина «Пульса» (без доступа к ядру).

    pulse-plugin new weather --name "Погода"     # новый плагин из шаблона pulse-assist/pulse-plugin-template
    pulse-plugin check                           # манифест, пути, команды задач, страницы меню
    pulse-plugin pack [--url-base URL]           # dist/<id>-<версия>.tar.gz + запись версии для каталога
    pulse-plugin dev --pulse URL --token T       # собрать и поставить на свой Пульс (поверх), --watch — при правке
    pulse-plugin publish ../pulse-plugins        # добавить версию в plugins/<id>.json каталога (дальше — MR)

Справочник контракта — CONTRACT.md в pulse-assist/pulse-plugin-sdk.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import date
from pathlib import Path

import httpx

TEMPLATE = "https://github.com/pulse-assist/pulse-plugin-template/archive/refs/heads/main.tar.gz"
ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
VERSION = re.compile(r"^\d+(\.\d+){0,2}([-+][0-9A-Za-z.-]+)?$")
SPEC = re.compile(r"^(>=|<=|==|>|<)?\s*(\d+(?:\.\d+)*)$")
PERMISSIONS = {
    "cards:read", "cards:write", "signals:create", "files", "files:open", "files:provide", "embed", "services",
    "jobs", "chat", "notify", "network",
}
EXCLUDE_DIRS = {".git", ".github", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist",
                ".plugin-data", ".idea", ".vscode", ".pulse_plugin_sdk"}
EXCLUDE_FILES = {".DS_Store"}


class PluginError(Exception):
    pass


# --- проверка -------------------------------------------------------------------------------------------------

def load_manifest(root: Path) -> dict:
    path = root / "pulse-plugin.json"
    if not path.is_file():
        raise PluginError(f"Нет {path} — это не папка плагина")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PluginError(f"pulse-plugin.json — не JSON: {exc}") from None


def _inside(root: Path, value: str, what: str, problems: list[str], must_exist: bool = True, is_dir: bool = False) -> None:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or value.startswith(("/", "\\")):
        problems.append(f"{what}: путь «{value}» — внутри плагина, без «..» и абсолютных путей")
        return
    target = root / rel
    if must_exist and not (target.is_dir() if is_dir else target.exists()):
        problems.append(f"{what}: нет {'папки' if is_dir else 'файла'} {value}")


def check(root: Path) -> tuple[dict, list[str]]:
    """Те же правила, что у Пульса при установке (CONTRACT.md); список проблем пуст — всё в порядке."""
    m = load_manifest(root)
    problems: list[str] = []
    if not ID.match(str(m.get("id", ""))):
        problems.append("id: латиница в нижнем регистре, цифры и «-», 2–40 символов")
    if not str(m.get("name", "")).strip():
        problems.append("name: нужно название")
    if not VERSION.match(str(m.get("version", ""))):
        problems.append("version: semver, например 1.2.0")
    for part in str(m.get("pulse", ">=1.0")).split(","):
        if part.strip() and not SPEC.match(part.strip()):
            problems.append(f"pulse: непонятное условие «{part.strip()}»")
    unknown = set(m.get("permissions") or []) - PERMISSIONS
    if unknown:
        problems.append(f"permissions: неизвестные права {', '.join(sorted(unknown))} (есть: {', '.join(sorted(PERMISSIONS))})")
    runtime = m.get("runtime")
    if runtime:
        kind = runtime.get("kind", "python")
        if kind == "python":
            if not runtime.get("entry"):
                problems.append("runtime.entry: ASGI-приложение, например my_plugin.app:app")
            _inside(root, runtime.get("path", "backend"), "runtime.path", problems, is_dir=True)
            if runtime.get("requirements"):
                _inside(root, runtime["requirements"], "runtime.requirements", problems)
        elif kind == "docker":
            _inside(root, runtime.get("dockerfile", "Dockerfile"), "runtime.dockerfile", problems)
        else:
            problems.append("runtime.kind: python или docker")
    ui = m.get("ui")
    if ui:
        built = (root / ui.get("dist", "ui")).is_dir()
        _inside(root, ui.get("dist", "ui"), "ui.dist", problems, must_exist=not ui.get("build"), is_dir=True)
        for item in m.get("menu") or []:
            page = str(item.get("page", "index.html")).split("#", 1)[0].split("?", 1)[0]
            if built and page and not (root / ui.get("dist", "ui") / page).is_file():
                problems.append(f"menu «{item.get('id')}»: нет страницы {ui.get('dist', 'ui')}/{page}")
    elif m.get("menu"):
        problems.append("menu без ui: страницам нужен блок ui")
    for item in m.get("menu") or []:
        if not ID.match(str(item.get("id", ""))) and not re.match(r"^[a-z][a-z0-9_-]*$", str(item.get("id", ""))):
            problems.append(f"menu: id пункта «{item.get('id')}» — латиница, цифры, «-», «_»")
    for key in ("skills", "agents"):
        for path in m.get(key) or []:
            _inside(root, path, key, problems)
    commands: set[str] = set()
    if m.get("commands"):
        _inside(root, m["commands"], "commands", problems)
        cpath = root / m["commands"]
        if cpath.is_file():
            try:
                commands = {c["name"] for c in json.loads(cpath.read_text(encoding="utf-8"))}
            except (ValueError, KeyError, TypeError):
                problems.append(f"{m['commands']}: список объектов с полем name")
    for job in m.get("jobs") or []:
        if job.get("command") not in commands:
            problems.append(f"jobs «{job.get('name')}»: команды {job.get('command')} нет в {m.get('commands') or 'commands.json'}")
    # services require digest + services permission + docker runtime
    for svc in m.get("services") or []:
        image = str(svc.get("image") or "")
        if "@sha256:" not in image:
            problems.append(f"services «{svc.get('id')}»: образ должен содержать @sha256:…")
    if m.get("services"):
        if "services" not in (m.get("permissions") or []):
            problems.append("services: нужно право services")
        if (m.get("runtime") or {}).get("kind") != "docker":
            problems.append("services: нужен runtime.kind = docker")
    files = m.get("files") or {}
    if files.get("handlers") or files.get("templates"):
        perms = set(m.get("permissions") or [])
        if "files:open" not in perms:
            problems.append("files: нужно право files:open")
        if files.get("handlers") and "embed" not in perms:
            problems.append("files.handlers: нужно право embed")
        for tmpl in files.get("templates") or []:
            if tmpl.get("file"):
                _inside(root, tmpl["file"], "files.templates", problems)
    if files.get("provider"):
        if "files:provide" not in set(m.get("permissions") or []):
            problems.append("files.provider: нужно право files:provide")
        if not m.get("runtime"):
            problems.append("files.provider: хранилище отдаёт процесс плагина — нужен runtime")
        if not str((files.get("provider") or {}).get("title", "")).strip():
            problems.append("files.provider.title: имя папки в «Файлах»")
    _inside(root, m.get("install", "INSTALL.md"), "install", problems)
    return m, problems


# --- сборка ---------------------------------------------------------------------------------------------------

def _files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name not in EXCLUDE_FILES and not name.endswith((".pyc", ".pyo")):
                out.append(Path(dirpath) / name)
    return out


def pack(root: Path, out: Path, url_base: str | None = None, build_ui: bool = True) -> dict:
    """Сборка tar.gz (папка <id>-<версия>/ внутри) и запись версии для каталога. Сборка воспроизводима:
    порядок файлов и время фиксированы — тот же исходник даёт тот же sha256."""
    manifest, problems = check(root)
    ui = manifest.get("ui") or {}
    if build_ui and ui.get("build") and not (root / ui.get("dist", "ui")).is_dir():
        print(f"Собираю интерфейс: {ui['build']}", file=sys.stderr)
        subprocess.run(ui["build"], shell=True, cwd=root, check=True)  # noqa: S602 — команда из своего манифеста
        manifest, problems = check(root)
    if problems:
        raise PluginError("Плагин не прошёл проверку:\n  " + "\n  ".join(problems))
    pid, version = manifest["id"], manifest["version"]
    out.mkdir(parents=True, exist_ok=True)
    name = f"{pid}-{version}.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in _files(root):
            rel = path.relative_to(root).as_posix()
            info = tarfile.TarInfo(f"{pid}-{version}/{rel}")
            data = path.read_bytes()
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    archive = out / name
    with archive.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    data = archive.read_bytes()
    repo = os.environ.get("GITHUB_REPOSITORY")
    base = url_base or (f"https://github.com/{repo}/releases/download/v{version}" if repo else None)
    entry = {"version": version, "channel": "beta" if re.search(r"[-]", version) else "stable",
             "pulse": manifest.get("pulse", ">=1.0"), "url": f"{base.rstrip('/')}/{name}" if base else name,
             "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
             "permissions": manifest.get("permissions") or [], "released_at": date.today().isoformat(),
             "changes": ""}
    (out / f"{pid}-{version}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"archive": str(archive), "entry": entry, "manifest": manifest}


# --- режим разработки -------------------------------------------------------------------------------------------

def _wait(http: httpx.Client, op: dict) -> dict:
    shown = 0
    while True:
        for line in op["log"][shown:]:
            print(line, file=sys.stderr)
        shown = len(op["log"])
        if op["status"] != "running":
            return op
        time.sleep(1)
        op = http.get(f"/api/plugin-operations/{op['id']}").raise_for_status().json()


def dev_install(root: Path, url: str, token: str, grant: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        built = pack(root, Path(tmp))
        with httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=120) as http:
            archive = Path(built["archive"])
            response = http.post("/api/plugins/upload", files={"file": (archive.name, archive.read_bytes())},
                                 data={"grant": ",".join(grant)})
            if response.status_code >= 400:
                raise PluginError(f"Пульс ответил {response.status_code}: {response.text[:500]}")
            op = _wait(http, response.json())
    if op["status"] != "success":
        raise PluginError(op.get("error") or "установка не удалась")
    return op.get("result") or {}


def _snapshot(root: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in _files(root)}


# --- каталог ----------------------------------------------------------------------------------------------------

def publish(root: Path, catalog: Path, dist: Path, changes: str, author: str | None, license_: str | None,
            homepage: str | None) -> Path:
    """Запись версии в plugins/<id>.json каталога: новая версия — первой; та же версия заменяется."""
    manifest = load_manifest(root)
    pid, version = manifest["id"], manifest["version"]
    entry_path = dist / f"{pid}-{version}.json"
    if not entry_path.is_file():
        raise PluginError(f"Нет {entry_path} — сначала pulse-plugin pack (или скачайте его из релиза)")
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    if changes:
        entry["changes"] = changes
    folder = catalog / "plugins" if (catalog / "plugins").is_dir() else catalog
    target = folder / f"{pid}.json"
    record = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {"id": pid, "versions": []}
    record.update({"name": manifest["name"], "description": manifest.get("description", "")})
    for key, value in (("author", author), ("license", license_), ("homepage", homepage)):
        if value:
            record[key] = value
        record.setdefault(key, "")
    record["versions"] = [entry] + [v for v in record.get("versions", []) if v.get("version") != version]
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


# --- новый плагин из шаблона ------------------------------------------------------------------------------------

def new(pid: str, name: str, target: Path, template_url: str = TEMPLATE) -> Path:
    if not ID.match(pid):
        raise PluginError("id: латиница в нижнем регистре, цифры и «-», 2–40 символов")
    if target.exists() and any(target.iterdir()):
        raise PluginError(f"Папка {target} не пуста")
    package = f"{pid.replace('-', '_')}_plugin"
    response = httpx.get(template_url, follow_redirects=True, timeout=120)
    response.raise_for_status()
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk() or ".." in Path(member.name).parts:
                    raise PluginError(f"Шаблон с недопустимым путём: {member.name}")
            tar.extractall(tmp)  # noqa: S202 — пути проверены выше
        source = next(Path(tmp).iterdir())
        shutil.copytree(source, target, dirs_exist_ok=True)
    old = target / "backend" / "example_plugin"
    if old.is_dir():
        old.rename(target / "backend" / package)
    for path in _files(target):
        if path.suffix in {".py", ".json", ".md", ".txt", ".toml", ".js", ".html", ".yml", ".yaml"} or path.name == "Dockerfile":
            text = path.read_text(encoding="utf-8")
            updated = text.replace("example_plugin", package)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
    manifest_path = target / "pulse-plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(id=pid, name=name, version="0.1.0", description="")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


# --- CLI ----------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulse-plugin", description="Утилита разработчика плагина «Пульса»")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="новый плагин из шаблона")
    p.add_argument("id")
    p.add_argument("--name", help="название для владельца (по умолчанию — id)")
    p.add_argument("--dir", help="папка (по умолчанию pulse-<id>)")
    p = sub.add_parser("check", help="проверить манифест и пути")
    p.add_argument("path", nargs="?", default=".")
    p = sub.add_parser("pack", help="собрать dist/<id>-<версия>.tar.gz и запись версии для каталога")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--out", default="dist")
    p.add_argument("--url-base", help="где будет лежать сборка (по умолчанию — релиз GitHub по $GITHUB_REPOSITORY)")
    p = sub.add_parser("dev", help="собрать и поставить на свой Пульс поверх установленной версии")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--pulse", default=os.environ.get("PULSE_API_URL"), help="адрес Пульса (или $PULSE_API_URL)")
    p.add_argument("--token", default=os.environ.get("PULSE_TOKEN"), help="токен (или $PULSE_TOKEN)")
    p.add_argument("--grant", default="", help="новые права через запятую — вы владелец этого Пульса")
    p.add_argument("--watch", action="store_true", help="переставлять при каждой правке файлов")
    p = sub.add_parser("publish", help="добавить версию в каталог: plugins/<id>.json (дальше — коммит и MR)")
    p.add_argument("catalog", help="папка каталога (клон pulse-assist/pulse-plugins или свой)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--dist", default="dist")
    p.add_argument("--changes", default="", help="что нового — для владельца, по-русски")
    p.add_argument("--author")
    p.add_argument("--license")
    p.add_argument("--homepage")
    a = parser.parse_args(argv)
    try:
        if a.cmd == "new":
            target = new(a.id, a.name or a.id, Path(a.dir or f"pulse-{a.id}"))
            print(f"Готово: {target}. Дальше — AGENTS.md в папке плагина и pulse-plugin check")
        elif a.cmd == "check":
            manifest, problems = check(Path(a.path))
            if problems:
                print("Проблемы:\n  " + "\n  ".join(problems), file=sys.stderr)
                return 1
            print(f"{manifest['id']} {manifest['version']}: проверка пройдена")
        elif a.cmd == "pack":
            built = pack(Path(a.path), Path(a.out), a.url_base)
            print(json.dumps({"archive": built["archive"], **built["entry"]}, ensure_ascii=False, indent=1))
        elif a.cmd == "dev":
            if not a.pulse or not a.token:
                raise PluginError("Нужны --pulse и --token (или PULSE_API_URL и PULSE_TOKEN)")
            root, grant = Path(a.path), [g.strip() for g in a.grant.split(",") if g.strip()]
            result = dev_install(root, a.pulse, a.token, grant)
            print(f"Установлено: {result.get('id')} {result.get('version')}")
            while a.watch:
                before = _snapshot(root)
                while _snapshot(root) == before:
                    time.sleep(1)
                print("Изменения — переставляю…", file=sys.stderr)
                try:
                    result = dev_install(root, a.pulse, a.token, grant)
                    print(f"Установлено: {result.get('id')} {result.get('version')}")
                except PluginError as exc:
                    print(f"Ошибка: {exc}", file=sys.stderr)
        elif a.cmd == "publish":
            target = publish(Path(a.path), Path(a.catalog), Path(a.path) / a.dist, a.changes, a.author, a.license,
                             a.homepage)
            print(f"Записано: {target}. Дальше — коммит и MR в репозиторий каталога")
    except PluginError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
