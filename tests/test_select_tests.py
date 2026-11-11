"""scripts/select_tests.py: which tests a change selects. Every case builds a tiny fake repository in tmp_path, so no real file matters."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("select_tests", ROOT / "scripts" / "select_tests.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["select_tests"] = S
_spec.loader.exec_module(S)


def make(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return S.Repo(tmp_path)


def pick(repo, *changed):
    mode, tests, nodes, why = S.select(list(changed), repo)
    return mode, [t for t in tests if t not in S.SMOKE_FILES], why


BASE = {
    "app/__init__.py": "from . import api\n",
    "app/api.py": "from . import queries, trends\n",
    "app/queries.py": "X = 1\n",
    "app/trends.py": "from flask import Blueprint\nbp = Blueprint('t', __name__)\n@bp.get('/api/trends')\ndef t():\n    return 1\n",
    "app/lookup.py": "def f():\n    return 1\n",
    "app/orphan.py": "def g():\n    return 1\n",
    "etl/__init__.py": "",
    "etl/db.py": "",
    "etl/build.py": "from . import db\n",
    "tests/api/conftest.py": "from app import create_app\n",
    "tests/api/test_trends.py": "def test_a(client):\n    client.get('/api/trends?x=1')\n",
    "tests/api/test_lookup.py": "from app import lookup\n",
    "tests/etl/test_build.py": "from etl import build\n",
    "tests/etl/test_other.py": "def test_o():\n    pass\n",
    **{f"tests/etl/test_filler{i}.py": "def test_f():\n    pass\n" for i in range(12)},
    "docs/GUIDE.md": "text\n",
    "migrations/001.sql": "select 1;\n",
}


def test_changed_python_module_selects_tests_that_import_it(tmp_path):
    r = make(tmp_path, BASE)
    mode, tests, why = pick(r, "app/lookup.py")
    assert mode == "subset" and tests == ["tests/api/test_lookup.py"]


def test_route_defined_in_a_module_selects_tests_that_request_it(tmp_path):
    r = make(tmp_path, BASE)
    mode, tests, _ = pick(r, "app/trends.py")
    assert mode == "subset" and tests == ["tests/api/test_trends.py"]


def test_etl_module_selects_its_tests_only(tmp_path):
    r = make(tmp_path, BASE)
    mode, tests, _ = pick(r, "etl/build.py")
    assert mode == "subset" and tests == ["tests/etl/test_build.py"]


@pytest.mark.parametrize("path", ["migrations/001.sql", "tests/api/conftest.py", "tests/_xdist_env.py", "requirements.txt", "Dockerfile", "scripts/deploy/run_etl.sh", "tests/fixtures/x.json"])
def test_hard_rules_select_the_full_suite(tmp_path, path):
    r = make(tmp_path, BASE)
    assert S.select([path], r)[0] == "full"


@pytest.mark.parametrize("path", ["app/__init__.py", "app/api.py", "app/queries.py", "etl/db.py"])
def test_hub_modules_select_the_full_suite(tmp_path, path):
    r = make(tmp_path, BASE)
    assert S.select([path], r)[0] == "full"


def test_a_module_most_tests_import_becomes_a_hub(tmp_path):
    files = dict(BASE)
    files["app/shared.py"] = "Y = 1\n"
    for i in range(10):
        files[f"tests/api/test_s{i}.py"] = "from app import shared\n"
    r = make(tmp_path, files)
    assert "app/shared.py" in r.hubs()
    assert S.select(["app/shared.py"], r)[0] == "full"


def test_walk_does_not_pass_through_a_hub(tmp_path):
    # trends is imported by the hub api; tests that import api must not be pulled in
    files = dict(BASE)
    files["tests/api/test_imports_api.py"] = "from app import api\n"
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "app/trends.py")
    assert "tests/api/test_imports_api.py" not in tests


def test_python_module_nothing_reaches_cannot_be_placed(tmp_path):
    r = make(tmp_path, BASE)
    assert S.select(["app/orphan.py"], r)[0] == "full"


def test_unknown_file_type_selects_full(tmp_path):
    r = make(tmp_path, BASE)
    assert S.select(["something/weird.bin"], r)[0] == "full"


def test_docs_only_selects_the_smoke_set(tmp_path):
    r = make(tmp_path, BASE)
    mode, tests, _ = pick(r, "docs/GUIDE.md")
    assert mode == "subset" and tests == []


def test_document_read_by_a_test_selects_that_test(tmp_path):
    files = dict(BASE)
    files["tests/etl/test_docs.py"] = "from pathlib import Path\nROOT = Path('.')\nTEXT = (ROOT / 'docs' / 'GUIDE.md').read_text()\n"
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "docs/GUIDE.md")
    assert tests == ["tests/etl/test_docs.py"]


def test_deleted_test_file_selects_nothing_extra(tmp_path):
    r = make(tmp_path, BASE)
    mode, tests, _ = pick(r, "tests/api/test_gone.py")
    assert mode == "subset" and tests == []


def test_changed_test_file_selects_itself(tmp_path):
    r = make(tmp_path, BASE)
    _, tests, _ = pick(r, "tests/etl/test_other.py")
    assert tests == ["tests/etl/test_other.py"]


def test_test_helper_selects_the_tests_that_import_it(tmp_path):
    files = dict(BASE)
    files["tests/etl/helper.py"] = "Z = 1\n"
    files["tests/etl/test_uses_helper.py"] = "import helper\nfrom tests.etl import helper as h\n"
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "tests/etl/helper.py")
    assert tests == ["tests/etl/test_uses_helper.py"]


def test_string_module_reference_counts_as_an_import(tmp_path):
    files = dict(BASE)
    files["tests/etl/test_cli.py"] = "import subprocess\ndef test_c():\n    subprocess.run(['python', '-m', 'etl.build'])\n"
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "etl/build.py")
    assert "tests/etl/test_cli.py" in tests


APP_JS = """import { homePage } from './js/pages/products.js';
import { trendsPage } from './js/pages/trends.js';
const routes = [
  { re: /^\\/?$/, page: homePage },
  { re: /^\\/trends\\/?$/, page: trendsPage },
];
"""
JS = {
    "app/static/index.html": '<script src="/static/app.js"></script>',
    "app/static/app.js": APP_JS,
    "app/static/app.css": "a{}",
    "app/static/js/util.js": "export const u = 1;\n",
    "app/static/js/pages/products.js": "import { u } from '../util.js';\n",
    "app/static/js/pages/trends.js": "import { u } from '../util.js';\n",
    "tests/ui/conftest.py": "import playwright\n",
    "tests/ui/test_ui.py": "def test_home(page):\n    page.goto('/')\n",
    "tests/ui/test_trends.py": "def test_t(page):\n    page.goto('/trends')\n",
    "tests/test_static_classnames.py": "def test_x():\n    pass\n",
}


def test_page_script_selects_the_ui_tests_that_open_its_route(tmp_path):
    r = make(tmp_path, {**BASE, **JS})
    _, tests, _ = pick(r, "app/static/js/pages/trends.js")
    assert "tests/ui/test_trends.py" in tests and "tests/ui/test_ui.py" not in tests


def test_shared_script_imported_by_every_page_selects_all_ui_tests(tmp_path):
    r = make(tmp_path, {**BASE, **JS})
    _, tests, _ = pick(r, "app/static/js/util.js")
    assert {"tests/ui/test_trends.py", "tests/ui/test_ui.py"} <= set(tests)


def test_stylesheet_selects_every_ui_test(tmp_path):
    r = make(tmp_path, {**BASE, **JS})
    _, tests, _ = pick(r, "app/static/app.css")
    assert {"tests/ui/test_trends.py", "tests/ui/test_ui.py"} <= set(tests)


def test_selector_change_runs_its_own_tests_only(tmp_path):
    files = {**BASE, "tests/test_select_tests.py": "def test_s():\n    pass\n"}
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "scripts/select_tests.py")
    assert tests == ["tests/test_select_tests.py"]


def test_the_real_repository_has_sane_hubs_and_every_test_is_indexed():
    r = S.Repo(ROOT)
    assert {"app/__init__.py", "app/queries.py", "etl/db.py"} <= r.hubs()
    assert len(r.tests) > 150
    assert r.edges["tests/api/test_api_trends.py"]


def test_a_module_used_by_a_hub_route_handler_is_tied_to_the_tests_that_request_that_route(tmp_path):
    files = dict(BASE)
    files["app/api.py"] = ("from flask import Blueprint\nfrom . import queries, trends, lookup as lk, helper_mod\nbp = Blueprint('a', __name__)\n"
                           "def shared():\n    return helper_mod.go()\n"
                           "@bp.get('/api/lookup')\ndef look():\n    return lk.find() + shared()\n")
    files["app/helper_mod.py"] = "def go():\n    return 1\n"
    files["tests/api/test_lookup_route.py"] = "def test_r(client):\n    client.get('/api/lookup?x=1')\n"
    files.update({f"tests/etl/test_pad{i}.py": "def test_p():\n    pass\n" for i in range(6)})
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "app/helper_mod.py")
    assert "tests/api/test_lookup_route.py" in tests                 # through the helper function of the handler


def test_a_test_named_after_a_module_depends_on_it(tmp_path):
    files = dict(BASE)
    files["app/widgets.py"] = "def w():\n    return 1\n"
    files["tests/api/test_api_widget_totals.py"] = "def test_w(client):\n    client.get('/api/zzz')\n"
    files.update({f"tests/etl/test_pad{i}.py": "def test_p():\n    pass\n" for i in range(6)})
    r = make(tmp_path, files)
    _, tests, _ = pick(r, "app/widgets.py")
    assert tests == ["tests/api/test_api_widget_totals.py"]
