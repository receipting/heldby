"""The gate: a forbid-rule, not an analysis.

"Nothing in this repo reaches a model except through the module we designated" is
a **negative** claim, and a reach analyser can only ever answer "no path found" —
which is not the same sentence. A forbid-rule fails on the exact line instead. The
one bypass that actually shipped in the estate this came from was a script calling
a provider directly, outside the gateway everything was supposed to route through;
it survived months of a call-graph analyser and would have been refused on the day
it was written by a rule like this.

Two things make it survivable in a real codebase.

**It ratchets.** A gate that fails on forty pre-existing call sites the day it
lands gets deleted the same day. `heldby lint --update-baseline` records what
exists now; the baseline can only shrink, and a violation not in it fails the
build. So the rule goes green immediately and can only tighten.

**Its scope is visible.** Every exclusion appears in the output, including on
success. A gate that quietly covers less than it claims is worse than no gate: it
reports "ok" over the thing it cannot see. The predecessor of this rule shipped a
version that exited 0 having checked nothing at all, and it was live in five
repositories' CI before anyone noticed.

The patterns are not defined here. They come from the same catalogue the scan
uses, so the gate and the inventory can never disagree about what a model call
looks like, and the nightly catalogue refresh improves both at once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog
from .scan import Site, scan

#: An env var *name* is not a call. It belongs in the inventory as a loose end,
#: never in a gate: failing a build because a `.env.example` mentions a key would
#: teach people to delete the gate rather than fix anything.
NON_VIOLATION_REASONS = {"env-var"}


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    rule_id: str
    why: str
    confidence: str
    evidence: str

    @property
    def key(self) -> str:
        """Baseline identity. Deliberately excludes the line number.

        Keying on the line would make every violation reappear the moment
        somebody adds an import above it, and a baseline that churns is a
        baseline people regenerate blindly.
        """
        return f"{self.file}::{self.rule_id}"

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "rule": self.rule_id,
            "why": self.why,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class LintResult:
    violations: list[Violation]
    accepted: list[Violation]
    stale_baseline: list[str]
    gateway_modules: list[str]
    excluded: list[str]
    files_scanned: int

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict:
        return {
            "schema": "heldby.lint.v0",
            "ok": self.ok,
            "gateway_modules": self.gateway_modules,
            "excluded": self.excluded,
            "files_scanned": self.files_scanned,
            "violations": [v.as_dict() for v in self.violations],
            "accepted_by_baseline": [v.as_dict() for v in self.accepted],
            "stale_baseline_entries": self.stale_baseline,
        }


def _load_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return set()
    return set(data.get("accepted") or [])


def write_baseline(path: Path, violations: list[Violation]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "heldby.baseline.v0",
                "note": (
                    "Model calls outside the designated gateway module(s) that existed when "
                    "this gate was adopted. This list may only SHRINK: heldby lint fails on "
                    "anything not in it. Delete an entry once the call is routed properly."
                ),
                "accepted": sorted({v.key for v in violations}),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def lint(
    root: Path,
    catalog: Catalog,
    *,
    gateway_modules: list[str],
    exclude: list[str] | None = None,
    baseline_path: Path | None = None,
    include_tests: bool = False,
) -> LintResult:
    """Find model calls outside the designated gateway module(s).

    `gateway_modules` is a list, not a string. One central module is the shape a
    single team converges on; an arbitrary codebase has an SDK wrapper per team, a
    legacy path and a test double, and a gate that permits exactly one file is a
    gate that gets turned off.
    """
    exclude = exclude or []
    report = scan(root, catalog, ignore_declarations=True, include_tests=include_tests)

    gateways = {g.rstrip("/") for g in gateway_modules}

    def is_gateway(path: str) -> bool:
        return any(path == g or path.startswith(f"{g}/") for g in gateways)

    def is_excluded(path: str) -> bool:
        return any(path == e or path.startswith(e.rstrip("/") + "/") for e in exclude)

    found: dict[str, Violation] = {}
    for site in report.sites:
        if site.kind != "model" or site.why in NON_VIOLATION_REASONS:
            continue
        if is_gateway(site.file) or is_excluded(site.file):
            continue
        violation = Violation(
            file=site.file,
            line=site.line,
            rule_id=site.rule_id,
            why=site.why,
            confidence=site.confidence,
            evidence=site.evidence,
        )
        # One entry per (file, rule); the first line found is the one reported.
        found.setdefault(violation.key, violation)

    accepted_keys = _load_baseline(baseline_path) if baseline_path else set()

    violations = [v for key, v in sorted(found.items()) if key not in accepted_keys]
    accepted = [v for key, v in sorted(found.items()) if key in accepted_keys]
    # A baseline entry whose violation is gone is reported so the file can be
    # trimmed. Left alone it silently re-permits the same bypass if it comes back.
    stale = sorted(accepted_keys - set(found))

    return LintResult(
        violations=violations,
        accepted=accepted,
        stale_baseline=stale,
        gateway_modules=sorted(gateways),
        excluded=sorted(exclude),
        files_scanned=report.files_scanned,
    )


def render_lint(result: LintResult, *, all_sites: bool = False) -> str:
    out: list[str] = []

    if result.violations:
        out.append(
            f"heldby lint: {len(result.violations)} model call(s) reach a provider outside "
            f"{', '.join(result.gateway_modules)}\n"
        )
        for v in result.violations:
            mark = " " if v.confidence == "confirmed" else "?"
            out.append(f" {mark} {v.file}:{v.line}  [{v.rule_id}] via {v.why}")
            out.append(f"      {v.evidence[:100]}")
        out.append(
            "\nEvery model call should route through the designated module, so that spend, the "
            "\nregister and anything published from it all agree. Declare the new call and use "
            "\nthe repo's gateway helper — or accept it deliberately with --update-baseline."
        )
    else:
        scope = f"{result.files_scanned} files"
        note = ""
        if result.excluded:
            note = (
                f" — {len(result.excluded)} path(s) excluded and NOT covered by this claim: "
                f"{', '.join(result.excluded)}"
            )
        out.append(
            f"heldby lint: ok ({scope} — every model call is made in "
            f"{', '.join(result.gateway_modules)}){note}"
        )

    if result.accepted:
        out.append(
            f"\n{len(result.accepted)} pre-existing bypass(es) accepted by the baseline. "
            "These are known debt, not compliance:"
        )
        for v in result.accepted:
            out.append(f"    {v.file}  [{v.rule_id}]")

    if result.stale_baseline:
        out.append(
            f"\n{len(result.stale_baseline)} baseline entry(ies) no longer match anything and "
            "should be deleted, or they will silently re-permit the same bypass:"
        )
        for key in result.stale_baseline:
            out.append(f"    {key}")

    if all_sites:
        out.append("\nDetection surface used by this gate is the shared catalogue; "
                   "run `heldby catalog` to print it in full.")
    return "\n".join(out)
