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
#: Jupyter notebooks are read as Python. Whole categories of AI work — finance,
#: research, anything data-science shaped — live primarily in notebooks, and a
#: sweep that skips them reports "no AI" over a repository that is nothing but AI.
#: Worse, it did so SILENTLY until this was added: the files matched no extension,
#: so they were never even counted as skipped.
NB_EXT = {".ipynb"}

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

#: A line comment. Stripped before matching, because prose is not code: a comment
#: reading "need to set the packages to run this code block" fired the subprocess
#: rule on the word "run", and a report whose findings include English sentences is
#: one a reader stops trusting on the first page.
COMMENT_RE = re.compile(r"(?:^|\s)(?:#+|//+)\s.*$")


#: A function or method DECLARATION. Never a call, however much its parameter list
#: looks like one: `def llm_completion(chat_prompt="", system="", temp=0.7)` fired
#: the subprocess rule on the parameter named `system`, and
#: `def run(self, queries)` fired it on the name of the function being defined.
#: The predecessor tool needed the same guard for the same reason.
DEFINITION_RE = re.compile(
    r"^\s*(?:@|(?:async\s+)?def\s|(?:export\s+)?(?:async\s+)?function\s|class\s)"
)


def strip_comment(line: str) -> str:
    """Remove a trailing line comment, leaving string literals alone.

    Deliberately conservative: it requires whitespace after the marker, so a URL
    containing `//` and a Python string containing `#` both survive. A comment
    written `#no-space` is not stripped, which over-reports rather than under-.

    One or more markers, because `## heading` is the common notebook style and a
    pattern expecting exactly one leaves every one of them unstripped.
    """
    if '"' not in line and "'" not in line and "`" not in line:
        return COMMENT_RE.sub("", line)
    return line


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

#: Keys matched EXACTLY, with no suffix. Agent frameworks name their agents with
#: `profile:` — but `profileId` is an export profile and `profile_name` is an AWS
#: credentials profile, and letting the suffix pattern near this key put both into
#: a customer-facing register as AI processes. The completeness gate caught it on
#: the first regeneration after the key was added.
LABEL_KEYS_EXACT = ("profile",)

#: Values that match the label shape but are never a process. `role: "user"` and
#: `role: "assistant"` appear in every chat-messages array ever written, and a
#: register listing "user" and "system" as AI processes is not one anybody reads
#: twice.
LABEL_STOPLIST = frozenset({
    "user", "assistant", "system", "tool", "function", "human", "ai", "model",
    "developer", "bot", "agent", "none", "default", "true", "false", "null",
    "prompt", "message", "content", "text", "json", "string", "object", "input",
    "output", "error", "unknown", "test", "example", "name", "id", "key", "value",
    # Tensor and dataset fields. These share the label shape and are never a
    # process; a register listing `input_ids` as an AI use is not read twice.
    "input_ids", "attention_mask", "token_type_ids", "labels", "logits",
    "seq_len", "max_length", "instruction", "context", "answer", "question",
    "response", "completion", "out_text", "target", "label", "features", "train",
    "validation", "embedding", "embeddings", "tokens", "temperature",
})
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
    r"[\"'`]?\b(?:(?:" + "|".join(sorted(LABEL_KEYS, key=len, reverse=True)) + r")"
    r"(?:_?(?:tag|name|id|key|label))?|" + "|".join(LABEL_KEYS_EXACT) + r")\b[\"'`]?\s*"
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
#: `feature`/`features` was removed 30 Jul 2026: `features["input_ids"]` is the
#: HuggingFace datasets column-schema API, ubiquitous in ML code, and it filled a
#: register with `seq_len`, `out_text` and `input_ids` as though they were AI
#: processes. Six of seven "named processes" in one repository were tensor fields.
#: `feature: "x"` as a LABEL is still meaningful and stays in LABEL_RE.
REGISTRY_LOOKUP_RE = re.compile(
    r"\b[\w$]*(?:process|processes|agents)[\w$]*\s*"
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
    declarations_ignored: bool
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


def notebook_source(text: str) -> str | None:
    """The code cells of a Jupyter notebook, concatenated.

    Markdown and raw cells are dropped; outputs are never read, because a cached
    output can contain anything and none of it is code that runs. Line numbers in
    a notebook finding refer to this concatenation rather than to the .ipynb file,
    which is a JSON document nobody reads by line — the report says so.
    """
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict) or "cells" not in doc:
        return None

    def strip_magics(code: str) -> str:
        """Blank out IPython magics and shell escapes, keeping line numbering.

        `!pip install …` and `%matplotlib inline` are not Python. One of them at
        the top of a notebook fails the whole parse, and every import-gated rule
        then silently cannot fire in that file — 15 of one repository's 39
        notebooks were losing their imports to a single `!pip install` on line 1.
        """
        lines = []
        for line in code.split("\n"):
            stripped = line.lstrip()
            lines.append("" if stripped[:1] in {"!", "%", "?"} else line)
        return "\n".join(lines)

    out: list[str] = []
    for cell in doc.get("cells") or []:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source")
        if isinstance(source, list):
            out.append(strip_magics("".join(s for s in source if isinstance(s, str))))
        elif isinstance(source, str):
            out.append(strip_magics(source))
    return "\n".join(out) if out else None


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
        elif isinstance(node, ast.Call):
            # importlib.import_module("litellm") — a deferred import, used
            # routinely to keep a slow package off the startup path. It is a real
            # import and the only trace of it is a string literal.
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
    return found, False


TS_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)["']([^"']+)["']"""
)
#: Named bindings — `import { openai, anthropic } from './providers'`. The same
#: re-export shape as Python's, and the binding name is the only tell that a
#: catalogued package is what is being reached through the local module.
TS_BINDING_RE = re.compile(r"\bimport\s*\{([^}]{1,300})\}\s*from")


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
        # A RE-EXPORT: `from aider.llm import litellm` gives the specifier
        # `aider.llm.litellm`, whose leaf is the catalogued package. Modules
        # commonly re-export a heavy dependency behind a local shim so the import
        # can be deferred, and the call site then imports the shim rather than the
        # package. Without this the main model call of a 47k-star coding agent is
        # invisible: `litellm.completion(**kwargs)` sits in plain sight while no
        # file in the repo appears to import litellm at all.
        if "." in specifier and specifier.rsplit(".", 1)[-1] == name:
            return True
        if "/" in specifier and specifier.rsplit("/", 1)[-1] == name:
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
        # Two shapes, anchored differently on purpose.
        #
        #   ["messages.create"]  → a method chain on an OBJECT, so it must be
        #                          preceded by a dot: `client.messages.create`.
        #   ["subprocess.run"]   → a MODULE-qualified call, where the module IS the
        #                          root, so requiring a leading dot demands
        #                          `something.subprocess.run` and never matches.
        #
        # A single bare name keeps the leading dot: `["invoke"]` must match
        # `chain.invoke(...)` and not every local function called `invoke`.
        chains: list[str] = []
        for entry in m.member:
            parts = entry.split(".")
            body = r"\s*\.\s*".join(f"(?:{_alt((part,))})" for part in parts)
            # Not-preceded-by-a-word-character, which permits both `.messages` and
            # a chain at the start of an expression while rejecting `mysubprocess`.
            # An optional leading dot cannot be used here: it consumes the very
            # character a following lookbehind would test, so the two never agree.
            prefix = r"(?<![\w])" if len(parts) > 1 else r"\.\s*"
            chains.append(prefix + body)
        out.append(("member", re.compile(rf"(?:{'|'.join(chains)})\b"), True))
    if m.ambient:
        # The negative lookbehind is load-bearing. An AMBIENT match is a global —
        # bare `eval(...)`, not `something.eval(...)`. Without it, PyTorch's
        # `model.eval()` reads as Python's eval() and every ML repository in
        # existence gets reported as executing arbitrary code. A method call on an
        # object is a member access, and members are matched by the member field.
        pattern = (
            rf"\bnew\s+{re.escape(m.ambient)}\s*\("
            if m.construct
            else rf"(?<![.\w]){re.escape(m.ambient)}\s*\("
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
    seen_sites: set[tuple[str, int, str]] = set()
    labels: dict[str, set[str]] = {}
    declarations: list[str] = []
    files_scanned = 0
    skipped: dict[str, int] = {}
    languages: dict[str, int] = {}
    unparsed: list[str] = []
    notebooks = 0
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
        elif suffix in PY_EXT or suffix in NB_EXT:
            ecosystem = "py"
        else:
            continue

        try:
            if path.stat().st_size > MAX_BYTES:
                skipped["too-large"] = skipped.get("too-large", 0) + 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if suffix in NB_EXT:
                extracted = notebook_source(text)
                if extracted is None:
                    skipped["unreadable-notebooks"] = skipped.get("unreadable-notebooks", 0) + 1
                    continue
                text = extracted
                notebooks += 1
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
            for group in TS_BINDING_RE.findall(text):
                for binding in group.split(","):
                    leaf = binding.split(" as ")[0].strip()
                    if leaf and leaf.isidentifier():
                        imports.add(leaf)

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
                for index, raw_line in enumerate(lines):
                    if len(raw_line) > 1000:
                        continue
                    if rule.action:
                        if DEFINITION_RE.match(raw_line):
                            continue
                        line = strip_comment(raw_line)
                    else:
                        line = raw_line
                    if not pattern.search(line):
                        continue
                    if rule.match.requires and not re.search(rule.match.requires, line):
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

                    key = (rel, index + 1, rule.id)
                    if key in seen_sites:
                        continue
                    seen_sites.add(key)
                    sites.append(
                        Site(
                            file=rel,
                            line=index + 1,
                            rule_id=rule.id,
                            kind=rule.kind,
                            why=why,
                            confidence=confidence,
                            evidence=raw_line.strip()[:200],
                            ecosystem=ecosystem,
                            action=rule.action,
                            severity=rule.severity,
                            model=model,
                            label=_nearby(lines, index, LABEL_RE),
                            deprecated_rule=rule.deprecated,
                        )
                    )
                    break  # one site per rule per way-of-firing per file

        for pattern in (LABEL_RE, NODE_RE, REGISTRY_LOOKUP_RE):
            for match in pattern.finditer(text):
                value = match.group(1).strip()
                if value.lower() in LABEL_STOPLIST:
                    continue
                labels.setdefault(rel, set()).add(value)

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
    if notebooks:
        limits.append(
            f"{notebooks} Jupyter notebook(s) were read as Python, from their code cells only. "
            "Line numbers for a notebook finding refer to those cells concatenated, not to a "
            "line of the .ipynb file. Cell OUTPUTS were not read — a cached output can contain "
            "anything and none of it is code that runs."
        )
    if skipped.get("unreadable-notebooks"):
        limits.append(
            f"{skipped['unreadable-notebooks']} .ipynb file(s) could not be parsed as notebooks "
            "and were not scanned."
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
        nbs = [f for f in unparsed if f.lower().endswith(".ipynb")]
        pys = [f for f in unparsed if not f.lower().endswith(".ipynb")]
        for kind, group in (("notebook", nbs), ("Python file", pys)):
            if not group:
                continue
            shown = ", ".join(group[:5]) + (" …" if len(group) > 5 else "")
            limits.append(
                f"{len(group)} {kind}(s) would not parse, so their imports are unknown and "
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
        declarations_ignored=ignore_declarations,
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
