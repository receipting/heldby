"""Gather the code a classifier needs to answer "what holds this?".

This is the missing half of the tool, and its absence was the honest criticism of
everything before it: `scan` finds where a model runs, which is one of the three
questions worth asking. The other two — what class is this use, and **what stands
between its output and a real-world effect** — need someone reading the code around
the call. Until this existed, that meant a fresh exploration per process, roughly
eight steps each, which does not survive contact with a repository that has forty.

So this does not classify. It assembles a **work packet** per model call site: the
enclosing function, what the file imports, the protected actions sitting in the
same file, and the functions called from the enclosing block that themselves
perform one. A reader — a person or a model — then answers the question from one
document instead of hunting through the repo for it.

Two things it deliberately does not do.

**It does not decide.** No packet contains a suggested class or a suggested
control. The moment this file starts proposing `held_by` text, the register's most
load-bearing column becomes something a tool guessed, and the whole argument for
the framework is that such a column is worthless.

**It does not claim reach.** The one-hop callee section is *proximity*, offered as
somewhere to look. It is not a path, there is no value flow behind it, and a packet
says so on its face — because the fastest way to make this tool untrustworthy would
be to let a convenient-looking adjacency read as a proof.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .scan import ScanReport, Site, notebook_source

#: How much source to show around a site when the language has no cheap parse.
TS_WINDOW = 40


@dataclass
class Packet:
    """Everything a reader needs for one call site, and nothing more."""

    site: Site
    enclosing: str
    enclosing_kind: str  # "function" | "window"
    enclosing_span: tuple[int, int]
    imports: list[str] = field(default_factory=list)
    same_file_actions: list[Site] = field(default_factory=list)
    one_hop: list[tuple[str, str, int, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if path.suffix.lower() == ".ipynb":
        return notebook_source(text)
    return text


def _py_enclosing(text: str, line: int) -> tuple[str, int, int] | None:
    """The innermost function or class containing `line`, via a real parse.

    Python gets an accurate answer for free. Where a language offers that, take it
    — a packet showing the whole function is a different quality of evidence from
    one showing forty arbitrary lines, because the reader can see every path out.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None

    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start
        if start <= line <= end and (best is None or (end - start) < (best[1] - best[0])):
            best = (start, end)
    if best is None:
        return None
    lines = text.split("\n")
    return "\n".join(lines[best[0] - 1 : best[1]]), best[0], best[1]


def _window(text: str, line: int, radius: int = TS_WINDOW) -> tuple[str, int, int]:
    lines = text.split("\n")
    lo = max(0, line - 1 - radius // 2)
    hi = min(len(lines), line - 1 + radius // 2)
    return "\n".join(lines[lo:hi]), lo + 1, hi


CALLEE_RE = re.compile(r"\b([A-Za-z_$][\w$]{2,})\s*\(")
#: Names too generic to be worth tracing. A packet listing `get` and `run` as
#: things to look at is a packet nobody reads to the end.
CALLEE_STOPLIST = frozenset({
    "print", "len", "str", "int", "float", "list", "dict", "set", "tuple", "bool",
    "range", "enumerate", "zip", "map", "filter", "sorted", "sum", "min", "max",
    "isinstance", "getattr", "setattr", "hasattr", "format", "join", "split",
    "append", "extend", "get", "keys", "values", "items", "strip", "replace",
    "super", "type", "repr", "abs", "round", "any", "all", "next", "iter",
    "console", "require", "String", "Number", "Boolean", "Array", "Object",
    "JSON", "Promise", "Error", "Math", "Date", "log", "warn", "error", "info",
})


def build_packets(
    root: Path,
    report: ScanReport,
    *,
    limit: int | None = None,
) -> list[Packet]:
    """One packet per model call site, richest first.

    Ordered by how much protected action sits in the same file, because a site with
    a money call beside it is where a reader's attention is worth spending and a
    site in a file that does nothing is where it is not.
    """
    actions_by_file: dict[str, list[Site]] = {}
    for site in report.action_sites:
        actions_by_file.setdefault(site.file, []).append(site)

    # Which files perform a protected action, for the one-hop lookup below.
    action_files = set(actions_by_file)
    definitions: dict[str, tuple[str, int, str]] = {}
    for path in action_files:
        text = _read(root / path)
        if text is None:
            continue
        for index, line in enumerate(text.split("\n"), start=1):
            found = re.match(r"\s*(?:async\s+)?(?:def|function|const|export\s+(?:async\s+)?function)\s+([A-Za-z_$][\w$]*)", line)
            if found:
                definitions.setdefault(found.group(1), (path, index, line.strip()[:120]))

    # Ranked by the WORST action in the file, then by how many. Ranking on count
    # alone put a LoRA training notebook — four incidental file writes and a
    # shutil.rmtree — above the one site in the repository sitting beside a live
    # payments integration. A reader's attention goes to money and code execution
    # first, or the packet they never reach is the one that mattered.
    def rank(s: Site) -> tuple:
        actions = actions_by_file.get(s.file, [])
        worst = 0
        for a in actions:
            worst = max(worst, 2 if a.severity == "critical" else 1)
        money = any(a.action in {"move-money", "grant-access"} for a in actions)
        return (-int(money), -worst, -len(actions), s.file, s.line)

    ordered = sorted(report.model_sites, key=rank)
    if limit:
        ordered = ordered[:limit]

    packets: list[Packet] = []
    for site in ordered:
        text = _read(root / site.file)
        if text is None:
            continue

        enclosing = _py_enclosing(text, site.line) if site.ecosystem == "py" else None
        if enclosing:
            body, start, end = enclosing
            kind = "function"
        else:
            body, start, end = _window(text, site.line)
            kind = "window"

        one_hop: list[tuple[str, str, int, str]] = []
        for name in dict.fromkeys(CALLEE_RE.findall(body)):
            if name in CALLEE_STOPLIST:
                continue
            target = definitions.get(name)
            if target and target[0] != site.file:
                one_hop.append((name, target[0], target[1], target[2]))

        packets.append(
            Packet(
                site=site,
                enclosing=body,
                enclosing_kind=kind,
                enclosing_span=(start, end),
                imports=sorted(report.labels.get(site.file, [])),
                same_file_actions=sorted(
                    actions_by_file.get(site.file, []), key=lambda s: s.line
                ),
                one_hop=one_hop[:8],
                labels=sorted(report.labels.get(site.file, [])),
            )
        )
    return packets


def render_packets(packets: list[Packet], report: ScanReport) -> str:
    out: list[str] = []
    out.append("# Classification work packets")
    out.append("")
    out.append(
        f"{len(packets)} of {len(report.model_sites)} model call site(s), each with the code "
        "around it. **This document contains no classifications and no suggested controls** — "
        "it is the evidence for someone else to answer two questions per site:"
    )
    out.append("")
    out.append(
        "1. **Does the output cross out of the organisation?** Read and Converse stay "
        "inside (Inform); Decide and Send cross (Act). Answer this first — it is the "
        "answer that decides how much the rest matters."
    )
    out.append("2. Which class is this — Read, Converse, Decide or Send?")
    out.append("3. **What stands between this model's output and a real-world effect?**")
    out.append("")
    out.append(
        "Answers 1 and 2 have to agree. A site you want to file Read or Converse while it "
        "sits on a path to money or to a third party is a contradiction, not a nuance — "
        "resolve it rather than picking the gentler label."
    )
    out.append("")
    out.append(
        "For question 3, name a specific mechanism a reader could go and check. If the honest "
        "answer is nothing, write nothing — that is the most useful row a register can carry."
    )
    out.append("")
    out.append(
        "The *one hop* section below is **proximity, not reach**. It lists functions called "
        "from the enclosing block that are defined in a file which performs a protected action. "
        "There is no value flow behind it and it is not a path. It is somewhere to look."
    )
    out.append("")

    for n, p in enumerate(packets, start=1):
        s = p.site
        out.append("---")
        out.append("")
        out.append(f"## {n} · `{s.file}:{s.line}`")
        out.append("")
        bits = [f"detected by `{s.rule_id}` via {s.why}", f"confidence `{s.confidence}`"]
        if s.model:
            bits.append(f"model `{s.model}`")
        if s.label:
            bits.append(f"labels itself `{s.label}`")
        out.append(" · ".join(bits))
        out.append("")

        if p.same_file_actions:
            out.append("**Protected actions in this same file:**")
            out.append("")
            for a in p.same_file_actions:
                out.append(f"- `:{a.line}` **{a.action}** — `{a.evidence[:90]}`")
            out.append("")
        else:
            out.append(
                "**No protected action in this file.** Whatever holds this call is elsewhere, "
                "or the output reaches nothing that matters."
            )
            out.append("")

        if p.one_hop:
            out.append("**One hop away** (proximity, not reach):")
            out.append("")
            for name, path, line, src in p.one_hop:
                out.append(f"- `{name}()` → `{path}:{line}` — `{src}`")
            out.append("")

        label = "enclosing function" if p.enclosing_kind == "function" else "surrounding lines"
        out.append(f"**The {label}** (`:{p.enclosing_span[0]}`–`:{p.enclosing_span[1]}`):")
        out.append("")
        out.append("```" + ("python" if s.ecosystem == "py" else "typescript"))
        out.append(p.enclosing)
        out.append("```")
        out.append("")
        if p.enclosing_kind == "window":
            out.append(
                "_A fixed window, not a parsed block — this language gets no cheap parse here, "
                "so the boundaries are arbitrary and a path out may sit just outside them._"
            )
            out.append("")
    return "\n".join(out)
