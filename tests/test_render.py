"""Register rendering tests.

The properties under test here are honesty properties, not formatting ones. A
register that quietly rounds a gap up to a control, or that omits a process it
could not classify, is worse than no register — it launders an unknown into a
reassurance, in a document someone will rely on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heldby import catalog as catalog_mod
from heldby.render import Process, Register, render_json, render_markdown
from heldby.scan import scan

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def scans():
    cat = catalog_mod.load()
    return {
        "ts-messy": scan(FIXTURES / "ts-messy", cat, ignore_declarations=True),
        "py-messy": scan(FIXTURES / "py-messy", cat, ignore_declarations=True),
    }


def _register(processes: list[Process], **kw) -> Register:
    return Register(org="Example", summary="A test estate.", processes=processes, **kw)


GOOD = Process(
    name="invoice-extraction",
    ai_class="read",
    model="claude-haiku-4-5",
    repo="ts-messy",
    does="Reads an invoice.",
    held_by="Every column it names must exist in the file.",
)
GAP = Process(
    name="blast",
    ai_class="write",
    model="claude-sonnet-4-6",
    repo="ts-messy",
    does="Sends model prose to a third party.",
    held_by="",
    reaches=["external-comms"],
)


def test_a_gap_is_rendered_as_nothing_in_bold(scans):
    """The most useful row in a register is the one that says nothing holds it.

    Rendering an empty control as a blank cell, an em-dash, or "under review" is
    how a register becomes a brochure.
    """
    markdown = render_markdown(_register([GOOD, GAP]), scans)
    assert "**nothing**" in markdown
    assert "1 process(es) have nothing holding them" in markdown


def test_a_gap_is_counted_in_the_machine_output(scans):
    payload = render_json(_register([GOOD, GAP]), scans)
    assert payload["counts"]["without_control"] == 1
    row = next(p for p in payload["processes"] if p["name"] == "blast")
    assert row["has_control"] is False


def test_fail_on_gap_is_available_to_a_build(scans):
    payload = render_json(_register([GOOD]), scans)
    assert payload["counts"]["without_control"] == 0


def test_an_undeclared_process_is_named_as_a_register_defect(scans):
    """The sweep found a process the register does not declare.

    This is the completeness check earning its place: the table cannot tell you
    what is missing from it.
    """
    markdown = render_markdown(_register([GOOD]), scans)
    payload = render_json(_register([GOOD]), scans)
    undeclared = payload["completeness"]["undeclared_processes"]
    assert undeclared, "the py-messy fixture labels processes the register omits"
    assert "defect in the register" in markdown
    for name in undeclared:
        assert name in markdown


def test_no_undeclared_process_reads_as_a_clean_finding(scans):
    """Declaring everything the sweep found should say so plainly."""
    found = {n for report in scans.values() for names in report.labels.values() for n in names}
    processes = [
        Process(name=n, ai_class="read", model="m", repo="x", does="d", held_by="checked")
        for n in found
    ]
    markdown = render_markdown(_register(processes), scans)
    assert "No undeclared AI process was found" in markdown


def test_out_of_scope_uses_are_named_not_omitted(scans):
    excluded = [{"name": "ci-reviewer", "what": "Reads our own source.", "why": "Dev tooling."}]
    markdown = render_markdown(_register([GOOD], excluded=excluded), scans)
    assert "ci-reviewer" in markdown
    assert "not a scope, it is a smaller number" in markdown


def test_the_register_always_refuses_the_taint_reading(scans):
    markdown = render_markdown(_register([GOOD]), scans)
    payload = render_json(_register([GOOD]), scans)
    assert "not taint analysis" in markdown.lower()
    assert any("taint" in d.lower() for d in payload["disclaimers"])
    assert any("compliance certification" in d.lower() for d in payload["disclaimers"])


def test_excluded_scope_is_aggregated_once_not_per_component(scans):
    """The same caveat repeated once per component, each with a different number in
    it, is noise — and noise in a limits section is how it stops being read."""
    limits = render_json(_register([GOOD]), scans)["limits"]
    test_lines = [line for line in limits if "test file(s) were excluded" in line]
    assert len(test_lines) <= 1


def test_the_stricter_class_governs_the_ordering(scans):
    """Read < Converse < Decide < Write, so the table builds toward the rows that
    carry the most consequence rather than sorting alphabetically."""
    processes = [
        Process(name="w", ai_class="write", model="m", repo="r", does="d", held_by="h"),
        Process(name="r", ai_class="read", model="m", repo="r", does="d", held_by="h"),
        Process(name="d", ai_class="decide", model="m", repo="r", does="d", held_by="h"),
    ]
    order = [p.name for p in _register(processes).sorted_processes()]
    assert order == ["r", "d", "w"]


def test_provenance_pins_the_detection_surface(scans):
    """A register should name the exact catalogue it was produced with, so a
    later run that finds something new can be told apart from a changed table."""
    markdown = render_markdown(_register([GOOD]), scans)
    assert "Detection surface:" in markdown
    assert render_json(_register([GOOD]), scans)["provenance"]
