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
from pathlib import Path

from . import __version__, catalog as catalog_mod
from .catalog import Catalog, CatalogError

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
        print(f"\nheldby: additive — {len(added)} rules added, none removed or altered.")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
