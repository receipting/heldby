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

CLASS_ORDER = ["read", "converse", "decide", "send"]
CLASS_LABEL = {"read": "Read", "converse": "Converse", "decide": "Decide", "send": "Send"}

CLASS_LINE = {
    "read": "turns documents or messages into data. The output gets checked against something real.",
    "converse": "answers the person who asked. It reaches nobody else and does nothing.",
    "decide": "acts with no person on the fast path, inside deterministic bounds and a threshold.",
    "send": "carries prose out to someone else. A named person releases it.",
}

CLASS_RULE = {
    "read": "Turns a document or message into structured data. No person required — but the "
    "output is checked against something real: a column that exists in the file, an account "
    "already on file, a total that reconciles.",
    "decide": "Proposes an action with a consequential real-world effect. No person on the fast "
    "path *by design*. Bounded by deterministic gates plus a configured threshold; everything "
    "outside the threshold goes to a queue a person works.",
    "converse": "Answers the person who asked — them and us, nobody else. No separate review, "
    "because the person who asked is the person who judges the answer, as they read it.",
    "send": "Carries prose the system will put in front of someone else. A named person edits "
    "and releases it, and the record says who. Nothing AI-written leaves unattended.",
}

#: The tier a class belongs to. Two tiers, because the thing that decides how much
#: a mistake costs is not what the model does — it is whether the output crosses
#: out of the organisation. Read and Converse stay inside; Decide and Send cross.
#:
#: This is the older split rediscovered: Parasuraman, Sheridan & Wickens (2000)
#: separate the *information* stages of automation (acquisition, analysis) from the
#: *action* stages (decision selection, action implementation), and put the risk in
#: the second pair. The Model Context Protocol reaches for the same line when it
#: marks a tool `openWorldHint` — "does the tool interact with an open world of
#: external entities, or is its domain closed?" — and makes that flag, not the
#: model, decide whether a host stops to ask permission.
CLASS_TIER = {"read": "inform", "converse": "inform", "decide": "act", "send": "act"}

TIER_ORDER = ["inform", "act"]
TIER_LABEL = {"inform": "Inform", "act": "Act"}

TIER_LINE = {
    "inform": "the output stays inside. Wrong costs you rework, and nobody outside ever knows.",
    "act": "the output crosses out — money moves, or words reach someone else. "
    "Every control worth having sits here.",
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
    #: Files or path prefixes whose model call sites this row accounts for. The
    #: stronger completeness claim: not every label is declared, but every model
    #: call site in the repo is attributable to some row. Plenty of real processes
    #: never label themselves at all — thirteen agents can be plainly visible as
    #: call sites while naming themselves nowhere — so a check that only compares
    #: names cannot see whether the register actually covers the code.
    files: list[str] = field(default_factory=list)

    #: The table cell. The full held_by is the detail; this is what a developer
    #: scans. Drafted short by whoever classified; falls back to the first
    #: sentence of held_by.
    held_by_short: str = ""

    @property
    def has_control(self) -> bool:
        return bool(self.held_by.strip())

    @property
    def tier(self) -> str:
        """`inform` or `act`. Unknown classes read as `act` — fail closed."""
        return CLASS_TIER.get(self.ai_class, "act")

    @property
    def contradicts_tier(self) -> bool:
        """Filed as staying inside, yet it reaches something outside.

        Class and reach used to be two independent assertions with nothing forcing
        them to agree, so a process could be filed Read while sitting on a call
        that moves money and no part of the register would object. The tier is what
        makes them checkable against each other: a Read that reaches `move-money`
        is not a judgement call anyone needs to relitigate, it is a contradiction
        in the row itself.

        Not an error here — this module never overrules a classification, it
        reports. But an unresolved contradiction is a finding, and it belongs in
        front of the reader rather than in a footnote.
        """
        return self.tier == "inform" and bool(self.reaches)

    @property
    def cell(self) -> str:
        if not self.has_control:
            return "**nothing**"
        if self.held_by_short.strip():
            return self.held_by_short.strip().rstrip(".")
        first = self.held_by.strip().split(". ")[0].rstrip(".")
        return first if len(first) <= 90 else first[:87] + "…"


@dataclass
class Register:
    org: str
    summary: str
    processes: list[Process]
    #: The findings a reader should meet before any table. The first screen decides
    #: whether the rest gets read at all: a register that opens with methodology is
    #: filed, one that opens with "a model call sits one file from pip install" is
    #: acted on. Written by whoever classified — never generated here.
    key_findings: list[str] = field(default_factory=list)
    protected_actions: dict[str, str] = field(default_factory=dict)
    scope_notes: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    generated: str = ""

    @property
    def drafted(self) -> list[Process]:
        return [p for p in self.processes if p.source == "drafted"]

    def sorted_processes(self) -> list[Process]:
        return sorted(
            self.processes,
            key=lambda p: (CLASS_ORDER.index(p.ai_class) if p.ai_class in CLASS_ORDER else 9,
                           p.repo, p.name),
        )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _unattributed_sites(register: Register, scans: dict[str, ScanReport]) -> list[str]:
    """Files containing a model call that no register row claims.

    Label coverage answers "is every name we found written down". This answers the
    question that actually matters: "is every place a model runs accounted for".
    """
    claimed = [f.rstrip("/") for p in register.processes for f in p.files]
    out: set[str] = set()
    for report in scans.values():
        for site in report.model_sites:
            if any(site.file == c or site.file.startswith(f"{c}/") for c in claimed):
                continue
            out.add(site.file)
    return sorted(out)


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
        # A row that points at real files is located, whether or not the code ever
        # says its name out loud.
        if p.name not in found and not (set(p.covers) & found) and not p.files
    )
    return undeclared, unfound


#: Caveats that hold for every sweep regardless of what it found. Stated once.
#: The first one has two forms, because where `reaches` came from changes what the
#: sentence may claim: a declared run reports a reviewed claim, an inferred run
#: reports somebody's reading. Printing the declared wording over an inferred run
#: overstates the provenance of the column an auditor leans on hardest.
TAINT_LIMIT_DECLARED = (
    "**This is not taint analysis and must not be read as any.** The sweep has no call graph "
    "and no value flow. `Reaches` above is a reviewed claim from each component's own "
    "declaration, never a path proved from the source."
)
TAINT_LIMIT_INFERRED = (
    "**This is not taint analysis and must not be read as any.** The sweep has no call graph "
    "and no value flow. Declarations were ignored on this run, so `Reaches` above is a "
    "reader's inference from the code — cited, but not proved."
)

UNIVERSAL_LIMITS = [
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
    inferred = any(r.declarations_ignored for r in scans.values())
    out = [TAINT_LIMIT_INFERRED if inferred else TAINT_LIMIT_DECLARED, *UNIVERSAL_LIMITS]

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


def render_markdown(register: Register, scans: dict[str, ScanReport]) -> str:
    """The document a developer opens. The table is the product; it comes first.

    Structure and prose follow VOICE.md. The framework essay lives in the README,
    not here — a reader of the register gets four bullets and the table.
    """
    out: list[str] = []
    generated = register.generated or date.today().strftime("%-d %B %Y")
    drafted = register.drafted
    processes = register.sorted_processes()

    out.append(f"# AI register — {register.org}{' — DRAFT' if drafted else ''}")
    out.append("")
    if drafted:
        out.append(
            f"> Machine-drafted. {len(drafted)} of {len(register.processes)} rows are "
            "unreviewed, marked †. Reviewing a row removes the mark; rewording it doesn't."
        )
        out.append("")

    out.append(
        "Ask one question first: **when this is wrong, does anyone outside the "
        "organisation find out?**"
    )
    out.append("")
    for key in TIER_ORDER:
        out.append(f"- **{TIER_LABEL[key]}** — {TIER_LINE[key]}")
    out.append("")
    out.append("Each tier splits in two, by what the model is doing:")
    out.append("")
    for key in CLASS_ORDER:
        out.append(
            f"- **{CLASS_LABEL[key]}** ({TIER_LABEL[CLASS_TIER[key]]}) — {CLASS_LINE[key]}"
        )
    out.append("")
    out.append(
        "Risk rises left to right: Read < Converse < Decide < Send. A process spanning two "
        "classes gets the stricter one. This report puts every AI call in this repo into one "
        "of the four."
    )
    out.append("")

    if register.summary.strip():
        out.append(register.summary.strip())
        out.append("")
    files = sum(r.files_scanned for r in scans.values())
    models = sum(len(r.model_sites) for r in scans.values())
    actions = sum(len(r.action_sites) for r in scans.values())
    out.append(
        f"The sweep read {files} files and found {models} model call sites and "
        f"{actions} protected-action call sites."
    )
    out.append("")

    # --- the table -----------------------------------------------------------
    out.append("| Process | Class | Held by |")
    out.append("|---|---|---|")
    for p in processes:
        mark = " †" if p.source == "drafted" else ""
        out.append(f"| `{p.name}`{mark} | {CLASS_LABEL.get(p.ai_class, p.ai_class)} | {_cell(p.cell)} |")
    out.append("")
    if drafted:
        out.append("_† machine-drafted, unreviewed._")
        out.append("")
    gaps = [p for p in processes if not p.has_control]
    if gaps:
        out.append(f"**{len(gaps)} process(es) have nothing holding them.** The gap is the finding.")
        out.append("")
    crossed = [p for p in processes if p.contradicts_tier]
    if crossed:
        out.append(
            f"**{len(crossed)} process(es) are filed as staying inside the organisation "
            "while reaching something outside it.** Either the class is wrong or the "
            "`reaches` list is, and which one it is changes what has to be built:"
        )
        out.append("")
        for p in crossed:
            out.append(
                f"- `{p.name}` — filed **{CLASS_LABEL.get(p.ai_class, p.ai_class)}** "
                f"({TIER_LABEL[p.tier]}), reaches {', '.join(f'`{r}`' for r in p.reaches)}"
            )
        out.append("")

    if register.key_findings:
        out.append("## Worth knowing")
        out.append("")
        for i, finding in enumerate(register.key_findings, start=1):
            out.append(f"{i}. {finding.strip()}")
            out.append("")

    # --- the detail ----------------------------------------------------------
    out.append("## The detail")
    out.append("")
    for key in CLASS_ORDER:
        group = [p for p in processes if p.ai_class == key]
        if not group:
            continue
        out.append(f"### {CLASS_LABEL[key]} — {len(group)}")
        out.append("")
        for p in group:
            mark = " †" if p.source == "drafted" else ""
            out.append(f"**`{p.name}`**{mark} · {p.model} · {p.repo}")
            out.append("")
            if p.does:
                out.append(p.does.strip())
                out.append("")
            if p.reaches:
                out.append(f"*Reaches:* {', '.join(p.reaches)}")
                out.append("")
            out.append(f"*Held by:* {p.held_by.strip() if p.has_control else '**Nothing.**'}")
            out.append("")

    if register.protected_actions:
        out.append("## What this repo can and can't do")
        out.append("")
        for action, prose in register.protected_actions.items():
            out.append(f"- **{action}** — {prose.strip()}")
        out.append("")

    if register.scope_notes or register.excluded:
        out.append("## Scope")
        out.append("")
        for note in register.scope_notes:
            out.append(note.strip())
            out.append("")
        if register.excluded:
            out.append("Outside scope, named rather than dropped:")
            out.append("")
            out.append("| Item | What it is | Why it's out |")
            out.append("|---|---|---|")
            for item in register.excluded:
                out.append(
                    f"| `{item.get('name', '?')}` | {_cell(item.get('what', ''))} | "
                    f"{_cell(item.get('why', ''))} |"
                )
            out.append("")

    # --- completeness --------------------------------------------------------
    undeclared, unfound = _completeness(register, scans)
    unattributed = _unattributed_sites(register, scans)
    out.append("## Completeness check")
    out.append("")
    out.append(
        "An independent sweep, blind to the table above, looked for AI this register misses."
    )
    out.append("")
    if undeclared:
        out.append(f"**{len(undeclared)} process(es) in the code are not in this register:** "
                   + ", ".join(f"`{n}`" for n in undeclared) + ". That is a register defect.")
    else:
        out.append("**No undeclared AI process found.** Every name the code uses is in the "
                   "table or the out-of-scope list.")
    out.append("")
    if unattributed:
        out.append(f"{len(unattributed)} file(s) contain a model call no row claims:")
        out.append("")
        for path in unattributed:
            out.append(f"- `{path}`")
        out.append("")
    if unfound:
        out.append(
            f"{len(unfound)} row(s) could not be located from the code alone "
            f"({', '.join(f'`{n}`' for n in unfound)}) — usually a naming mismatch. "
            "Set `covers` or `files` on the row."
        )
        out.append("")

    out.append("## What this can't see")
    out.append("")
    for limit in _aggregate_limits(scans):
        out.append(f"- {limit}")
    out.append("")

    out.append("---")
    out.append("")
    digests = sorted({f"{p['id']}@{p['digest']}" for r in scans.values() for p in r.provenance})
    out.append(
        f"_Generated {generated} by [heldby](https://github.com/receipting/heldby). "
        f"Not taint analysis, not a certification, not legal advice. "
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
                "tier": p.tier,
                "contradicts_tier": p.contradicts_tier,
                "model": p.model,
                "component": p.repo,
                "does": p.does,
                "reaches": p.reaches,
                "covers": p.covers,
                "files": p.files,
                "held_by": p.held_by,
                "held_by_short": p.cell,
                "has_control": p.has_control,
                "source": p.source,
            }
            for p in register.sorted_processes()
        ],
        "key_findings": register.key_findings,
        "counts": {
            "processes": len(register.processes),
            "drafted_unreviewed": len(register.drafted),
            "by_class": {
                key: sum(1 for p in register.processes if p.ai_class == key) for key in CLASS_ORDER
            },
            "by_tier": {
                key: sum(1 for p in register.processes if p.tier == key) for key in TIER_ORDER
            },
            "without_control": sum(1 for p in register.processes if not p.has_control),
            "contradicting_tier": sum(1 for p in register.processes if p.contradicts_tier),
        },
        "protected_actions": register.protected_actions,
        "excluded": register.excluded,
        "completeness": {
            "components_swept": sorted(scans),
            "files_read": sum(r.files_scanned for r in scans.values()),
            "model_sites": sum(len(r.model_sites) for r in scans.values()),
            "protected_action_sites": sum(len(r.action_sites) for r in scans.values()),
            "undeclared_processes": undeclared,
            "unattributed_model_sites": _unattributed_sites(register, scans),
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
