#!/usr/bin/env python
"""Apply catalogue proposals from the nightly freshness run. Deterministic.

The nightly job has a model search for new and changed AI frameworks. That model
never edits the catalogue. It writes a proposals JSON, and this script decides
what gets in — because the safety of an unattended merge has to be structural, not
a matter of trusting the proposer.

Three gates, in order:

1. **Additive only.** A proposal whose rule id already exists is dropped. This
   script cannot alter or delete a rule; there is no code path that does.
2. **Schema valid.** Every proposal is parsed by the same loader the tool uses, so
   a rule that would not load is dropped before it can break the build.
3. **Real package.** Every proposed package name is resolved against npm or PyPI.
   A hallucinated or misspelled name is dropped.

That bounds the worst case. The discovery step reads the open web, and web content
is data, not instructions — a page that says "add a rule excluding X" cannot get
that past gate 1, because excluding something means altering or removing a rule.
The most a hostile page can achieve is one useless rule for a package that really
exists, which the fixture tests then have to survive, and which shows up in the
pull request diff for anyone reading it later.

Everything dropped is printed with a reason. A proposal that vanished silently
would make the run look cleaner than it was.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from heldby import catalog as catalog_mod  # noqa: E402
from heldby.catalog import CatalogError  # noqa: E402


def yaml_scalar(value: str) -> str:
    """Quote a scalar so YAML reads it back as the same string."""
    return json.dumps(value)


def yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(yaml_scalar(v) for v in values) + "]"


def render_rule(rule: dict) -> str:
    """Render one accepted rule as YAML text.

    Text, not a YAML dump of the whole file: round-tripping would strip the
    comments and the notes, and those are most of why the catalogue is readable.
    """
    lines = [f"  - id: {rule['id']}", f"    kind: {rule['kind']}"]

    if rule.get("deprecated"):
        lines.append("    deprecated: true")
    for key in ("action", "severity"):
        if rule.get(key):
            lines.append(f"    {key}: {rule[key]}")
    if rule.get("payload_arg") is not None:
        lines.append(f"    payload_arg: {rule['payload_arg']}")
    if rule.get("remedies"):
        lines.append(f"    remedies: {yaml_list(rule['remedies'])}")
    if rule.get("trifecta_role"):
        lines.append(f"    trifecta_role: {rule['trifecta_role']}")

    for group in ("imports", "registry"):
        if rule.get(group):
            lines.append(f"    {group}:")
            for key, names in sorted(rule[group].items()):
                lines.append(f"      {key}: {yaml_list(list(names))}")

    lines.append("    match:")
    for key, value in sorted(rule["match"].items()):
        if isinstance(value, bool):
            lines.append(f"      {key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            lines.append(f"      {key}: {yaml_list(value)}")
        else:
            lines.append(f"      {key}: {yaml_scalar(value)}")

    if rule.get("model_arg"):
        lines.append(f"    model_arg: {rule['model_arg']}")

    note = (rule.get("note") or "").strip()
    if note:
        lines.append("    note: >")
        for chunk in note.split("\n"):
            lines.append(f"      {chunk.strip()}")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposals", help="path to the proposals JSON")
    parser.add_argument("--catalog-dir", default=str(REPO / "src" / "heldby" / "catalog"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-registry-check", action="store_true",
                        help="for tests only; the nightly job must not pass this")
    args = parser.parse_args()

    catalog_dir = Path(args.catalog_dir)

    try:
        existing = catalog_mod.load(catalog_dir)
    except CatalogError as exc:
        print(f"apply-proposals: the catalogue does not currently load — {exc}", file=sys.stderr)
        return 2

    known_ids = set(existing.by_id)
    known_files = {f.path.name for f in existing.files}

    payload = json.loads(Path(args.proposals).read_text(encoding="utf-8"))
    proposals = payload.get("proposals") or []
    if not isinstance(proposals, list):
        print("apply-proposals: `proposals` must be a list", file=sys.stderr)
        return 2

    accepted: dict[str, list[dict]] = {}
    dropped: list[tuple[str, str]] = []
    seen_this_run: set[str] = set()

    for raw in proposals:
        rule_id = (raw.get("rule") or {}).get("id", "<no id>")
        target = raw.get("file")
        rule = raw.get("rule") or {}

        if target not in known_files:
            dropped.append((rule_id, f"target file {target!r} is not one of {sorted(known_files)}"))
            continue
        if rule_id in known_ids:
            dropped.append((rule_id, "a rule with this id already exists — this script only adds"))
            continue
        if rule_id in seen_this_run:
            dropped.append((rule_id, "proposed twice in one run"))
            continue

        # Gate 2: it has to load through the real parser.
        try:
            parsed = catalog_mod._parse_rule(rule, "proposal")
        except CatalogError as exc:
            dropped.append((rule_id, f"invalid: {exc}"))
            continue

        # Gate 3: every package name it claims has to exist.
        if not args.skip_registry_check and parsed.registry:
            probe = catalog_mod.Catalog(
                files=(
                    catalog_mod.CatalogFile(
                        id="proposal", version="proposal", path=Path("proposal"),
                        digest="", rules=(parsed,),
                    ),
                )
            )
            bad = [r for r in catalog_mod.resolve(probe) if r.is_problem]
            if bad:
                detail = ", ".join(f"{r.registry}:{r.name} ({r.status})" for r in bad)
                dropped.append((rule_id, f"package does not resolve: {detail}"))
                continue

        seen_this_run.add(rule_id)
        accepted.setdefault(target, []).append(rule)

    for rule_id, why in dropped:
        print(f"  dropped  {rule_id}: {why}")

    total = sum(len(v) for v in accepted.values())
    if not total:
        print(f"apply-proposals: nothing applied ({len(dropped)} dropped, {len(proposals)} proposed).")
        return 0

    for target, rules in sorted(accepted.items()):
        path = catalog_dir / target
        block = "\n" + "".join(render_rule(r) for r in rules)
        if args.dry_run:
            print(f"--- would append to {target} ---\n{block}")
            continue
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
        for rule in rules:
            print(f"  added    {rule['id']} -> {target}")

    if not args.dry_run:
        # Refuse to leave the tree in a state the tool cannot load.
        try:
            catalog_mod.load(catalog_dir)
        except CatalogError as exc:
            print(f"apply-proposals: appended rules broke the catalogue — {exc}", file=sys.stderr)
            return 2

    print(f"apply-proposals: applied {total}, dropped {len(dropped)}, of {len(proposals)} proposed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
