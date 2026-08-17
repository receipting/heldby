"""Catalogue loading, validation, and the grow-only gate.

Where a test records a real miss, the comment names it. A regression test whose
reason has been forgotten gets deleted by the next person to find it awkward.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from heldby import catalog as catalog_mod  # noqa: E402
from heldby.catalog import CatalogError  # noqa: E402
from heldby.schema import ACTIONS, Match, Rule, normalise_ident  # noqa: E402


@pytest.fixture(scope="module")
def cat():
    return catalog_mod.load()


def write_catalog(tmp_path: Path, body: str, name: str = "t.yml") -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


MINIMAL = """
id: t
version: t.v1
rules:
  - id: a.one
    kind: model
    match: { symbol: ["go"] }
"""


# --- the shipped catalogue --------------------------------------------------


def test_shipped_catalogue_loads(cat):
    assert len(cat.rules) > 100
    assert all(f.digest for f in cat.files)


def test_every_model_rule_can_actually_fire(cat):
    for rule in cat.model_rules():
        assert not rule.match.is_empty(), rule.id


def test_sink_rules_carry_a_known_action_and_severity(cat):
    for rule in cat.sink_rules():
        assert rule.action in ACTIONS, rule.id
        assert rule.severity in {"critical", "high"}, rule.id


def test_python_langchain_module_names_are_catalogued(cat):
    """Regression: TauricResearch/TradingAgents, 95k stars, reported no AI.

    Its provider imports are `langchain_openai`, `langchain_anthropic`,
    `langchain_google_genai`, `langchain_aws` — Python module names. A catalogue
    carrying only the npm names (`@langchain/openai`) matches none of them, so the
    tool would have reported "no AI detected" over an entire multi-agent trading
    platform. This is why imports are keyed by ecosystem rather than being one
    `package` field.
    """
    py_imports = {name for r in cat.rules for name in r.imports.get("py", ())}
    for module in (
        "langchain_openai",
        "langchain_anthropic",
        "langchain_google_genai",
        "langchain_aws",
        "langchain_core",
    ):
        assert module in py_imports, f"{module} is not catalogued"


def test_npm_and_pypi_names_are_not_assumed_equal(cat):
    """The Google SDK is @google/genai on npm and google-genai on PyPI.

    If some later refactor derives one from the other, this fails.
    """
    rule = cat.by_id["google.genai"]
    assert rule.registry["npm"] == ("@google/genai",)
    assert rule.registry["pypi"] == ("google-genai",)
    assert rule.imports["py"] == ("google.genai",)


def test_configured_providers_are_matched_as_bare_literals(cat):
    """Regression: fourteen vendors behind one `openai` import.

    TradingAgents selects xai / deepseek / qwen / glm / minimax / openrouter /
    kimi / groq / nvidia / ollama by setting `base_url` in a PROVIDERS dict. There
    is no fetch call, so a url_contains rule scoped to a network call never fires.
    The base URL sitting in a config table is the only tell.
    """
    literals = {lit for r in cat.rules for lit in r.match.url_literal}
    for host in ("api.deepseek.com", "api.x.ai", "api.minimax.io", "openrouter.ai/api"):
        assert any(host in lit or lit in host for lit in literals), host


def test_an_action_in_the_vocabulary_is_detectable_or_declared_undetectable(cat):
    """A declared action with no rule is a section that can never fill in.

    `alter-recipients` was in ACTIONS from the start and had no rule for months,
    so heldby could not corroborate receipting's `contact-extraction` claim; it
    stayed an assertion, which is the failure mode the tool exists to remove.

    `decide-about-person` is still bare on purpose — credit, hiring, claims and
    moderation are judgements about what a call means, not signatures — and
    sinks.yml's header says so. If it ever gains a rule, that header is wrong.
    """
    detected = {rule.action for rule in cat.sink_rules()}
    assert "alter-recipients" in detected
    assert ACTIONS - detected == {"decide-about-person"}


def test_deprecated_rules_are_kept_not_deleted(cat):
    """A superseded SDK stays catalogued because repos still on it exist."""
    legacy = cat.by_id["google.generative-ai.legacy"]
    assert legacy.deprecated is True
    assert legacy.match.member  # still matches


# --- validation refuses rather than degrades -------------------------------


def test_unknown_action_is_refused(tmp_path):
    body = MINIMAL.replace(
        'match: { symbol: ["go"] }',
        'match: { symbol: ["go"] }\n    action: exfiltrate\n    severity: high',
    )
    with pytest.raises(CatalogError, match="closed vocabulary"):
        catalog_mod.load(write_catalog(tmp_path, body))


def test_a_match_that_sets_nothing_is_refused(tmp_path):
    body = MINIMAL.replace('match: { symbol: ["go"] }', "match: {}")
    with pytest.raises(CatalogError, match="never fire"):
        catalog_mod.load(write_catalog(tmp_path, body))


def test_a_remedy_that_does_not_fit_the_action_is_refused(tmp_path):
    """`sandbox` is meaningless for anything but code execution.

    Without this check a catalogue entry becomes a place to write reassurance.
    """
    body = MINIMAL.replace(
        'match: { symbol: ["go"] }',
        'match: { symbol: ["go"] }\n    action: execute-code\n    severity: high'
        '\n    remedies: ["human-release"]',
    )
    with pytest.raises(CatalogError, match="not meaningful"):
        catalog_mod.load(write_catalog(tmp_path, body))


def test_severity_without_an_action_is_refused(tmp_path):
    body = MINIMAL.replace(
        'match: { symbol: ["go"] }', 'match: { symbol: ["go"] }\n    severity: high'
    )
    with pytest.raises(CatalogError, match="means nothing"):
        catalog_mod.load(write_catalog(tmp_path, body))


def test_a_duplicate_rule_id_across_files_is_refused(tmp_path):
    write_catalog(tmp_path, MINIMAL, "one.yml")
    write_catalog(tmp_path, MINIMAL.replace("id: t", "id: u"), "two.yml")
    with pytest.raises(CatalogError, match="declared in both"):
        catalog_mod.load(tmp_path)


def test_an_unknown_ecosystem_key_is_refused(tmp_path):
    body = MINIMAL.replace(
        'match: { symbol: ["go"] }', 'imports: { rust: ["x"] }\n    match: { symbol: ["go"] }'
    )
    with pytest.raises(CatalogError, match="unknown keys"):
        catalog_mod.load(write_catalog(tmp_path, body))


def test_an_empty_catalogue_directory_is_refused(tmp_path):
    """Refuse rather than scan with no rules and report a clean bill of health."""
    with pytest.raises(CatalogError, match="no catalogue files"):
        catalog_mod.load(tmp_path)


# --- identifier folding ----------------------------------------------------


def test_one_rule_spans_naming_conventions():
    """generate_content and generateContent are the same API in two languages."""
    assert normalise_ident("generate_content") == normalise_ident("generateContent")
    assert normalise_ident("run_sync") == normalise_ident("runSync")
    assert normalise_ident("base_url") == normalise_ident("baseURL")
    assert normalise_ident("create") != normalise_ident("created")


# --- the grow-only gate ----------------------------------------------------


def _cat_from(tmp_path: Path, rules_yaml: str, name: str):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "t.yml").write_text(
        f"id: t\nversion: t.v1\nrules:\n{rules_yaml}", encoding="utf-8"
    )
    return catalog_mod.load(directory)


TWO_RULES = """  - id: a.one
    kind: model
    match: { symbol: ["go"] }
  - id: a.two
    kind: model
    match: { symbol: ["stop"] }
"""


def test_adding_a_rule_is_additive(tmp_path):
    base = _cat_from(tmp_path, TWO_RULES, "base")
    head = _cat_from(
        tmp_path, TWO_RULES + '  - id: a.three\n    kind: model\n    match: { symbol: ["new"] }\n', "head"
    )
    added, removed, altered = catalog_mod.diff_additive(base, head)
    assert added == ["a.three"]
    assert not removed and not altered


def test_deleting_a_rule_is_caught(tmp_path):
    """A deleted rule is a silent blind spot — the report still says it scanned everything."""
    base = _cat_from(tmp_path, TWO_RULES, "base")
    head = _cat_from(tmp_path, TWO_RULES.split("  - id: a.two")[0], "head")
    _, removed, _ = catalog_mod.diff_additive(base, head)
    assert removed == ["a.two"]


def test_narrowing_a_match_is_caught(tmp_path):
    base = _cat_from(tmp_path, TWO_RULES, "base")
    head = _cat_from(tmp_path, TWO_RULES.replace('["stop"]', '["stopped"]'), "head")
    _, _, altered = catalog_mod.diff_additive(base, head)
    assert altered == ["a.two"]


def test_annotating_a_rule_is_allowed(tmp_path):
    """The unattended job may add a note or mark a package superseded.

    Neither changes what is detected, so neither needs a person.
    """
    base = _cat_from(tmp_path, TWO_RULES, "base")
    head = _cat_from(
        tmp_path, TWO_RULES.replace('    match: { symbol: ["stop"] }\n',
                                    '    match: { symbol: ["stop"] }\n    deprecated: true\n    note: superseded\n'),
        "head",
    )
    added, removed, altered = catalog_mod.diff_additive(base, head)
    assert not added and not removed and not altered


# --- the proposal applier --------------------------------------------------


def run_applier(tmp_path: Path, proposals: dict, catalog_dir: Path) -> subprocess.CompletedProcess:
    path = tmp_path / "proposals.json"
    path.write_text(json.dumps(proposals), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply_proposals.py"), str(path),
         "--catalog-dir", str(catalog_dir), "--skip-registry-check"],
        capture_output=True, text=True,
    )


@pytest.fixture
def target(tmp_path):
    directory = tmp_path / "cat"
    directory.mkdir()
    (directory / "t.yml").write_text(f"id: t\nversion: t.v1\nrules:\n{TWO_RULES}", encoding="utf-8")
    return directory


def test_applier_adds_a_valid_rule(tmp_path, target):
    result = run_applier(tmp_path, {"proposals": [{
        "file": "t.yml",
        "rule": {"id": "b.new", "kind": "model", "imports": {"py": ["thing"]},
                 "match": {"symbol": ["call"]}, "note": "a note"},
    }]}, target)
    assert result.returncode == 0, result.stderr
    assert "added    b.new" in result.stdout
    assert "b.new" in catalog_mod.load(target).by_id


def test_applier_refuses_to_touch_an_existing_rule(tmp_path, target):
    """The model cannot alter or delete. There is no code path that does."""
    result = run_applier(tmp_path, {"proposals": [{
        "file": "t.yml",
        "rule": {"id": "a.two", "kind": "model", "match": {"symbol": ["something-else"]}},
    }]}, target)
    assert result.returncode == 0
    assert "already exists" in result.stdout
    assert catalog_mod.load(target).by_id["a.two"].match.symbol == ("stop",)


def test_applier_drops_an_invalid_rule_and_says_why(tmp_path, target):
    result = run_applier(tmp_path, {"proposals": [{
        "file": "t.yml",
        "rule": {"id": "b.bad", "kind": "wishful", "match": {"symbol": ["x"]}},
    }]}, target)
    assert result.returncode == 0
    assert "dropped  b.bad" in result.stdout
    assert "b.bad" not in catalog_mod.load(target).by_id


def test_applier_drops_a_rule_aimed_at_an_unknown_file(tmp_path, target):
    result = run_applier(tmp_path, {"proposals": [{
        "file": "../../../etc/passwd",
        "rule": {"id": "b.escape", "kind": "model", "match": {"symbol": ["x"]}},
    }]}, target)
    assert result.returncode == 0
    assert "is not one of" in result.stdout


def test_applier_leaves_the_catalogue_loadable(tmp_path, target):
    run_applier(tmp_path, {"proposals": [
        {"file": "t.yml", "rule": {"id": f"b.{i}", "kind": "model",
                                   "match": {"symbol": [f"s{i}"]}}} for i in range(5)
    ]}, target)
    assert len(catalog_mod.load(target).rules) == 7
