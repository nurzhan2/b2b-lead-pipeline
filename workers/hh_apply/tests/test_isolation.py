"""hh_apply и лидген не знают друг о друге — проверяется разбором, а не обещанием.

По образцу WWAO ``agent/tests/test_isolation.py``. Этот воркер действует под
личным аккаунтом человека; ядро лидгена и остальные воркеры — анонимные
сборщики. Смешать их — значит однажды отправить отклик из планировщика или
слить личные данные в лидген. Поэтому:

* hh_apply не импортирует core_client, другие воркеры и вообще сетевые
  клиенты: наружу он ходит только браузером на hh;
* в его коде (не в документации) нет адресов ядра;
* никто в лидгене не импортирует hh_apply, и playwright есть только в его
  requirements.txt;
* всё, что он пишет о сессии владельца, в .gitignore и не отслеживается git.

Импорты проверяются разбором AST, а не поиском по тексту: модули пакета
обсуждают эти границы в docstring'ах, и поиск по тексту принял бы объяснение
за нарушение.
"""

import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKERS = REPO / "workers"
PACKAGE = WORKERS / "hh_apply"
PRODUCTION = [p for p in PACKAGE.rglob("*.py") if "tests" not in p.relative_to(PACKAGE).parts]

NETWORK_CLIENTS = {"httpx", "requests", "aiohttp", "urllib.request", "http.client", "socket",
                   "websockets", "grpc", "urllib3"}
DYNAMIC_IMPORTERS = {"import_module", "__import__", "load_module"}


def _absolute(path: Path, level: int, module: str | None) -> str:
    """Относительный импорт -> полное имя, относительно пакета, где лежит САМ файл."""
    try:
        package = list(path.parent.relative_to(REPO).parts)
    except ValueError:  # файл вне репозитория (тесты самого сканера)
        return "." * level + (module or "")
    base = package[: len(package) - (level - 1)] if level > 1 else package
    return ".".join([*base, *([module] if module else [])])


def imported_modules(path: Path) -> set[str]:
    """Все модули, которые файл импортирует: оператором или по имени-строке."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module if node.level == 0 and node.module else _absolute(path, node.level, node.module))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in DYNAMIC_IMPORTERS:
                names.update(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return names


def code_strings(path: Path) -> list[str]:
    """Строковые константы кода — без docstring'ов, где границы описаны словами."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


# --- hh_apply -> лидген ------------------------------------------------------------


def test_hh_apply_never_imports_core_client():
    offenders = {p.name: sorted(m for m in imported_modules(p) if "core_client" in m) for p in PACKAGE.rglob("*.py")}
    assert not {k: v for k, v in offenders.items() if v}


def test_hh_apply_has_no_core_client_of_its_own():
    assert not (PACKAGE / "core_client.py").exists()


def test_hh_apply_never_imports_another_worker():
    for path in PACKAGE.rglob("*.py"):
        crossing = [m for m in imported_modules(path)
                    if m.startswith("workers") and not m.startswith("workers.hh_apply")]
        assert not crossing, f"{path.name} импортирует чужое: {crossing}"


def test_relative_imports_are_resolved_against_their_own_package():
    enricher = WORKERS / "enrich" / "enricher.py"
    assert "workers.enrich.core_client" in imported_modules(enricher)
    assert not any(m.startswith("workers.hh_apply") for m in imported_modules(enricher))


def test_hh_apply_has_no_network_client_besides_the_browser():
    """Без HTTP-клиента в ядро нельзя отправить ничего — даже случайно."""
    for path in PRODUCTION:
        found = [m for m in imported_modules(path) if m in NETWORK_CLIENTS or m.split(".")[0] in {"httpx", "requests", "aiohttp"}]
        assert not found, f"{path.name} тянет сетевой клиент: {found}"


def test_no_address_of_the_core_in_hh_apply_code():
    for path in PRODUCTION:
        suspicious = [s for s in code_strings(path)
                      if any(marker in s.lower() for marker in ("/api/v1/", "ingest", ":8081", "core_url"))]
        assert not suspicious, f"{path.name}: {suspicious}"


def test_settings_and_env_example_have_no_core_url():
    from workers.hh_apply.config import Settings

    assert "core_url" not in Settings.model_fields
    assert "CORE_URL" not in (PACKAGE / ".env.example").read_text(encoding="utf-8")


# --- лидген -> hh_apply ----------------------------------------------------------------


def test_no_other_worker_imports_hh_apply_or_playwright():
    for path in WORKERS.rglob("*.py"):
        if PACKAGE in path.parents:
            continue
        found = [m for m in imported_modules(path) if m.startswith("workers.hh_apply") or m.split(".")[0] == "playwright"]
        assert not found, f"{path.relative_to(REPO)}: {found}"


def test_playwright_is_only_in_hh_apply_requirements():
    assert "playwright" in (PACKAGE / "requirements.txt").read_text(encoding="utf-8")
    for requirements in WORKERS.glob("*/requirements.txt"):
        if requirements.parent == PACKAGE:
            continue
        assert "playwright" not in requirements.read_text(encoding="utf-8").lower(), requirements


def test_the_java_core_knows_nothing_about_hh_apply_or_playwright():
    pom = (REPO / "pom.xml").read_text(encoding="utf-8").lower()
    assert "playwright" not in pom
    for path in (REPO / "src").rglob("*.java"):
        text = path.read_text(encoding="utf-8")
        assert "hh_apply" not in text and "playwright" not in text.lower(), path


# --- сессия владельца не утекает в git ---------------------------------------------------


def test_everything_the_agent_writes_is_ignored_by_git():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("workers/hh_apply/session/", "workers/hh_apply/evidence/", "workers/hh_apply/screenshots/",
                    "workers/hh_apply/*.sqlite3", "workers/hh_apply/cover_letter.md"):
        assert pattern in ignored, f"{pattern} должно быть в .gitignore"


def test_nothing_the_agent_writes_is_tracked():
    try:
        tracked = subprocess.run(["git", "ls-files", "workers/hh_apply"], cwd=REPO, capture_output=True,
                                 text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git недоступен")
    leaked = [name for name in tracked
              if any(part in name for part in ("/session/", "/evidence/", "/screenshots/", ".sqlite3"))
              or name.endswith("/cover_letter.md")]
    assert not leaked


# --- сам сканер ---------------------------------------------------------------------


def test_the_scan_sees_a_module_fetched_by_name(tmp_path):
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text('import importlib\nm = importlib.import_module("workers.hh.core_client")\n', encoding="utf-8")
    assert "workers.hh.core_client" in imported_modules(sneaky)


def test_the_scan_ignores_docstrings_but_not_code(tmp_path):
    module = tmp_path / "m.py"
    module.write_text('"""Никогда не POST /api/v1/companies/ingest."""\nURL = "http://localhost:8081/api/v1/x"\n',
                      encoding="utf-8")
    strings = code_strings(module)
    assert strings == ["http://localhost:8081/api/v1/x"]
