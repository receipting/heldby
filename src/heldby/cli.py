"""heldby's command line. Deterministic commands only — no model call happens here.

The split matters and is the whole architecture: discovery, linting and emission
are deterministic and can gate a build with an exit code. Classification — what
class a call site is, and what actually holds it — needs a model reading the
surrounding code, so it lives in the skill and its result is committed as a
reviewed artefact. CI checks the artefact is current; it does not re-infer it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from . import __version__, catalog as catalog_mod
from .catalog import Catalog, CatalogError
from . import adopt as adopt_mod
from . import lint as lint_mod
from .render import Process, Register, render_json, render_markdown
from .scan import ScanReport
from .scan import scan as scan_repo

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_UNREADABLE = 2


def _load_catalog_at_ref(ref: str, directory: Path) -> Catalog:
    """Load the catalogue as it exists at a git ref, without touching the tree."""
    rel = directory.relative_to(_repo_root(directory))
    listing = subprocess.run(
        ["git", "ls-tree", "--name-only", f"{ref}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
        cwd=_repo_root(directory),
    )
    if listing.returncode != 0:
        raise CatalogError(f"cannot read {rel} at {ref}: {listing.stderr.strip()}")

    names = [n for n in listing.stdout.split() if n.endswith(".yml")]
    if not names:
        raise CatalogError(f"no catalogue files at {ref}:{rel}")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for name in names:
            blob = subprocess.run(
                ["git", "show", f"{ref}:{(rel / name).as_posix()}"],
                capture_output=True,
                text=True,
                cwd=_repo_root(directory),
            )
            if blob.returncode != 0:
                raise CatalogError(f"cannot read {name} at {ref}: {blob.stderr.strip()}")
            (tmpdir / name).write_text(blob.stdout, encoding="utf-8")
        return catalog_mod.load(tmpdir)


def _repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=start if start.is_dir() else start.parent,
    )
    if result.returncode != 0:
        raise CatalogError("not inside a git repository, so there is no ref to compare against")
    return Path(result.stdout.strip())


def _print_catalog(cat: Catalog) -> None:
    """Print the whole detection surface. Nothing is hidden.

    A tool that reports what it found without disclosing what it was looking for
    is asking to be trusted on the strength of its silence.
    """
    for f in cat.files:
        print(f"\n{f.id}  ({f.version}, {len(f.rules)} rules, sha256:{f.digest})")
        print("-" * 78)
        for rule in f.rules:
            flags = []
            if rule.deprecated:
                flags.append("deprecated")
            if rule.action:
                flags.append(f"{rule.action}/{rule.severity}")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {rule.id:<38} {rule.kind}{suffix}")
            for eco, names in sorted(rule.imports.items()):
                print(f"      import({eco}): {', '.join(names)}")
            bits = []
            if rule.match.symbol:
                bits.append(f"symbol={list(rule.match.symbol)}")
            if rule.match.member:
                bits.append(f"member={list(rule.match.member)}")
            if rule.match.ambient:
                bits.append(f"ambient={rule.match.ambient}")
            if rule.match.construct:
                bits.append("construct")
            if rule.match.url_contains:
                bits.append(f"url_contains={list(rule.match.url_contains)}")
            if rule.match.url_literal:
                bits.append(f"url_literal={list(rule.match.url_literal)}")
            if rule.match.env:
                bits.append(f"env={list(rule.match.env)}")
            if bits:
                print(f"      match: {'; '.join(bits)}")

    totals = {}
    for rule in cat.rules:
        totals[rule.kind] = totals.get(rule.kind, 0) + 1
    print(f"\n{len(cat.rules)} rules across {len(cat.files)} files: ", end="")
    print(", ".join(f"{v} {k}" for k, v in sorted(totals.items())))


def cmd_catalog(args: argparse.Namespace) -> int:
    directory = Path(args.dir) if args.dir else catalog_mod.CATALOG_DIR
    try:
        cat = catalog_mod.load(directory)
    except CatalogError as exc:
        print(f"heldby: catalogue will not load — {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    if args.assert_additive:
        try:
            base = _load_catalog_at_ref(args.assert_additive, directory)
        except CatalogError as exc:
            print(f"heldby: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE

        added, removed, altered = catalog_mod.diff_additive(base, cat)
        for rule_id in added:
            print(f"  + {rule_id}")
        for rule_id in removed:
            print(f"  - {rule_id}  REMOVED")
        for rule_id in altered:
            print(f"  ~ {rule_id}  ALTERED")

        if removed or altered:
            print(
                f"\nheldby: not additive — {len(removed)} removed, {len(altered)} altered.\n"
                "The catalogue only grows: a rule is never deleted and its match is never\n"
                "narrowed, because repos on the old SDK still exist and a deleted rule is a\n"
                "silent blind spot. Mark a superseded package `deprecated: true` and add the\n"
                "new name beside it. This change needs a person.",
                file=sys.stderr,
            )
            return EXIT_FINDING
        print(f"\nheldby: additive — {len(added)} rule(s) added, none removed or altered.")
        return EXIT_OK

    if args.check_registries:
        results = catalog_mod.resolve(cat, timeout=args.timeout)
        problems = [r for r in results if r.is_problem]
        stale = [r for r in results if r.status == "deprecated"]

        if args.json:
            print(
                json.dumps(
                    {
                        "schema": "heldby.registry-check.v0",
                        "checked": len(results),
                        "results": [
                            {
                                "registry": r.registry,
                                "name": r.name,
                                "status": r.status,
                                "detail": r.detail,
                                "rules": list(r.rule_ids),
                            }
                            for r in results
                        ],
                    },
                    indent=2,
                )
            )
        else:
            for r in results:
                if r.status == "ok":
                    continue
                mark = "MISSING " if r.status == "missing" else "DEPRECATED"
                print(f"  {mark} {r.registry}:{r.name} — {r.detail}")
                print(f"            would stop detecting: {', '.join(r.rule_ids)}")
            ok = len(results) - len(problems) - len(stale)
            print(
                f"\nheldby: checked {len(results)} package names — "
                f"{ok} ok, {len(stale)} deprecated, {len(problems)} unresolvable."
            )
            if stale:
                print(
                    "Deprecated is not a failure: a superseded package stays catalogued so\n"
                    "repos still on it keep being detected. Add the new name beside it."
                )
        return EXIT_FINDING if problems else EXIT_OK

    if args.json:
        print(
            json.dumps(
                {
                    "schema": "heldby.catalog.v0",
                    "provenance": cat.provenance(),
                    "rules": [
                        {
                            "id": r.id,
                            "kind": r.kind,
                            "deprecated": r.deprecated,
                            "imports": {k: list(v) for k, v in r.imports.items()},
                            "registry": {k: list(v) for k, v in r.registry.items()},
                            "action": r.action,
                            "severity": r.severity,
                            "note": r.note,
                        }
                        for r in cat.rules
                    ],
                },
                indent=2,
            )
        )
    else:
        _print_catalog(cat)
    return EXIT_OK


def _render_scan(report: ScanReport) -> None:
    """The human view. Ordered so the load-bearing findings come first.

    Gateway bypasses lead, because a call the company believes is metered and
    logged but is not is the most actionable thing here. Then the model sites.
    Then the protected actions present in the repo AT ALL — separately from
    whether a model reaches them, because "this codebase cannot move money" and
    "the model is gated away from money" are very different claims and only one
    of them is usually true.
    """
    print(f"\nheldby scan — {report.target}")
    print(
        f"{report.files_scanned} files read "
        f"({', '.join(f'{v} {k}' for k, v in sorted(report.languages.items()))})"
    )

    if report.gateways:
        print(f"\nGATEWAY  {', '.join(report.gateways)}")
        if report.bypass_candidates:
            print(
                f"\n{len(report.bypass_candidates)} BYPASS CANDIDATE(S) — a model reached outside "
                "the gateway.\nThe gateway is not a control, but a call it never sees is unmetered, "
                "unlogged\nand invisible to any spend or usage claim built on it."
            )
            for site in report.bypass_candidates:
                print(f"  {site.file}:{site.line}  [{site.rule_id}]  {site.evidence[:90]}")
        elif any(s.confidence == "confirmed" for s in report.sites if s.rule_id in report.gateways):
            print("  No direct provider call found outside it.")
        else:
            print(
                "  Present in config, but no confirmed call routes through it — so no bypass\n"
                "  claim is made either way. Reads as one provider option, not a mandated path."
            )

    models = report.model_sites
    print(f"\nMODEL SITES — {len(models)}")
    by_file: dict[str, list] = {}
    for site in models:
        by_file.setdefault(site.file, []).append(site)
    for path in sorted(by_file):
        print(f"\n  {path}")
        for site in sorted(by_file[path], key=lambda s: s.line):
            mark = " " if site.confidence == "confirmed" else "?"
            extra = []
            if site.model:
                extra.append(f"model={site.model}")
            if site.label:
                extra.append(f"label={site.label}")
            tail = f"   {' '.join(extra)}" if extra else ""
            print(f"   {mark} :{site.line:<5} {site.rule_id:<32} via {site.why}{tail}")

    actions = report.action_sites
    if actions:
        grouped: dict[str, list] = {}
        for site in actions:
            grouped.setdefault(site.action or "?", []).append(site)
        print(f"\nPROTECTED ACTIONS PRESENT — {len(actions)} site(s)")
        print("  What this codebase can do. NOT a claim that a model reaches any of it.")
        for action in sorted(grouped, key=lambda a: -len(grouped[a])):
            files = sorted({s.file for s in grouped[action]})
            shown = ", ".join(files[:3]) + (f" +{len(files) - 3} more" if len(files) > 3 else "")
            print(f"    {action:<26} {len(grouped[action]):>3} site(s)  {shown}")

    if report.labels:
        names = sorted({n for v in report.labels.values() for n in v})
        print(f"\nSELF-LABELLED PROCESSES — {len(names)}")
        print("  What the code calls its own AI features, from its observability metadata.")
        print("  These, not file paths, are the register's rows.")
        for name in names:
            print(f"    {name}")

    loose = [d for d in report.dependencies if not d["imported_anywhere"]]
    if loose:
        print(f"\nDEPENDENCIES WITH NO IMPORT — {len(loose)}")
        print("  Either dead weight or a use this sweep could not see. Both are findings.")
        for dep in loose:
            print(f"    {dep['registry']}:{dep['package']:<38} ({', '.join(dep['declared_in'])})")

    if report.declarations:
        print(f"\nALREADY DECLARED — {len(report.declarations)} file(s)")
        for path in report.declarations:
            print(f"    {path}")

    print("\nWHAT THIS DID NOT SEE")
    for limit in report.limits:
        for i, chunk in enumerate(textwrap.wrap(limit, 74)):
            print(f"  {'-' if i == 0 else ' '} {chunk}")

    confirmed = sum(1 for s in models if s.confidence == "confirmed")
    print(
        f"\n{len(models)} model site(s): {confirmed} confirmed, {len(models) - confirmed} inferred. "
        f"{len(actions)} protected-action site(s)."
    )
    print("Confidence is not accuracy. `inferred` means the shape is there and the "
          "value flow\nwas not proven — read the code before believing either label.\n")


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"heldby: {root} is not a directory", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        cat = catalog_mod.load(Path(args.catalog) if args.catalog else catalog_mod.CATALOG_DIR)
    except CatalogError as exc:
        print(f"heldby: catalogue will not load — {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    report = scan_repo(
        root,
        cat,
        ignore_declarations=args.ignore_declarations,
        include_tests=args.include_tests,
    )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        _render_scan(report)

    if args.fail_on_finding and report.model_sites:
        return EXIT_FINDING
    return EXIT_OK


def cmd_register(args: argparse.Namespace) -> int:
    """Render the register from a reviewed classification plus a fresh sweep.

    The classification file is the source of truth for `class` and `held_by`, and
    this command will not invent either. What it adds is the sweep: an independent
    read of the source that knows nothing about the declarations, so the register
    can carry a completeness claim rather than only an inventory.
    """
    import yaml

    spec_path = Path(args.classification)
    if not spec_path.is_file():
        print(f"heldby: no classification file at {spec_path}", file=sys.stderr)
        return EXIT_UNREADABLE
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}

    missing = [k for k in ("organisation", "components", "processes") if k not in spec]
    if missing:
        print(f"heldby: classification is missing {', '.join(missing)}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        cat = catalog_mod.load()
    except CatalogError as exc:
        print(f"heldby: catalogue will not load — {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    processes = []
    for raw in spec["processes"]:
        processes.append(
            Process(
                name=raw["name"],
                ai_class=raw["class"],
                model=raw.get("model", "unknown"),
                repo=raw.get("component", "?"),
                does=raw.get("does", ""),
                held_by=raw.get("held_by", ""),
                reaches=list(raw.get("reaches") or []),
                source=raw.get("source", "declared"),
                covers=list(raw.get("covers") or []),
                files=list(raw.get("files") or []),
            )
        )

    # Read every component before writing anything. A register that quietly omits
    # a component understates the AI in the system to whoever reads it, which is
    # the one failure that actually matters here.
    # Component paths resolve against `repos_root` when given, so a committed
    # classification file is not tied to one machine's directory layout.
    base = Path(spec["repos_root"]).expanduser() if spec.get("repos_root") else spec_path.parent

    scans: dict[str, ScanReport] = {}
    for name, rel in spec["components"].items():
        expanded = Path(rel).expanduser()
        root = expanded if expanded.is_absolute() else (base / expanded).resolve()
        if not root.is_dir():
            print(f"heldby: component {name!r} is not readable at {root}", file=sys.stderr)
            return EXIT_UNREADABLE
        scans[name] = scan_repo(root, cat, ignore_declarations=True)

    register = Register(
        org=spec["organisation"],
        summary=spec.get("summary", ""),
        processes=processes,
        protected_actions=spec.get("protected_actions") or {},
        scope_notes=list(spec.get("scope_notes") or []),
        excluded=list(spec.get("excluded") or []),
        generated=spec.get("generated", ""),
    )

    markdown = render_markdown(register, scans, layout=args.layout)
    payload = render_json(register, scans)

    if args.out_md:
        Path(args.out_md).write_text(markdown, encoding="utf-8")
        print(f"wrote {args.out_md}", file=sys.stderr)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out_json}", file=sys.stderr)
    if not args.out_md and not args.out_json:
        print(json.dumps(payload, indent=2) if args.json else markdown)

    undeclared = payload["completeness"]["undeclared_processes"]
    gaps = payload["counts"]["without_control"]
    if undeclared:
        print(
            f"\nheldby: {len(undeclared)} AI process(es) in the code are not declared: "
            f"{', '.join(undeclared)}",
            file=sys.stderr,
        )
        return EXIT_FINDING
    if gaps and args.fail_on_gap:
        print(f"\nheldby: {gaps} process(es) have nothing holding them.", file=sys.stderr)
        return EXIT_FINDING
    return EXIT_OK



def _repo_config(root: Path) -> dict:
    """Read the target repo's heldby.yml, if it has one."""
    import yaml

    path = root / "heldby.yml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def cmd_lint(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"heldby: {root} is not a directory", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        cat = catalog_mod.load()
    except CatalogError as exc:
        print(f"heldby: catalogue will not load — {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    cfg = _repo_config(root)
    gateways = args.gateway or cfg.get("gateway_modules") or []
    if not gateways:
        print(
            "heldby lint: no gateway module configured, so there is nothing to enforce.\n"
            "Add gateway_modules to heldby.yml (or run `heldby adopt` to seed one), or pass\n"
            "--gateway. Refusing to pass a gate that checks nothing.",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE

    baseline = root / (cfg.get("baseline") or ".heldby-baseline.json")
    result = lint_mod.lint(
        root,
        cat,
        gateway_modules=list(gateways),
        exclude=list(cfg.get("exclude") or []),
        baseline_path=baseline,
        include_tests=args.include_tests,
    )

    if args.update_baseline:
        all_found = result.violations + result.accepted
        lint_mod.write_baseline(baseline, all_found)
        print(
            f"heldby lint: baselined {len(all_found)} pre-existing bypass(es) into "
            f"{baseline.name}.\nThis list may only shrink from here."
        )
        return EXIT_OK

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(lint_mod.render_lint(result))
    return EXIT_OK if result.ok else EXIT_FINDING


def cmd_adopt(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"heldby: {root} is not a directory", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        cat = catalog_mod.load()
    except CatalogError as exc:
        print(f"heldby: catalogue will not load — {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    plan = adopt_mod.plan(root, cat)

    if plan.blocked and not args.force:
        for path in plan.existing:
            print(f"heldby adopt: {path.name} already exists", file=sys.stderr)
        print(
            "\nRefusing to overwrite a declaration someone has already filled in — the prose "
            "in it\nis the part that took the work. Pass --force if you mean to replace it.",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE

    declaration = adopt_mod.render_declaration(plan)
    config = adopt_mod.render_config(plan)

    if args.dry_run:
        print(f"--- {plan.declaration_path.name} ---\n{declaration}")
        print(f"--- {plan.config_path.name} ---\n{config}")
        return EXIT_OK

    plan.declaration_path.write_text(declaration, encoding="utf-8")
    plan.config_path.write_text(config, encoding="utf-8")

    result = lint_mod.lint(
        root, cat, gateway_modules=plan.gateway_modules, baseline_path=None
    )
    lint_mod.write_baseline(plan.baseline_path, result.violations)

    print(f"heldby adopt: wrote {plan.declaration_path.name}, {plan.config_path.name} "
          f"and {plan.baseline_path.name}")
    print(f"  {len(plan.processes)} process(es) stubbed: {', '.join(plan.processes) or '(none found)'}")
    print(f"  {len(plan.gateway_modules)} gateway module(s) seeded from where calls are made today")
    print(f"  {len(result.violations)} pre-existing bypass(es) baselined, so the gate is green now")
    print(
        "\nNext, and none of it is optional:\n"
        "  1. Fill in `does` and `heldBy` for each process. `heldBy` is the whole point —\n"
        "     name the specific control, and leave it EMPTY if there genuinely isn't one.\n"
        "     The register will print `nothing`, which is the most useful row it can carry.\n"
        "  2. Check every `class`. They are all stubbed as `read`, which is wrong for any\n"
        "     process that can reach a protected action.\n"
        "  3. Route each model call through the gateway module, passing the typed feature,\n"
        "     then delete its entry from the baseline.\n"
        "  4. Add `heldby lint` to CI."
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="heldby",
        description="Find every place AI runs in a codebase, and name what holds it.",
    )
    parser.add_argument("--version", action="version", version=f"heldby {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cat = sub.add_parser("catalog", help="print or check the detection surface")
    cat.add_argument("--dir", help="catalogue directory (defaults to the packaged one)")
    cat.add_argument("--json", action="store_true", help="machine-readable output")
    cat.add_argument(
        "--check-registries",
        action="store_true",
        help="resolve every catalogued package name against npm and PyPI",
    )
    cat.add_argument(
        "--assert-additive",
        metavar="REF",
        help="fail if the catalogue removes or alters any rule present at REF",
    )
    cat.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    cat.set_defaults(func=cmd_catalog)

    sc = sub.add_parser("scan", help="find every place a model might run in a repo")
    sc.add_argument("path", nargs="?", default=".", help="repo to scan (default: .)")
    sc.add_argument("--json", action="store_true", help="machine-readable output")
    sc.add_argument("--catalog", help="catalogue directory (defaults to the packaged one)")
    sc.add_argument(
        "--ignore-declarations",
        action="store_true",
        help="do not read existing AI declarations — for an honest inference-only run",
    )
    sc.add_argument(
        "--include-tests",
        action="store_true",
        help="also scan test files (excluded by default; the count is always reported)",
    )
    sc.add_argument(
        "--fail-on-finding",
        action="store_true",
        help="exit 1 if any model site is found (for gating a build)",
    )
    sc.set_defaults(func=cmd_scan)

    reg = sub.add_parser("register", help="render the register a customer or auditor reads")
    reg.add_argument(
        "--classification",
        required=True,
        metavar="FILE",
        help="reviewed class and held-by content, plus the components to sweep",
    )
    reg.add_argument("--out-md", metavar="FILE", help="write the human register here")
    reg.add_argument("--out-json", metavar="FILE", help="write the machine register here")
    reg.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    reg.add_argument(
        "--layout",
        choices=("table", "sections"),
        default="table",
        help="table for a screen; sections for print, where a 6-column table collapses",
    )
    reg.add_argument(
        "--fail-on-gap",
        action="store_true",
        help="exit 1 if any process has nothing holding it",
    )
    reg.set_defaults(func=cmd_register)

    ln = sub.add_parser("lint", help="fail the build on a model call outside the gateway module")
    ln.add_argument("path", nargs="?", default=".", help="repo to check (default: .)")
    ln.add_argument("--gateway", action="append", metavar="PATH",
                    help="a module permitted to construct a client (repeatable)")
    ln.add_argument("--update-baseline", action="store_true",
                    help="accept every current bypass; the list may only shrink after this")
    ln.add_argument("--include-tests", action="store_true", help="also check test files")
    ln.add_argument("--json", action="store_true", help="machine-readable output")
    ln.set_defaults(func=cmd_lint)

    ad = sub.add_parser("adopt", help="write declarations and a gate into the repo")
    ad.add_argument("path", nargs="?", default=".", help="repo to adopt (default: .)")
    ad.add_argument("--dry-run", action="store_true", help="print what would be written")
    ad.add_argument("--force", action="store_true", help="overwrite an existing declaration")
    ad.set_defaults(func=cmd_adopt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
