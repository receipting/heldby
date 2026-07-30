"""The discovery sweep: every place a model might run, and every protected action near it.

This phase is deliberately **high recall and low precision**. It is not the answer;
it is the worklist the classify pass reads code against. An over-flagged site costs
a minute of a reviewer's attention. A wrongly-cleared site hides exactly the risk
this tool exists to find, and hides it behind a clean report — which is worse than
no report. So when in doubt, this emits the site and marks it `inferred`.

**It is not taint analysis and must never be read as any.** There is no call graph
here, no value flow, no whole-program reachability. Three reasons, all learned by
running the predecessor that did have those things:

- It graded a well-known agent that shells out to the host as safe, because the
  agent reached its model through an interface and the call graph severed there.
  Worse, its own uncertainty instrumentation reported zero doubt at precisely the
  point it had gone blind.
- It required the target's dependencies to be installed, which for a tool a
  stranger points at an unfamiliar repo is fatal to ever being run.
- It was TypeScript-only, and the Python it could not see is where a real gateway
  bypass lived unnoticed for months.

What replaces it is honest: report the sites, report what is syntactically near
them, name what could not be resolved, and let a model read the code. The model is
the frontend, which is what makes this language-agnostic for free.
"""

from __future__ import annotations

import ast
import json
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog
from .schema import Rule

TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}
PY_EXT = {".py", ".pyi"}

#: Directories never worth reading. Each is either not the target's own code or a
#: build artefact of it, and scanning them buries the repo's own sites under
#: thousands from vendored dependencies.
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".nuxt", ".output", "out", "coverage", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "vendor", "target", ".wip", ".turbo", ".svelte-kit", "site-packages",
}

#: Directories holding a NESTED CHECKOUT of another branch of the same repo. Their
#: code is real, but it is not this branch's code: scanning them reports a branch's
#: copy of an already-fixed file as a live finding, and surfaces unmerged work as
#: though it had shipped. Skipped, and the count is reported — because "there is an
#: undeclared AI process on an unmerged branch" is worth knowing, just not worth
#: confusing with production.
NESTED_CHECKOUT_DIRS = {".claude", ".worktrees", "worktrees"}

#: A file this big is generated. Reported as skipped rather than silently dropped.
MAX_BYTES = 1_500_000

#: Test files. Excluded by default because a test that names every provider URL it
#: supports is not an AI use, and on a real repo those sites outnumber the genuine
#: ones and bury them. The count of what was excluded is always reported: a scope
#: you cannot see is a scope you cannot audit.
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e|fixtures?)(/|$)"
    r"|(^|/)test_[^/]+$"
    r"|_test\.(py|pyi|ts|tsx|js|jsx|mjs|cjs)$"
    r"|\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$",
    re.IGNORECASE,
)

#: Text saying a URL is reaching the network here, rather than sitting in config.
NETWORK_HINT = re.compile(
    r"\b(fetch|axios|request|requests|httpx|urllib|urlopen|http|client|post|send|curl)\b",
    re.IGNORECASE,
)

#: How a call site labels itself for its own observability. Every AI feature
#: already names itself somewhere, for its own logging and cost attribution — and
#: that name makes a far better register row than a file path.
LABEL_KEYS = (
    "process", "feature", "run_name", "runName", "span_name", "spanName",
    "operation_name", "operationName", "task", "agent_name", "agentName",
)
#: Matches three shapes, because real code uses all three:
#:   process: 'matching'                        an inline object property
#:   process = 'matching'                       a plain assignment
#:   const PROCESS: AIFeature = 'recon-judge'   hoisted to a TYPED constant
#: The third is the one that gets missed. A type annotation between the name and
#: the literal widens the type away from the literal, and a naive
#: `key[:=]"value"` pattern stops dead at the annotation — which is how a real
#: declared process went unfound on the first run over a repo whose register we
#: already knew the answer to.
#: Four shapes, all of which appear in real code, and every one of the last three
#: was found by running this over repos whose answer we already knew:
#:   process: 'matching'                        an inline object property
#:   "process": "customer-report"               a QUOTED key (JSON-ish, and how
#:                                              Python dicts and JS objects write it)
#:   const PROCESS: AIFeature = 'recon-judge'   hoisted to a TYPED constant
#:   const PROCESS_TAG: AIFeature = 'contact-…' a typed constant with a SUFFIXED name
#: A type annotation between the name and the literal widens the type away from the
#: literal, so a naive `key[:=]"value"` pattern stops dead at the annotation. Each of
#: these misses was one real declared process silently absent from the register.
LABEL_RE = re.compile(
    r"[\"'`]?\b(?:" + "|".join(sorted(LABEL_KEYS, key=len, reverse=True)) + r")"
    r"(?:_?(?:tag|name|id|key|label))?\b[\"'`]?\s*"
    r"(?::\s*[\w.$<>\[\]|]+(?:\s*\|\s*[\w.$<>\[\]|]+)*)?\s*"
    r"[:=]\s*[\"'`]([A-Za-z0-9][\w.\- ]{1,60})[\"'`]",
    re.IGNORECASE,
)
#: A process named by looking it up in a registry, rather than by labelling a call:
#:   const CODEGEN = AI_PROCESSES["transform-codegen"]
#:   createGatewayClient(key, "transform-codegen")
#: Some repos never write `process: '…'` anywhere; the name only ever appears as a
#: subscript key. Scoped to identifiers that look like a process registry, because
#: a bare `CONFIG["timeout"]` is not a process and matching it would fill the
#: register with noise. This reads a USAGE site, not a declaration — it learns that
#: a process by this name runs here, and nothing about its class or what holds it,
#: which is the line --ignore-declarations has to keep to stay honest.
REGISTRY_LOOKUP_RE = re.compile(
    r"\b[\w$]*(?:process|processes|feature|features|agent|agents)[\w$]*\s*"
    r"\[\s*[\"'`]([A-Za-z][\w.\- ]{1,60})[\"'`]\s*\]",
    re.IGNORECASE,
)

#: Graph node registration — for agent frameworks this is the real process list.
NODE_RE = re.compile(
    r"\badd_?[Nn]ode\s*\(\s*[\"'`]([\w.\- ]{1,60})[\"'`]"
)

MODEL_ID_RE = re.compile(
    r"[\"'`]((?:claude|gpt|o1|o3|o4|gemini|llama|mistral|mixtral|command|deepseek|qwen"
    r"|grok|kimi|glm|phi|nova)[\w.:@\-]{2,60})[\"'`]",
    re.IGNORECASE,
)
MODEL_ASSIGN_RE = re.compile(r"\bmodel(?:_?id|_?name)?\s*[:=]\s*[\"'`]([^\"'`]{2,80})[\"'`]")


def _variants(name: str) -> set[str]:
    """Both naming conventions for one identifier.

    `generateContent` and `generate_content` are the same API in two languages, so
    one catalogue rule must match both. Done here rather than by folding the source,
    because folding the source loses the line numbers.
    """
    out = {name}
    out.add(re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower())
    parts = name.split("_")
    if len(parts) > 1:
        out.add(parts[0] + "".join(p.title() for p in parts[1:]))
    return {v for v in out if v}


def _alt(names: tuple[str, ...]) -> str:
    variants: set[str] = set()
    for name in names:
        variants |= _variants(name)
    return "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))


@dataclass(frozen=True)
class Site:
    file: str
    line: int
    rule_id: str
    kind: str
    why: str
    confidence: str
    evidence: str
    ecosystem: str | None = None
    action: str | None = None
    severity: str | None = None
    model: str | None = None
    label: str | None = None
    deprecated_rule: bool = False

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "rule": self.rule_id,
            "kind": self.kind,
            "why": self.why,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "ecosystem": self.ecosystem,
            "action": self.action,
            "severity": self.severity,
            "model": self.model,
            "label": self.label,
        }


@dataclass
class ScanReport:
    target: str
    files_scanned: int
    files_skipped: dict[str, int]
    languages: dict[str, int]
    sites: list[Site]
    labels: dict[str, list[str]]
    dependencies: list[dict]
    gateways: list[str]
    bypass_candidates: list[Site]
    declarations: list[str]
    limits: list[str]
    provenance: list[dict]

    @property
    def model_sites(self) -> list[Site]:
        return [s for s in self.sites if s.kind == "model"]

    @property
    def action_sites(self) -> list[Site]:
        return [s for s in self.sites if s.action]

    def as_dict(self) -> dict:
        return {
            "schema": "heldby.scan.v0",
            "target": self.target,
            "scope": {
                "files_scanned": self.files_scanned,
                "files_skipped": self.files_skipped,
                "languages": self.languages,
            },
            "provenance": self.provenance,
            "gateways": self.gateways,
            "declarations": self.declarations,
            "sites": [s.as_dict() for s in self.sites],
            "bypass_candidates": [s.as_dict() for s in self.bypass_candidates],
            "labels": self.labels,
            "dependencies": self.dependencies,
            "limits": self.limits,
        }


# --- import extraction ------------------------------------------------------


def _py_imports(text: str) -> tuple[set[str], bool]:
    """Import specifiers from Python source, via the standard library parser.

    Python gets a real parse for free, which TypeScript does not without requiring
    the target's toolchain. Where a language answers accurately and cheaply, take
    it — and say plainly where it did not.
    """
    try:
        # Someone else's lint warnings are not our output. Parsing arbitrary code
        # raises SyntaxWarning for things like an invalid escape sequence, and
        # letting that surface makes the tool look like it is complaining about
        # itself in the middle of a register a customer is going to read.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set(), True
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}")
    return found, False


TS_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)["']([^"']+)["']"""
)


def _import_hit(specifier: str, names: tuple[str, ...]) -> bool:
    """Does an import specifier satisfy one of a rule's names?

    Prefix matching at a boundary, so `azure.ai.inference` covers
    `azure.ai.inference.aio` and `fs` covers `fs/promises`, while `open` does not
    match `openai`.
    """
    for name in names:
        if specifier == name:
            return True
        if specifier.startswith(name) and len(specifier) > len(name) and specifier[len(name)] in "./":
            return True
    return False


# --- matching ---------------------------------------------------------------


def _compile(rule: Rule) -> list[tuple[str, re.Pattern[str], bool]]:
    """(why, pattern, needs_import) for every way this rule can fire."""
    out: list[tuple[str, re.Pattern[str], bool]] = []
    m = rule.match

    if m.symbol:
        out.append(("symbol", re.compile(rf"\b(?:{_alt(m.symbol)})\b"), True))
    if m.member:
        # Each entry is one alternative, and an entry may itself be a dotted access
        # chain: `["emails.send", "sendMail"]` means either. Treating the list as a
        # single chain instead — which is what the first cut did — builds a regex
        # like `.emails.send.sendMail` that can never match anything, and the rule
        # silently never fires. A rule that cannot fire is worse than no rule.
        chains = [
            r"\s*\.\s*".join(f"(?:{_alt((part,))})" for part in entry.split("."))
            for entry in m.member
        ]
        out.append(("member", re.compile(rf"\.\s*(?:{'|'.join(chains)})\b"), True))
    if m.ambient:
        pattern = (
            rf"\bnew\s+{re.escape(m.ambient)}\s*\("
            if m.construct
            else rf"\b{re.escape(m.ambient)}\s*\("
        )
        out.append(("ambient", re.compile(pattern), False))
    if m.url_contains:
        out.append(("url", re.compile("|".join(re.escape(u) for u in m.url_contains)), False))
    if m.url_literal:
        out.append(
            ("url-literal", re.compile("|".join(re.escape(u) for u in m.url_literal)), False)
        )
    if m.env:
        out.append(
            ("env-var", re.compile(r"\b(?:" + "|".join(re.escape(e) for e in m.env) + r")\b"), False)
        )
    return out


ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)?\s*([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=")
IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _network_identifiers(lines: list[str]) -> set[str]:
    """Identifiers appearing on any line that looks like a network call.

    This is the one-hop resolution. A base URL is almost never written at the call
    site; it is hoisted to a constant and interpolated later:

        const GATEWAY = 'https://gateway.example.com/v1'
        const res = await fetch(`${GATEWAY}/messages`, ...)

    Judging the URL line alone, there is no network call anywhere near it, so the
    site reads as config rather than traffic — and a real gateway in a real repo
    came back "present in config, no confirmed call routes through it" for exactly
    this reason. Following the constant one hop fixes it, and one hop is all this
    claims: the predecessor tool needed the same fix to take two repos from zero
    detected model calls to forty-five.
    """
    out: set[str] = set()
    for line in lines:
        if len(line) <= 1000 and NETWORK_HINT.search(line):
            out.update(IDENT_RE.findall(line))
    return out


def _nearby(lines: list[str], index: int, pattern: re.Pattern[str], radius: int = 6) -> str | None:
    lo, hi = max(0, index - radius), min(len(lines), index + radius + 1)
    for i in range(lo, hi):
        found = pattern.search(lines[i])
        if found:
            return found.group(1) if found.groups() else found.group(0)
    return None


def scan(
    root: Path,
    catalog: Catalog,
    *,
    ignore_declarations: bool = False,
    include_tests: bool = False,
) -> ScanReport:
    compiled = {rule.id: _compile(rule) for rule in catalog.rules}

    sites: list[Site] = []
    labels: dict[str, set[str]] = {}
    declarations: list[str] = []
    files_scanned = 0
    skipped: dict[str, int] = {}
    languages: dict[str, int] = {}
    unparsed: list[str] = []
    file_imports: dict[str, set[str]] = {}

    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if any(part in NESTED_CHECKOUT_DIRS for part in path.parts):
            if path.suffix.lower() in TS_EXT | PY_EXT:
                skipped["nested-checkouts"] = skipped.get("nested-checkouts", 0) + 1
            continue
        suffix = path.suffix.lower()
        if suffix in TS_EXT:
            ecosystem = "ts"
        elif suffix in PY_EXT:
            ecosystem = "py"
        else:
            continue

        try:
            if path.stat().st_size > MAX_BYTES:
                skipped["too-large"] = skipped.get("too-large", 0) + 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue

        rel = str(path.relative_to(root))
        if not include_tests and TEST_PATH_RE.search(rel):
            skipped["tests"] = skipped.get("tests", 0) + 1
            continue
        if rel.endswith((".d.ts", ".d.mts", ".d.cts")):
            # Ambient type declarations, usually generated and sometimes enormous.
            # They describe an API rather than calling one, so a hit here is noise:
            # a platform's generated bindings file names every service it offers.
            skipped["type-declarations"] = skipped.get("type-declarations", 0) + 1
            continue

        files_scanned += 1
        languages[ecosystem] = languages.get(ecosystem, 0) + 1
        lines = text.splitlines()

        if ecosystem == "py":
            imports, failed = _py_imports(text)
            if failed:
                unparsed.append(rel)
        else:
            imports = set(TS_IMPORT_RE.findall(text))

        file_imports[rel] = imports
        network_idents = _network_identifiers(lines)

        if "AI_PROCESSES" in text or "defineProcesses" in text:
            declarations.append(rel)

        for rule in catalog.rules:
            eco_names = rule.imports.get(ecosystem, ())
            has_import = bool(eco_names) and any(_import_hit(s, eco_names) for s in imports)

            for why, pattern, needs_import in compiled[rule.id]:
                if needs_import and not has_import:
                    continue
                for index, line in enumerate(lines):
                    if len(line) > 1000 or not pattern.search(line):
                        continue

                    if why in {"symbol", "member"}:
                        confidence = "confirmed" if has_import else "inferred"
                    elif why in {"url", "url-literal"}:
                        assigned = ASSIGN_RE.match(line)
                        reaches_network = bool(NETWORK_HINT.search(line)) or (
                            assigned is not None and assigned.group(1) in network_idents
                        )
                        confidence = "confirmed" if reaches_network else "inferred"
                    else:
                        confidence = "inferred" if why == "env-var" else "confirmed"

                    model = None
                    if rule.kind == "model":
                        model = _nearby(lines, index, MODEL_ASSIGN_RE) or _nearby(
                            lines, index, MODEL_ID_RE
                        )

                    sites.append(
                        Site(
                            file=rel,
                            line=index + 1,
                            rule_id=rule.id,
                            kind=rule.kind,
                            why=why,
                            confidence=confidence,
                            evidence=line.strip()[:200],
                            ecosystem=ecosystem,
                            action=rule.action,
                            severity=rule.severity,
                            model=model,
                            label=_nearby(lines, index, LABEL_RE),
                            deprecated_rule=rule.deprecated,
                        )
                    )
                    break  # one site per rule per way-of-firing per file

        for match in LABEL_RE.finditer(text):
            labels.setdefault(rel, set()).add(match.group(1))
        for match in NODE_RE.finditer(text):
            labels.setdefault(rel, set()).add(match.group(1))
        for match in REGISTRY_LOOKUP_RE.finditer(text):
            labels.setdefault(rel, set()).add(match.group(1))

    dependencies = _dependencies(root, catalog, file_imports)

    gateway_ids = {r.id for r in catalog.rules if r.id.startswith("gateway.")}
    gateway_sites = [s for s in sites if s.rule_id in gateway_ids]
    gateway_files = {s.file for s in gateway_sites}
    gateways = sorted({s.rule_id for s in gateway_sites})

    # A bypass is only a bypass relative to a gateway something actually routes
    # THROUGH. A gateway host appearing as one entry in a list of fourteen
    # provider base URLs is a menu option, not a mandate, and calling the other
    # thirteen "bypasses" of it inverts the finding. So the claim requires at
    # least one confirmed gateway site — a URL reaching a network call — and not
    # merely a string sitting in config.
    mandated = [s for s in gateway_sites if s.confidence == "confirmed"]
    bypass: list[Site] = []
    if mandated:
        for site in sites:
            if site.kind != "model" or site.rule_id in gateway_ids:
                continue
            if site.file in gateway_files:
                continue
            if site.rule_id.startswith("provider.") or site.rule_id.endswith(".client.construct"):
                bypass.append(site)

    limits = [
        "Not taint analysis. No call graph and no value flow: a site's proximity to a "
        "protected action is syntactic, and reach is a claim for the classify pass to make.",
        "A model client built by a factory in one module and called in another leaves no call "
        "site in the module that owns the feature. Both are reported; neither is linked.",
        "Languages read: TypeScript/JavaScript and Python only. Go, Java, C#, Ruby, PHP and "
        "Rust were NOT scanned, and a model call in one of them is invisible here.",
    ]
    if skipped.get("tests"):
        limits.append(
            f"{skipped['tests']} test file(s) were NOT scanned. A test naming every provider "
            "URL it supports is not an AI use, and on a real repo those sites bury the genuine "
            "ones — but a model call that only exists in a test is invisible here. "
            "Re-run with --include-tests to cover them."
        )
    if gateways and not mandated:
        limits.append(
            f"A gateway ({', '.join(gateways)}) appears in config but no confirmed call routes "
            "through it, so no bypass claim is made. It reads as one provider option among "
            "several rather than a mandated path."
        )
    if skipped.get("nested-checkouts"):
        limits.append(
            f"{skipped['nested-checkouts']} file(s) inside nested checkouts of other branches "
            f"({', '.join(sorted(NESTED_CHECKOUT_DIRS))}) were not scanned. Their code is real but "
            "it is not this branch's; an AI call that exists only on an unmerged branch is "
            "invisible here, and worth checking separately before it ships."
        )
    if skipped.get("type-declarations"):
        limits.append(
            f"{skipped['type-declarations']} ambient type-declaration file(s) (.d.ts) were not "
            "scanned. They describe APIs rather than call them."
        )
    if skipped.get("too-large"):
        limits.append(f"{skipped['too-large']} file(s) over {MAX_BYTES // 1000}kB were not read.")
    if skipped.get("unreadable"):
        limits.append(f"{skipped['unreadable']} file(s) could not be read.")
    if unparsed:
        shown = ", ".join(unparsed[:5]) + (" …" if len(unparsed) > 5 else "")
        limits.append(
            f"{len(unparsed)} Python file(s) would not parse, so their imports are unknown and "
            f"import-gated rules could not fire in them: {shown}"
        )
    if declarations:
        state = "IGNORED (--ignore-declarations), so this run is pure inference" if ignore_declarations else "read"
        limits.append(f"{len(declarations)} file(s) carry an AI declaration; declarations were {state}.")

    return ScanReport(
        target=str(root),
        files_scanned=files_scanned,
        files_skipped=skipped,
        languages=languages,
        sites=sites,
        labels={k: sorted(v) for k, v in sorted(labels.items())},
        dependencies=dependencies,
        gateways=gateways,
        bypass_candidates=bypass,
        declarations=[] if ignore_declarations else sorted(declarations),
        limits=limits,
        provenance=catalog.provenance(),
    )


def _dependencies(root: Path, catalog: Catalog, file_imports: dict[str, set[str]]) -> list[dict]:
    """Catalogued packages a manifest declares, and whether anything imports them.

    A dependency with no call site is a loose end worth a line in the register: it
    is either dead weight or a use the sweep could not see, and both are findings.
    """
    declared: dict[str, set[str]] = {}

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for name in data.get(section) or {}:
                declared.setdefault(name, set()).add("package.json")

    for filename in ("requirements.txt", "requirements-dev.txt"):
        req = root / filename
        if req.is_file():
            for raw in req.read_text(encoding="utf-8", errors="replace").splitlines():
                dep = re.split(r"[<>=!~\[;#\s]", raw.strip(), maxsplit=1)[0]
                if dep and not dep.startswith("-"):
                    declared.setdefault(dep, set()).add(filename)

    toml = root / "pyproject.toml"
    if toml.is_file():
        try:
            import tomllib

            data = tomllib.loads(toml.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a malformed manifest must not stop the sweep
            data = {}
        deps = list((data.get("project") or {}).get("dependencies") or [])
        for group in (data.get("dependency-groups") or {}).values():
            if isinstance(group, list):
                deps += [d for d in group if isinstance(d, str)]
        for raw in deps:
            dep = re.split(r"[<>=!~\[;\s]", raw.strip(), maxsplit=1)[0]
            if dep:
                declared.setdefault(dep, set()).add("pyproject.toml")

    all_imports = {spec for specs in file_imports.values() for spec in specs}
    out: list[dict] = []
    for rule in catalog.rules:
        for registry, names in rule.registry.items():
            for name in names:
                if name not in declared:
                    continue
                import_names = rule.imports.get("ts" if registry == "npm" else "py", ())
                used = (
                    any(_import_hit(spec, import_names) for spec in all_imports)
                    if import_names
                    else False
                )
                out.append(
                    {
                        "package": name,
                        "registry": registry,
                        "declared_in": sorted(declared[name]),
                        "rule": rule.id,
                        "imported_anywhere": used,
                    }
                )
    return sorted(out, key=lambda d: (d["imported_anywhere"], d["package"]))
