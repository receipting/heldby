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
    ai_class="send",
    model="claude-sonnet-4-6",
    repo="ts-messy",
    does="Sends model prose to a third party.",
    held_by="",
    reaches=["external-comms"],
)


def test_the_tier_is_derived_from_the_class_and_fails_closed():
    """Inform and Act are not a fifth thing to classify — they fall out of the class.

    An unrecognised class reads as Act, because the failure that matters is a
    process that crosses being treated as one that does not.
    """
    assert GOOD.tier == "inform"
    assert GAP.tier == "act"
    assert Process(
        name="x", ai_class="freestyle", model="m", repo="r", does="d", held_by="h"
    ).tier == "act"


def test_a_class_that_disagrees_with_its_reach_is_a_contradiction(scans):
    """Class and reach used to be two independent assertions with nothing forcing
    them to agree, so a process could be filed Read while sitting on a call that
    moves money and no part of the register would object.

    This is the check that catches the real misfiling — cheapest label, protected
    action underneath — and it is arithmetic rather than a judgement call.
    """
    misfiled = Process(
        name="bank-routing",
        ai_class="read",
        model="m",
        repo="r",
        does="Picks which account a row belongs to.",
        held_by="Chooses only from accounts already on file.",
        reaches=["move-money"],
    )
    assert misfiled.contradicts_tier
    assert not GOOD.contradicts_tier, "reaches nothing, so nothing to contradict"
    assert not GAP.contradicts_tier, "Act-tier and it reaches — that agrees"

    md = render_markdown(_register([misfiled]), scans)
    assert "bank-routing" in md
    assert "move-money" in md
    assert "filed as staying inside" in md


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
    assert "register defect" in markdown
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
    assert "No undeclared AI process found" in markdown


def test_out_of_scope_uses_are_named_not_omitted(scans):
    excluded = [{"name": "ci-reviewer", "what": "Reads our own source.", "why": "Dev tooling."}]
    markdown = render_markdown(_register([GOOD], excluded=excluded), scans)
    assert "ci-reviewer" in markdown
    assert "named rather than dropped" in markdown


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
    """Read < Converse < Decide < Send, so the table builds toward the rows that
    carry the most consequence rather than sorting alphabetically."""
    processes = [
        Process(name="s", ai_class="send", model="m", repo="r", does="d", held_by="h"),
        Process(name="r", ai_class="read", model="m", repo="r", does="d", held_by="h"),
        Process(name="d", ai_class="decide", model="m", repo="r", does="d", held_by="h"),
    ]
    order = [p.name for p in _register(processes).sorted_processes()]
    assert order == ["r", "d", "s"]


def test_provenance_pins_the_detection_surface(scans):
    """A register should name the exact catalogue it was produced with, so a
    later run that finds something new can be told apart from a changed table."""
    markdown = render_markdown(_register([GOOD]), scans)
    assert "Detection surface:" in markdown
    assert render_json(_register([GOOD]), scans)["provenance"]


def test_a_grouped_row_resolves_the_labels_it_covers(scans):
    """Thirteen agents differing only in their prompt are one process, not thirteen.

    Grouping them is right, but the completeness check then reports every label
    the row does not literally match as undeclared — which happened on the first
    real cold run and made a clean register look like it had gaps.
    """
    found = {n for r in scans.values() for names in r.labels.values() for n in names}
    assert len(found) >= 2, "fixture must label more than one process"
    grouped = Process(
        name="the-agents",
        ai_class="decide",
        model="selected at runtime",
        repo="x",
        does="Several agents that differ only in their prompt.",
        held_by="A typed enum on the output.",
        covers=sorted(found),
    )
    payload = render_json(_register([grouped]), scans)
    assert payload["completeness"]["undeclared_processes"] == []
    assert payload["completeness"]["declared_but_not_located"] == []


def test_an_unlocated_row_blames_naming_first(scans):
    """The usual cause is a descriptive row name, not a missing process."""
    ghost = Process(
        name="nothing-in-the-code-is-called-this",
        ai_class="read", model="m", repo="x", does="d", held_by="h",
    )
    markdown = render_markdown(_register([ghost]), scans)
    assert "naming mismatch" in markdown
    assert "`covers`" in markdown


def test_a_model_call_no_row_claims_is_reported(scans):
    """The stronger completeness claim.

    Label coverage asks whether every name found is written down. This asks whether
    every place a model runs is accounted for — and plenty of real processes never
    name themselves, so the first check cannot see them at all.
    """
    payload = render_json(_register([GOOD]), scans)
    unattributed = payload["completeness"]["unattributed_model_sites"]
    assert unattributed, "the fixtures contain model calls no row claims"
    assert "a model call no row claims" in render_markdown(_register([GOOD]), scans)


def test_a_row_claiming_its_files_is_located_without_a_label(scans):
    """Thirteen agents can be plainly visible as call sites while naming themselves
    nowhere. Pointing a row at the files locates it."""
    every_file = sorted({s.file for r in scans.values() for s in r.model_sites})
    row = Process(
        name="a-process-the-code-never-names",
        ai_class="decide", model="selected at runtime", repo="x",
        does="d", held_by="h", files=every_file,
    )
    payload = render_json(_register([row]), scans)
    assert payload["completeness"]["unattributed_model_sites"] == []
    assert payload["completeness"]["declared_but_not_located"] == []


DRAFT = Process(
    name="drafted-thing",
    ai_class="read",
    model="selected at runtime",
    repo="x",
    does="d",
    held_by="a mechanism",
    source="drafted",
)


def test_a_drafted_register_says_so_before_anything_else(scans):
    """The draft is a starting point for a conversation, not a claim of record.

    A machine-drafted row that reads identically to a reviewed one launders a
    guess into a register — so the banner leads, and every drafted row is marked.
    """
    markdown = render_markdown(_register([GOOD, DRAFT]), scans)
    banner = markdown.index("Machine-drafted.")
    first_row = markdown.index("`invoice-extraction`")
    assert banner < first_row, "the banner must come before any row"
    assert "— DRAFT" in markdown.splitlines()[0]
    assert "`drafted-thing` †" in markdown
    assert "`invoice-extraction` |" in markdown, "a reviewed row carries no mark"


def test_a_fully_reviewed_register_carries_no_draft_furniture(scans):
    markdown = render_markdown(_register([GOOD]), scans)
    assert "DRAFT" not in markdown.splitlines()[0]
    assert "Machine-drafted" not in markdown


def test_key_findings_lead_the_document(scans):
    """The first screen decides whether the rest gets read at all."""
    register = _register([GOOD])
    register.key_findings = ["**The one that matters.** A model call sits one file from money."]
    markdown = render_markdown(register, scans)
    table = markdown.index("| Process | Class | Held by |")
    findings = markdown.index("## Worth knowing")
    detail = markdown.index("## The detail")
    assert table < findings < detail, "table first, findings second, detail last"
    assert "one file from money" in markdown


def test_drafted_count_is_in_the_machine_output(scans):
    payload = render_json(_register([GOOD, DRAFT]), scans)
    assert payload["counts"]["drafted_unreviewed"] == 1
    row = next(p for p in payload["processes"] if p["name"] == "drafted-thing")
    assert row["source"] == "drafted"


def test_html_is_one_self_contained_file(scans):
    """It has to survive being emailed as a single attachment with nothing
    alongside it, so no external request of any kind."""
    from heldby.html import render_html

    page = render_html(_register([GOOD, GAP]), scans)
    assert page.startswith("<!doctype html>")
    for forbidden in ("<script", "src=", "@import", "//fonts.", "cdn."):
        assert forbidden not in page, f"external dependency: {forbidden}"
    assert "<style>" in page and "@page" in page, "must carry its own print stylesheet"


def test_html_marks_the_gap_and_the_draft(scans):
    from heldby.html import render_html

    page = render_html(_register([GOOD, GAP]), scans)
    assert "nothing</span>" in page
    assert "have nothing holding them" in page


def test_html_escapes_source_it_quotes(scans):
    """The register quotes real source lines and some of them contain HTML."""
    from heldby.html import render_html

    row = Process(
        name="x", ai_class="read", model="m", repo="r",
        does='renders <script>alert(1)</script> into the page',
        held_by="output is escaped by `escapeHtml`",
    )
    page = render_html(_register([row]), scans)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "<code>escapeHtml</code>" in page, "inline markdown still renders"


def test_reaches_provenance_matches_how_the_run_was_made(scans):
    """A declared run reports a reviewed claim; an inferred run reports a
    reading. Printing the declared wording over an inferred run overstates the
    provenance of the column an auditor leans on hardest."""
    from heldby.render import _aggregate_limits

    text = " ".join(_aggregate_limits(scans))
    assert "reader's inference from the code" in text, "these fixtures ignore declarations"
    assert "reviewed claim" not in text


def test_scope_table_columns_are_20_40_40(scans):
    """The two prose columns need equal room. Under auto layout the browser hands
    width to whichever column has the longest unbreakable run — the Item column is
    all identifiers — and the last column collapses to a ribbon."""
    from heldby.html import render_html

    excluded = [{"name": "a-very-long-identifier-name", "what": "x", "why": "y"}]
    page = render_html(_register([GOOD], excluded=excluded), scans)
    assert 'table class="scope"' in page
    assert "table.scope { table-layout:fixed; }" in page
    for n, w in ((1, "20%"), (2, "40%"), (3, "40%")):
        assert f"table.scope td:nth-child({n}) {{ width:{w}; }}" in page
    assert "table.scope code { overflow-wrap:anywhere" in page, "identifiers must wrap"


def test_lifecycle_controls_are_kept_out_of_the_held_by_column(scans):
    """What holds the estate as it changes is not what holds an output.

    A declaration gate and a lint reduce the chance of an unheld process
    existing. Neither stands between one model's output and one effect, which is
    what `held_by` records — and the moment a register lets them into that
    column, assurance reads as a gate.
    """
    reg = _register([GOOD], lifecycle={"Enforced": "A lint refuses an undeclared call."})
    text = render_markdown(reg, scans)
    assert "## What holds the AI as it changes" in text
    assert "A lint refuses an undeclared call." in text
    # ...and it says why it is a separate section rather than a row.
    assert "has to stand between one output and one effect" in text


def test_a_register_with_no_lifecycle_section_omits_it(scans):
    assert "What holds the AI as it changes" not in render_markdown(_register([GOOD]), scans)


def test_lifecycle_reaches_the_machine_output(scans):
    payload = render_json(_register([GOOD], lifecycle={"Declared": "It will not compile."}), scans)
    assert payload["lifecycle"] == {"Declared": "It will not compile."}


def test_a_scoped_out_model_call_is_not_reported_as_a_defect(scans):
    """The completeness check must still report the file — no row claims it, and
    that is true — without contradicting the Scope section that just explained
    it. Reporting it in the same red as a real gap trains a reader to skip both.
    """
    from heldby.html import render_html

    # Claims every model-call file except src/bypass.ts, which Scope names.
    row = Process(name="x", ai_class="read", model="m", repo="ts-messy", does="d",
                  held_by="h", files=["src/gateway.ts", "agents.py", "report.py"])
    excluded = [{"name": "bypass.ts", "what": "An eval harness.", "why": "Not shipped."}]
    reg = _register([row], excluded=excluded)
    text = render_markdown(reg, scans)
    assert "the Scope section above accounts for each" in text
    assert "see *bypass.ts* above" in text
    page = render_html(reg, scans)
    assert 'class="clear"' in page and "bypass.ts</em> above" in page


def test_an_unexplained_model_call_still_reads_as_a_finding(scans):
    """The softening is only for files Scope names. Anything else stays a gap."""
    from heldby.html import render_html

    row = Process(name="x", ai_class="read", model="m", repo="ts-messy", does="d", held_by="h")
    page = render_html(_register([row]), scans)
    assert 'class="alarm"><p>' in page
