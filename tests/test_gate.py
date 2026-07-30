"""Lint and adopt tests.

The failure modes under test are the ones that make a gate worthless while looking
like it works: passing over a scope it silently excluded, and going green by
renaming the problem instead of recording it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from heldby import adopt as adopt_mod
from heldby import catalog as catalog_mod
from heldby import lint as lint_mod

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def cat():
    return catalog_mod.load()


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "ts-messy"
    shutil.copytree(FIXTURES / "ts-messy", target)
    return target


# --- the gate ---------------------------------------------------------------


def test_a_bypass_outside_the_gateway_fails(repo, cat):
    result = lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts"])
    assert not result.ok
    assert all(v.file == "src/bypass.ts" for v in result.violations)


def test_the_gateway_module_is_not_a_bypass_of_itself(repo, cat):
    result = lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts"])
    assert "src/gateway.ts" not in {v.file for v in result.violations}


def test_several_gateway_modules_are_allowed(repo, cat):
    """One central module is what a single team converges on.

    An arbitrary codebase has an SDK wrapper per team, a legacy path and a test
    double, and a gate that permits exactly one file gets switched off.
    """
    result = lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts", "src/bypass.ts"])
    assert result.ok


def test_an_env_var_name_is_never_a_violation(repo, cat):
    """Failing a build because .env.example mentions a key teaches people to
    delete the gate rather than fix anything."""
    result = lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts"])
    assert all(v.why != "env-var" for v in result.violations)


def test_the_baseline_makes_the_gate_green_without_hiding_anything(repo, cat, tmp_path):
    baseline = tmp_path / "baseline.json"
    first = lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts"])
    assert first.violations

    lint_mod.write_baseline(baseline, first.violations)
    second = lint_mod.lint(
        repo, cat, gateway_modules=["src/gateway.ts"], baseline_path=baseline
    )
    assert second.ok
    assert second.accepted, "an accepted bypass must still be reported"
    assert "known debt, not compliance" in lint_mod.render_lint(second)


def test_a_new_bypass_still_fails_after_baselining(repo, cat, tmp_path):
    """The whole point of a ratchet: green today, and it can only tighten."""
    baseline = tmp_path / "baseline.json"
    lint_mod.write_baseline(
        baseline, lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts"]).violations
    )

    (repo / "src" / "fresh.ts").write_text(
        "export const go = () => fetch('https://api.anthropic.com/v1/messages')\n",
        encoding="utf-8",
    )
    result = lint_mod.lint(
        repo, cat, gateway_modules=["src/gateway.ts"], baseline_path=baseline
    )
    assert not result.ok
    assert any(v.file == "src/fresh.ts" for v in result.violations)


def test_a_stale_baseline_entry_is_reported(repo, cat, tmp_path):
    """Left alone it silently re-permits the same bypass if it comes back."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"accepted": ["src/gone.ts::provider.openai"]}), encoding="utf-8")
    result = lint_mod.lint(
        repo, cat, gateway_modules=["src/gateway.ts"], baseline_path=baseline
    )
    assert "src/gone.ts::provider.openai" in result.stale_baseline
    assert "should be deleted" in lint_mod.render_lint(result)


def test_the_baseline_key_survives_a_line_shift(repo, cat, tmp_path):
    """Keying on the line number makes every violation reappear when someone adds
    an import above it, and a baseline that churns gets regenerated blindly."""
    baseline = tmp_path / "baseline.json"
    lint_mod.write_baseline(
        baseline, lint_mod.lint(repo, cat, gateway_modules=["src/gateway.ts"]).violations
    )
    bypass = repo / "src" / "bypass.ts"
    bypass.write_text("// a new line at the top\n" + bypass.read_text(), encoding="utf-8")
    assert lint_mod.lint(
        repo, cat, gateway_modules=["src/gateway.ts"], baseline_path=baseline
    ).ok


def test_an_exclusion_is_named_even_when_the_gate_passes(repo, cat):
    """A scope you cannot see is a scope you cannot audit. A gate that quietly
    covers less than it claims reports 'ok' over the thing it cannot see."""
    result = lint_mod.lint(
        repo, cat, gateway_modules=["src/gateway.ts"], exclude=["src/bypass.ts"]
    )
    assert result.ok
    rendered = lint_mod.render_lint(result)
    assert "NOT covered by this claim" in rendered
    assert "src/bypass.ts" in rendered


# --- adopt ------------------------------------------------------------------


def test_adopt_designates_one_gateway_and_baselines_the_rest(repo, cat):
    """Declaring every file that calls a model to be a gateway makes the gate
    green instantly and meaningless — it green-lights the bypasses by renaming
    them, then reports 'every model call is made in the gateway'."""
    plan = adopt_mod.plan(repo, cat)
    assert plan.gateway_modules == ["src/gateway.ts"], "should prefer the hinted name"
    assert "src/bypass.ts" in plan.other_model_files


def test_adopt_generates_held_by_empty(repo, cat):
    """Not 'TODO', not 'under review' — empty, so the register prints `nothing`.

    Pre-filling it would launder an unknown into a claim, in the one column that
    matters.
    """
    plan = adopt_mod.plan(repo, cat)
    declaration = adopt_mod.render_declaration(plan)
    assert "heldBy: ''" in declaration
    assert "TODO" not in declaration


def test_adopt_generates_the_typed_wrapper_not_only_the_declaration(repo, cat):
    """The load-bearing part is the typed feature parameter. Without the wrapper
    the clever bit does not come with it and the declaration is just a comment
    that happens to compile."""
    declaration = adopt_mod.render_declaration(adopt_mod.plan(repo, cat))
    assert "export type AIFeature = keyof typeof AI_PROCESSES" in declaration
    assert "modelMeta(feature: AIFeature)" in declaration
    assert "const T extends Record<string, AIProcess>" in declaration


def test_adopt_stubs_every_discovered_process(repo, cat):
    plan = adopt_mod.plan(repo, cat)
    assert "invoice-extraction" in plan.processes
    assert "invoice-extraction" in adopt_mod.render_declaration(plan)


def test_adopt_picks_python_for_a_python_repo(cat, tmp_path):
    target = tmp_path / "py-messy"
    shutil.copytree(FIXTURES / "py-messy", target)
    plan = adopt_mod.plan(target, cat)
    assert plan.ecosystem == "py"
    assert plan.declaration_path.name == "ai_register.py"
    declaration = adopt_mod.render_declaration(plan)
    assert '"held_by": ""' in declaration
    assert "literal_eval" in declaration, "the dict must stay readable without importing"


def test_adopt_refuses_to_clobber_a_filled_in_declaration(repo, cat):
    """The prose in a declaration is the part that took the work."""
    plan = adopt_mod.plan(repo, cat)
    plan.declaration_path.write_text("// mine\n", encoding="utf-8")
    assert adopt_mod.plan(repo, cat).blocked
