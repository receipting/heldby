"""Load, validate and digest the catalogue; resolve it against package indexes.

Three jobs, and the third is the one that keeps this tool honest.

Loading validates every rule against the closed vocabularies in `schema.py` and
refuses the whole catalogue on any error — a partly-loaded detection surface
would silently narrow what the tool can see, and the report would still say it
scanned everything.

Digesting gives each catalogue file a sha256 that goes into the report's
provenance, so a register can name the exact detection surface it was produced
with.

Resolving checks every catalogued package name against npm and PyPI. Catalogue
freshness is the single point of failure for a tool like this: a package rename
made this catalogue's predecessor report "no model roots detected" over a
106k-star agent that shells out. A rename must fail us loudly, on a schedule,
rather than quietly at the moment someone trusts the output.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    ACTIONS,
    ECOSYSTEMS,
    KINDS,
    REGISTRIES,
    REMEDIES,
    REMEDIES_FOR_ACTION,
    SEVERITIES,
    TRIFECTA_ROLES,
    Match,
    Rule,
)

CATALOG_DIR = Path(__file__).parent / "catalog"


class CatalogError(Exception):
    """A catalogue that will not load. Never downgraded to a warning."""


@dataclass(frozen=True)
class CatalogFile:
    id: str
    version: str
    path: Path
    digest: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Catalog:
    files: tuple[CatalogFile, ...]

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(r for f in self.files for r in f.rules)

    @property
    def by_id(self) -> dict[str, Rule]:
        return {r.id: r for r in self.rules}

    def model_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind == "model")

    def sink_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.action)

    def provenance(self) -> list[dict[str, str]]:
        return [{"id": f.id, "version": f.version, "digest": f.digest} for f in self.files]


def _as_tuple(value: Any, *, where: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise CatalogError(f"{where}: `{field}` must be a string or a list of strings")


def _parse_match(raw: Any, where: str) -> Match:
    if raw is None:
        raise CatalogError(f"{where}: a rule needs a `match`")
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}: `match` must be a mapping")

    known = {"member", "symbol", "ambient", "construct", "url_contains", "url_literal", "env"}
    unknown = set(raw) - known
    if unknown:
        raise CatalogError(f"{where}: unknown match keys {sorted(unknown)}")

    ambient = raw.get("ambient")
    if ambient is not None and not isinstance(ambient, str):
        raise CatalogError(f"{where}: `ambient` must be a string")
    construct = raw.get("construct", False)
    if not isinstance(construct, bool):
        raise CatalogError(f"{where}: `construct` must be true or false")

    match = Match(
        member=_as_tuple(raw.get("member"), where=where, field="member"),
        symbol=_as_tuple(raw.get("symbol"), where=where, field="symbol"),
        ambient=ambient,
        construct=construct,
        url_contains=_as_tuple(raw.get("url_contains"), where=where, field="url_contains"),
        url_literal=_as_tuple(raw.get("url_literal"), where=where, field="url_literal"),
        env=_as_tuple(raw.get("env"), where=where, field="env"),
    )
    if match.is_empty():
        raise CatalogError(f"{where}: `match` sets no field, so the rule can never fire")
    return match


def _parse_names(raw: Any, allowed: frozenset[str], *, where: str, field: str) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}: `{field}` must be a mapping of {sorted(allowed)}")
    bad = set(raw) - allowed
    if bad:
        raise CatalogError(f"{where}: `{field}` has unknown keys {sorted(bad)}; allowed: {sorted(allowed)}")
    return {k: _as_tuple(v, where=where, field=f"{field}.{k}") for k, v in raw.items() if v}


def _parse_rule(raw: Any, where: str) -> Rule:
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}: each rule must be a mapping")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise CatalogError(f"{where}: every rule needs a non-empty `id`")
    where = f"{where} [{rule_id}]"

    known = {
        "id", "kind", "match", "imports", "registry", "deprecated", "note", "model_arg",
        "action", "severity", "payload_arg", "remedies", "trifecta_role",
    }
    unknown = set(raw) - known
    if unknown:
        raise CatalogError(f"{where}: unknown keys {sorted(unknown)}")

    kind = raw.get("kind")
    if kind not in KINDS:
        raise CatalogError(f"{where}: `kind` must be one of {sorted(KINDS)}, got {kind!r}")

    action = raw.get("action")
    if action is not None and action not in ACTIONS:
        raise CatalogError(f"{where}: `action` {action!r} is not in the closed vocabulary {sorted(ACTIONS)}")

    severity = raw.get("severity")
    if action and severity not in SEVERITIES:
        raise CatalogError(f"{where}: a rule with an `action` needs a `severity` in {sorted(SEVERITIES)}")
    if severity is not None and not action:
        raise CatalogError(f"{where}: `severity` without an `action` means nothing")

    remedies = _as_tuple(raw.get("remedies"), where=where, field="remedies")
    bad_remedies = set(remedies) - REMEDIES
    if bad_remedies:
        raise CatalogError(f"{where}: unknown remedies {sorted(bad_remedies)}")
    if remedies and action in REMEDIES_FOR_ACTION:
        misfit = set(remedies) - REMEDIES_FOR_ACTION[action]
        if misfit:
            raise CatalogError(
                f"{where}: remedies {sorted(misfit)} are not meaningful for action {action!r}"
            )

    trifecta_role = raw.get("trifecta_role")
    if trifecta_role is not None and trifecta_role not in TRIFECTA_ROLES:
        raise CatalogError(f"{where}: `trifecta_role` must be one of {sorted(TRIFECTA_ROLES)}")

    payload_arg = raw.get("payload_arg")
    if payload_arg is not None and not isinstance(payload_arg, int):
        raise CatalogError(f"{where}: `payload_arg` must be an integer argument index")

    deprecated = raw.get("deprecated", False)
    if not isinstance(deprecated, bool):
        raise CatalogError(f"{where}: `deprecated` must be true or false")

    return Rule(
        id=rule_id,
        kind=kind,
        match=_parse_match(raw.get("match"), where),
        imports=_parse_names(raw.get("imports"), ECOSYSTEMS, where=where, field="imports"),
        registry=_parse_names(raw.get("registry"), REGISTRIES, where=where, field="registry"),
        deprecated=deprecated,
        note=raw.get("note"),
        model_arg=raw.get("model_arg"),
        action=action,
        severity=severity,
        payload_arg=payload_arg,
        remedies=remedies,
        trifecta_role=trifecta_role,
    )


def load_file(path: Path) -> CatalogFile:
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path.name}: not valid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"{path.name}: the top level must be a mapping")
    for required in ("id", "version", "rules"):
        if required not in raw:
            raise CatalogError(f"{path.name}: missing `{required}`")
    if not isinstance(raw["rules"], list) or not raw["rules"]:
        raise CatalogError(f"{path.name}: `rules` must be a non-empty list")

    rules = tuple(_parse_rule(r, path.name) for r in raw["rules"])
    return CatalogFile(
        id=raw["id"],
        version=raw["version"],
        path=path,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        rules=rules,
    )


def load(directory: Path | None = None) -> Catalog:
    """Load every `*.yml` in the catalogue directory, or refuse the lot."""
    directory = directory or CATALOG_DIR
    if not directory.is_dir():
        raise CatalogError(f"no catalogue directory at {directory}")
    paths = sorted(directory.glob("*.yml"))
    if not paths:
        raise CatalogError(f"no catalogue files in {directory}")

    files = tuple(load_file(p) for p in paths)

    seen: dict[str, str] = {}
    for f in files:
        for rule in f.rules:
            if rule.id in seen:
                raise CatalogError(
                    f"rule id {rule.id!r} is declared in both {seen[rule.id]} and {f.path.name}"
                )
            seen[rule.id] = f.path.name
    return Catalog(files=files)


# --- the grow-only gate -----------------------------------------------------


def diff_additive(base: Catalog, head: Catalog) -> tuple[list[str], list[str], list[str]]:
    """Compare two catalogues. Returns (added, removed, altered) rule ids.

    The invariant this exists to enforce: **the catalogue only grows.** A rule is
    never deleted and its match is never narrowed. A superseded package is marked
    `deprecated: true` and keeps matching, because repos on the old SDK still
    exist and a deleted rule is a silent blind spot.

    That invariant is what makes the nightly freshness job safe to merge without
    a person reading it. A run that only adds rules cannot reduce what the tool
    detects, so the worst case is a useless rule rather than a new blind spot.
    Anything that removes or alters a rule is a narrowing and needs review, which
    includes a rename — the right shape for a rename is to add the new name and
    keep the old one.

    Note what is deliberately NOT compared: `note` and `deprecated`. Annotating a
    rule or marking a package superseded does not change what it detects, so the
    unattended job is allowed to do both.
    """
    base_ids = {r.id: r for r in base.rules}
    head_ids = {r.id: r for r in head.rules}

    added = sorted(set(head_ids) - set(base_ids))
    removed = sorted(set(base_ids) - set(head_ids))
    altered = sorted(
        rid
        for rid in set(base_ids) & set(head_ids)
        if base_ids[rid].identity() != head_ids[rid].identity()
    )
    return added, removed, altered


# --- registry resolution ----------------------------------------------------

NPM = "https://registry.npmjs.org"
PYPI = "https://pypi.org/pypi"


@dataclass(frozen=True)
class Resolution:
    registry: str
    name: str
    rule_ids: tuple[str, ...]
    status: str  # ok | missing | deprecated | error
    detail: str = ""

    @property
    def is_problem(self) -> bool:
        return self.status in {"missing", "error"}


def _get_json(url: str, timeout: float) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers={"User-Agent": "heldby-catalog-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _resolve_one(registry: str, name: str, rule_ids: tuple[str, ...], timeout: float) -> Resolution:
    if registry == "npm":
        url = f"{NPM}/{urllib.parse.quote(name, safe='@')}"
    else:
        url = f"{PYPI}/{urllib.parse.quote(name)}/json"

    try:
        payload = _get_json(url, timeout)
    except Exception as exc:  # noqa: BLE001 — a network fault is reported, never silently ok
        return Resolution(registry, name, rule_ids, "error", f"{type(exc).__name__}: {exc}")

    if payload is None:
        return Resolution(registry, name, rule_ids, "missing", "404 — renamed, unpublished or a typo")

    if registry == "npm":
        latest = (payload.get("dist-tags") or {}).get("latest", "")
        versions = payload.get("versions") or {}
        meta = versions.get(latest) or {}
        if meta.get("deprecated"):
            return Resolution(registry, name, rule_ids, "deprecated", str(meta["deprecated"])[:160])
        return Resolution(registry, name, rule_ids, "ok", latest)

    info = payload.get("info") or {}
    if info.get("yanked"):
        return Resolution(registry, name, rule_ids, "deprecated", str(info.get("yanked_reason") or "yanked")[:160])
    return Resolution(registry, name, rule_ids, "ok", str(info.get("version", "")))


def resolve(catalog: Catalog, *, timeout: float = 20.0, workers: int = 12) -> list[Resolution]:
    """Check every catalogued package name against npm and PyPI.

    One entry per (registry, name), with the rules that depend on it, so a
    failure names what would stop being detected.
    """
    wanted: dict[tuple[str, str], set[str]] = {}
    for rule in catalog.rules:
        for registry, names in rule.registry.items():
            for name in names:
                wanted.setdefault((registry, name), set()).add(rule.id)

    targets = sorted(wanted)
    results: list[Resolution] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_resolve_one, registry, name, tuple(sorted(wanted[(registry, name)])), timeout): (
                registry,
                name,
            )
            for registry, name in targets
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: (r.status != "ok", r.registry, r.name))
    return results
