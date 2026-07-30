"""Render the register — the human artefact and the machine one.

The register is the point of all of this. Everything else is scaffolding for
producing a table someone can hand to an auditor, paste into a vendor
questionnaire, or publish as a trust-centre page.

Two rules govern what this may write.

**Class and `held_by` are never inferred here.** They come from a classification
input a person wrote or reviewed. This module will not invent a control, and it
will not soften a missing one: a process whose `held_by` is empty renders as
**nothing**, in bold, in the table a customer reads. A register that cannot record
a gap is a brochure.

**The completeness claim is the scan's, and it is bounded.** "We swept the code and
found no undeclared AI process" is worth something only alongside what the sweep
could not see, so the limits travel with the table and are not an appendix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .scan import ScanReport

CLASS_ORDER = ["read", "converse", "decide", "write"]
CLASS_LABEL = {"read": "Read", "converse": "Converse", "decide": "Decide", "write": "Write"}

CLASS_RULE = {
    "read": "Turns a document or message into structured data. No person required — but the "
    "output is checked against something real: a column that exists in the file, an account "
    "already on file, a total that reconciles.",
    "decide": "Proposes an action with a consequential real-world effect. No person on the fast "
    "path *by design*. Bounded by deterministic gates plus a configured threshold; everything "
    "outside the threshold goes to a queue a person works.",
    "converse": "Answers the person who asked — them and us, nobody else. No separate review, "
    "because the person who asked is the person who judges the answer, as they read it.",
    "write": "Produces prose the system will carry to someone else. A named person edits and "
    "releases it, and the record says who. Nothing AI-written leaves unattended.",
}


@dataclass
class Process:
    name: str
    ai_class: str
    model: str
    repo: str
    does: str
    held_by: str
    reaches: list[str] = field(default_factory=list)
    source: str = "declared"
    #: Other names the code uses for this process. A register row is often
    #: legitimately one row for many call sites — thirteen agents that differ only
    #: in their prompt are one process, not thirteen — and the completeness check
    #: has to resolve those labels to this row or it reports them as undeclared.
    covers: list[str] = field(default_factory=list)

    @property
    def has_control(self) -> bool:
        return bool(self.held_by.strip())


@dataclass
class Register:
    org: str
    summary: str
    processes: list[Process]
    protected_actions: dict[str, str] = field(default_factory=dict)
    scope_notes: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    generated: str = ""

    def sorted_processes(self) -> list[Process]:
        return sorted(
            self.processes,
            key=lambda p: (CLASS_ORDER.index(p.ai_class) if p.ai_class in CLASS_ORDER else 9,
                           p.repo, p.name),
        )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _completeness(register: Register, scans: dict[str, ScanReport]) -> tuple[list[str], list[str]]:
    """Compare what was declared against what the sweep found.

    Returns (undeclared, unfound). `undeclared` is the finding that matters: a
    process name the code labels itself with that no declaration covers. `unfound`
    is the softer direction — a declared process the sweep could not see, which is
    a limit of the sweep rather than a defect in the register, and is reported as
    such rather than quietly dropped.
    """
    declared = {p.name for p in register.processes}
    declared |= {name for p in register.processes for name in p.covers}
    excluded = {e.get("name", "") for e in register.excluded}
    found: set[str] = set()
    for report in scans.values():
        for names in report.labels.values():
            found.update(names)

    undeclared = sorted(n for n in found - declared - excluded)
    # Only rows the code never names anywhere count as unfound. A row that
    # declares `covers` has already been resolved above.
    unfound = sorted(
        p.name
        for p in register.processes
        if p.name not in found and not (set(p.covers) & found)
    )
    return undeclared, unfound


#: Caveats that hold for every sweep regardless of what it found. Stated once.
UNIVERSAL_LIMITS = [
    "**This is not taint analysis and must not be read as any.** The sweep has no call graph "
    "and no value flow. `Reaches` above is a reviewed claim from each component's own "
    "declaration, never a path proved from the source.",
    "A model client built by a factory in one module and called in another leaves no call site "
    "in the module that owns the feature. The sweep reports both and links neither, so a "
    "process is credited to a component by its declaration rather than by where its client "
    "was constructed.",
    "Only TypeScript, JavaScript and Python were read. A model call written in Go, Java, C#, "
    "Ruby, PHP or Rust would be invisible to the sweep. The estate contains none, which is a "
    "statement about the estate and not something the sweep could have established.",
]

#: Aggregated across components, because the same caveat repeated once per component
#: with a different number in it is noise, and noise in this section is how a limits
#: list stops being read.
SKIP_PROSE = {
    "tests": (
        "{n} test file(s) were excluded. A test that names every provider URL it supports is "
        "not an AI use, and those sites outnumber and bury the real ones — but an AI call "
        "existing only in a test would be invisible here."
    ),
    "nested-checkouts": (
        "{n} file(s) inside nested checkouts of other branches were excluded. Their code is "
        "real but it is not the shipped branch's. An AI call that exists only on an unmerged "
        "branch is invisible here, and is worth checking separately before it ships."
    ),
    "type-declarations": (
        "{n} ambient type-declaration file(s) were excluded. They describe interfaces rather "
        "than call them."
    ),
    "too-large": "{n} file(s) were too large to read and were not scanned.",
    "unreadable": "{n} file(s) could not be read at all.",
}


def _aggregate_limits(scans: dict[str, ScanReport]) -> list[str]:
    out = list(UNIVERSAL_LIMITS)

    out.append(
        "The sweep was run with declarations deliberately hidden from it, so that its "
        "completeness finding is independent of the table it is checking. It located the "
        "processes above from the source alone."
    )

    totals: dict[str, int] = {}
    for report in scans.values():
        for reason, count in report.files_skipped.items():
            totals[reason] = totals.get(reason, 0) + count
    for reason, prose in SKIP_PROSE.items():
        if totals.get(reason):
            out.append(prose.format(n=totals[reason]))

    unconfirmed = sorted(
        name
        for name, report in scans.items()
        if report.gateways and not any(s.confidence == "confirmed" for s in report.sites if s.rule_id in report.gateways)
    )
    if unconfirmed:
        out.append(
            "In "
            + ", ".join(unconfirmed)
            + " a gateway appears in configuration but the sweep could not confirm from the "
            "source alone that every call routes through it, so it makes no claim either way. "
            "The gateway is in any case a metering and logging boundary, not a control, and is "
            "never credited in the *Held by* column."
        )
    return out


def render_markdown(
    register: Register, scans: dict[str, ScanReport], *, layout: str = "table"
) -> str:
    """`layout="sections"` for print; `"table"` for a screen. See the note below."""
    out: list[str] = []
    generated = register.generated or date.today().strftime("%-d %B %Y")

    out.append(f"# AI register — {register.org}")
    out.append("")
    out.append(register.summary.strip())
    out.append("")
    out.append(
        "Every place a model runs in this system, what class of use it is, and — the column "
        "that matters — **what stands between the model's output and a real-world effect**."
    )
    out.append("")

    # --- the framework, so the table can be read without a briefing ----------
    out.append("## How to read this")
    out.append("")
    out.append(
        "Two questions get asked about AI and neither is useful. *Do you use AI?* — everyone "
        "does. *How accurate is the model?* — unanswerable, and the wrong axis. The model will "
        "be wrong at a rate nobody can drive to zero, so the question worth asking is **what it "
        "can reach when it is**."
    )
    out.append("")
    out.append("Each use is classified by its gate, not by its technology:")
    out.append("")
    out.append("| Class | The rule |")
    out.append("|---|---|")
    for key in CLASS_ORDER:
        out.append(f"| **{CLASS_LABEL[key]}** | {_cell(CLASS_RULE[key])} |")
    out.append("")
    out.append(
        "Where a process spans two classes, **the stricter class governs** — Read < Converse "
        "< Decide < Write. Two call sites using the same model on the same document belong to "
        "different classes if what they can reach differs."
    )
    out.append("")

    # --- the register itself --------------------------------------------------
    # Two layouts, because the artefact has two audiences. A table is right on a
    # screen, where a reader scans down the Held-by column comparing controls. It
    # is wrong on paper: six columns whose last cell is a paragraph collapses into
    # an unreadable ribbon at A4. The sections layout is the same content, one
    # process at a time, for anything that gets printed or emailed as a PDF.
    processes = register.sorted_processes()
    out.append(f"## The register — {len(processes)} processes")
    out.append("")

    if layout == "sections":
        for key in CLASS_ORDER:
            group = [p for p in processes if p.ai_class == key]
            if not group:
                continue
            out.append(f"### {CLASS_LABEL[key]} — {len(group)}")
            out.append("")
            out.append(f"_{CLASS_RULE[key]}_")
            out.append("")
            for p in group:
                out.append(f"**`{p.name}`** · {p.model} · {p.repo}")
                out.append("")
                if p.does:
                    out.append(p.does.strip())
                    out.append("")
                if p.reaches:
                    out.append(f"*Reaches:* {', '.join(p.reaches)}")
                    out.append("")
                held = p.held_by.strip() if p.has_control else "**Nothing.**"
                out.append(f"*Held by:* {held}")
                out.append("")
    else:
        out.append("| Process | Class | Model | Component | Reaches | Held by |")
        out.append("|---|---|---|---|---|---|")
        for p in processes:
            reaches = ", ".join(f"`{r}`" for r in p.reaches) if p.reaches else "—"
            held = _cell(p.held_by) if p.has_control else "**nothing**"
            out.append(
                f"| `{p.name}` | {CLASS_LABEL.get(p.ai_class, p.ai_class)} | `{p.model}` | "
                f"{p.repo} | {reaches} | {held} |"
            )
        out.append("")

    gaps = [p for p in processes if not p.has_control]
    if gaps:
        out.append(
            f"**{len(gaps)} process(es) have nothing holding them.** They are listed above with "
            "`nothing` in the last column rather than omitted, because a register that cannot "
            "record a gap is a brochure."
        )
        out.append("")

    # --- what each protected action rests on ---------------------------------
    if register.protected_actions:
        out.append("## What holds each protected action")
        out.append("")
        for action, prose in register.protected_actions.items():
            out.append(f"- **{action}** — {prose.strip()}")
        out.append("")

    # --- scope ---------------------------------------------------------------
    if register.scope_notes or register.excluded:
        out.append("## Scope")
        out.append("")
        for note in register.scope_notes:
            out.append(note.strip())
            out.append("")
        if register.excluded:
            out.append(
                "These uses are **outside the scope above**. They are named rather than omitted: "
                "a count reduced by quietly deleting rows is not a scope, it is a smaller number."
            )
            out.append("")
            out.append("| Outside scope | What it is | Why |")
            out.append("|---|---|---|")
            for item in register.excluded:
                out.append(
                    f"| `{item.get('name', '?')}` | {_cell(item.get('what', ''))} | "
                    f"{_cell(item.get('why', ''))} |"
                )
            out.append("")

    # --- the independent completeness check ----------------------------------
    undeclared, unfound = _completeness(register, scans)
    total_files = sum(r.files_scanned for r in scans.values())
    total_models = sum(len(r.model_sites) for r in scans.values())
    total_actions = sum(len(r.action_sites) for r in scans.values())

    out.append("## Independent completeness check")
    out.append("")
    out.append(
        "The table above is generated from each component's own declaration. This section is "
        "the other direction: an independent sweep of the source code, which knows nothing "
        "about those declarations, looking for AI the register does not mention."
    )
    out.append("")
    out.append(
        f"**{total_files} source files** were read across {len(scans)} components. The sweep "
        f"found **{total_models} model call sites** and **{total_actions} protected-action call "
        f"sites**."
    )
    out.append("")
    if undeclared:
        out.append(
            f"**{len(undeclared)} AI process(es) found in the code that the register does not "
            "declare.** This is a defect in the register, not a note for later:"
        )
        out.append("")
        for name in undeclared:
            out.append(f"- `{name}`")
        out.append("")
    else:
        out.append(
            "**No undeclared AI process was found.** Every process the code labels itself with "
            "appears in the register above, or in the out-of-scope table."
        )
        out.append("")
    if unfound:
        out.append(
            f"{len(unfound)} declared process(es) could not be located by the sweep "
            f"({', '.join(f'`{n}`' for n in unfound)}). **Check the names first**: the most "
            "common cause is a register row named descriptively while the code labels itself "
            "something else, which is a naming mismatch rather than a missing process — set "
            "`covers` on the row to the labels the code actually uses. Where the names do "
            "agree, this is a limit of the sweep and the declaration is the stronger source."
        )
        out.append("")

    # --- limits --------------------------------------------------------------
    out.append("## What this analysis cannot see")
    out.append("")
    out.append(
        "A proof that names its own limits is the only kind worth publishing. Everything below "
        "is a real boundary on the claim made above, stated so that a reader can judge the "
        "claim rather than take it."
    )
    out.append("")
    for limit in _aggregate_limits(scans):
        out.append(f"- {limit}")
    out.append("")
    out.append("This register is not a compliance certification and is not legal advice.")
    out.append("")

    out.append("---")
    out.append("")
    digests = sorted({f"{p['id']}@{p['digest']}" for r in scans.values() for p in r.provenance})
    out.append(
        f"_Generated {generated} by [heldby](https://github.com/receipting/heldby). "
        f"Detection surface: {', '.join(digests)}._"
    )
    out.append("")
    return "\n".join(out)


def render_json(register: Register, scans: dict[str, ScanReport]) -> dict:
    undeclared, unfound = _completeness(register, scans)
    return {
        "schema": "heldby.register.v0",
        "organisation": register.org,
        "generated": register.generated or date.today().isoformat(),
        "processes": [
            {
                "name": p.name,
                "class": p.ai_class,
                "model": p.model,
                "component": p.repo,
                "does": p.does,
                "reaches": p.reaches,
                "covers": p.covers,
                "held_by": p.held_by,
                "has_control": p.has_control,
                "source": p.source,
            }
            for p in register.sorted_processes()
        ],
        "counts": {
            "processes": len(register.processes),
            "by_class": {
                key: sum(1 for p in register.processes if p.ai_class == key) for key in CLASS_ORDER
            },
            "without_control": sum(1 for p in register.processes if not p.has_control),
        },
        "protected_actions": register.protected_actions,
        "excluded": register.excluded,
        "completeness": {
            "components_swept": sorted(scans),
            "files_read": sum(r.files_scanned for r in scans.values()),
            "model_sites": sum(len(r.model_sites) for r in scans.values()),
            "protected_action_sites": sum(len(r.action_sites) for r in scans.values()),
            "undeclared_processes": undeclared,
            "declared_but_not_located": unfound,
        },
        "limits": _aggregate_limits(scans),
        "provenance": sorted(
            {f"{p['id']}@{p['digest']}" for r in scans.values() for p in r.provenance}
        ),
        "disclaimers": [
            "Not taint analysis. `reaches` is a reviewed claim from each component's "
            "declaration, not a proof derived from the code.",
            "Not a model-accuracy evaluation.",
            "Not a compliance certification and not legal advice.",
        ],
    }
