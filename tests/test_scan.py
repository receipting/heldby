"""Scanner tests.

Most of these are regressions, and every regression names the real repo that
produced it. That ordering is deliberate: each one was found by running the
scanner over a codebase whose register we already knew the answer to, and not one
was found by reasoning about the scanner. The predecessor tool learned the same
lesson the same way — seven defects, all from contact with real code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heldby import catalog as catalog_mod
from heldby.scan import LABEL_RE, REGISTRY_LOOKUP_RE, _import_hit, _variants, scan

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def cat():
    return catalog_mod.load()


def labels_of(report) -> set[str]:
    return {name for names in report.labels.values() for name in names}


def rules_of(report) -> set[str]:
    return {site.rule_id for site in report.sites}


# --- identifier folding -----------------------------------------------------


def test_one_rule_spans_both_naming_conventions():
    """`generateContent` and `generate_content` are the same API in two languages.

    Writing both into every rule doubles the catalogue and guarantees drift.
    """
    assert "generate_content" in _variants("generateContent")
    assert "generateContent" in _variants("generate_content")


def test_import_prefix_matches_at_a_boundary_only():
    assert _import_hit("azure.ai.inference.aio", ("azure.ai.inference",))
    assert _import_hit("fs/promises", ("fs",))
    assert _import_hit("anthropic", ("anthropic",))
    # The one that matters: a rule for `open` must not claim `openai`.
    assert not _import_hit("openai", ("open",))
    assert not _import_hit("langchain_openai", ("langchain",))


# --- label extraction: every shape real code uses ---------------------------
# Each of the last three was a real declared process that went missing on the
# first run over a repo whose register we already knew.


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("{ process: 'matching', customer }", "matching"),
        ('"process": "customer-report",', "customer-report"),
        ("const PROCESS: AIFeature = 'recon-judge'", "recon-judge"),
        ("const PROCESS_TAG: AIFeature = 'contact-extraction'", "contact-extraction"),
        ("span_name = 'nightly-sweep'", "nightly-sweep"),
    ],
    ids=["inline-property", "quoted-key", "typed-constant", "suffixed-typed-constant", "assignment"],
)
def test_label_shapes(source: str, expected: str):
    found = LABEL_RE.search(source)
    assert found is not None, f"no label found in {source!r}"
    assert found.group(1) == expected


def test_a_process_named_only_by_a_registry_lookup_is_still_found():
    """Some repos never write `process: '…'` anywhere.

    Found for real: a repo whose two processes appear only as subscript keys, so
    every label shape above missed both. It read as 2/2 at first only because the
    scan was also reading unmerged branch checkouts nested inside the repo — which
    is why those are now skipped, and why this shape needed its own rule.
    """
    found = REGISTRY_LOOKUP_RE.search('const CODEGEN = AI_PROCESSES["transform-codegen"];')
    assert found is not None
    assert found.group(1) == "transform-codegen"


def test_registry_lookup_ignores_ordinary_config_subscripts():
    """`CONFIG["timeout"]` is not a process, and matching it would fill the
    register with noise. Scoped to identifiers that look like a process registry."""
    assert REGISTRY_LOOKUP_RE.search('CONFIG["timeout"]') is None
    assert REGISTRY_LOOKUP_RE.search('HEADERS["content-type"]') is None


# --- the TypeScript fixture -------------------------------------------------


def test_ts_finds_the_gateway_and_the_bypass(cat):
    report = scan(FIXTURES / "ts-messy", cat)

    assert "gateway.cloudflare-ai" in rules_of(report)
    bypassed = {s.file for s in report.bypass_candidates}
    assert "src/bypass.ts" in bypassed, "a direct provider call outside the gateway is the finding"
    assert "src/gateway.ts" not in bypassed, "the gateway module is not a bypass of itself"


def test_ts_finds_the_ungated_send(cat):
    """A Write-class site with nothing holding it is the most useful row there is."""
    report = scan(FIXTURES / "ts-messy", cat)
    sends = [s for s in report.sites if s.action == "external-comms"]
    assert any(s.file == "src/ungated.ts" for s in sends)


def test_ts_reports_a_dependency_nobody_imports(cat):
    """A catalogued dependency with no call site is a loose end, not a pass.

    Found for real in a production repo: an AI SDK in package.json that nothing
    imports. Either dead weight or a use the sweep cannot see; both are findings.
    """
    report = scan(FIXTURES / "ts-messy", cat)
    loose = {d["package"] for d in report.dependencies if not d["imported_anywhere"]}
    assert "@ai-sdk/anthropic" in loose


# --- the Python fixture -----------------------------------------------------


def test_python_langchain_is_found_by_its_module_name(cat):
    """The whole reason `imports` is per-ecosystem.

    A catalogue carrying only npm names sees nothing here: this imports
    `langchain_anthropic`, not `@langchain/anthropic`. As originally designed,
    the scanner reported "no AI detected" over a 95k-star Python AI repo.
    """
    report = scan(FIXTURES / "py-messy", cat)
    assert "langchain.chat.models" in rules_of(report)


def test_graph_nodes_are_the_register_rows(cat):
    """One shared invoke helper can serve a dozen agents.

    The rows worth having are the agents, which is what node registration names —
    the files that own them import no SDK at all.
    """
    report = scan(FIXTURES / "py-messy", cat)
    assert {"Triage Analyst", "Escalation Writer"} <= labels_of(report)


def test_python_finds_model_driven_shell_execution(cat):
    report = scan(FIXTURES / "py-messy", cat)
    assert "anthropic.messages" in rules_of(report)
    execs = [s for s in report.sites if s.action == "execute-code"]
    assert any(s.file == "report.py" for s in execs)


# --- honesty -----------------------------------------------------------------


def test_every_site_carries_a_confidence(cat):
    report = scan(FIXTURES / "ts-messy", cat)
    assert report.sites
    assert all(s.confidence in {"confirmed", "inferred"} for s in report.sites)


def test_the_report_always_states_what_it_could_not_see(cat):
    """A proof that names its own limits is the only kind worth publishing."""
    report = scan(FIXTURES / "py-messy", cat)
    blob = " ".join(report.limits).lower()
    assert "taint" in blob, "it must never be mistaken for taint analysis"
    assert "factory" in blob, "the factory blind spot is structural and must be declared"
    assert "not scanned" in blob or "were not" in blob


def test_excluded_scope_is_never_silent(cat):
    """A scope you cannot see is a scope you cannot audit.

    Tests are excluded by default because they bury real sites — but the count
    must appear in the report, or the exclusion reads as "covered everything".
    """
    report = scan(FIXTURES / "py-messy", cat, include_tests=False)
    if report.files_skipped.get("tests"):
        assert any("test file" in limit for limit in report.limits)


def test_declarations_can_be_masked_for_an_honest_run(cat):
    """The acceptance test would be circular otherwise.

    Pointed at a repo that declares its own processes, reading the declaration is
    reading the answer. The flag withholds it and says so in the limits.
    """
    read = scan(FIXTURES / "py-messy", cat)
    masked = scan(FIXTURES / "py-messy", cat, ignore_declarations=True)
    assert read.declarations, "the fixture does declare"
    assert not masked.declarations
    assert any("pure inference" in limit for limit in masked.limits)


def test_nested_branch_checkouts_are_skipped_but_reported(cat, tmp_path):
    """A nested checkout is another branch's code, not this branch's.

    Scanning it surfaces unmerged work as though it had shipped. Found for real:
    an undeclared process living only in two branch worktrees inside a repo.
    """
    (tmp_path / ".claude" / "worktrees" / "epic" / "src").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "epic" / "src" / "q.ts").write_text(
        "import Anthropic from '@anthropic-ai/sdk'\n"
        "const x = { process: 'undeclared-thing' }\n",
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    assert "undeclared-thing" not in labels_of(report)
    assert report.files_skipped.get("nested-checkouts") == 1
    assert any("nested checkout" in limit for limit in report.limits)


def test_a_repo_living_under_a_skipped_directory_name_is_still_scanned(cat, tmp_path):
    """The skip is about what's inside the root, never about where the root sits.

    Found for real in this repo: the check tested the ABSOLUTE path, so a checkout
    under `<repo>/.claude/worktrees/<name>/` — the default layout for a Claude Code
    session — matched on its own location and every file was skipped. The run then
    reported "no AI detected" over a repo it had never opened, which is the worst
    failure this tool has: silent, confident, and wrong.
    """
    import shutil

    root = tmp_path / ".claude" / "worktrees" / "session" / "checkout"
    shutil.copytree(FIXTURES / "ts-messy", root)

    report = scan(root, cat)
    assert "gateway.cloudflare-ai" in rules_of(report)
    assert report.files_scanned == scan(FIXTURES / "ts-messy", cat).files_scanned
    assert not report.files_skipped.get("nested-checkouts")


def test_scan_is_deterministic(cat):
    """Two runs, byte-identical. A report that drifts cannot be diffed in CI."""
    first = scan(FIXTURES / "ts-messy", cat).as_dict()
    second = scan(FIXTURES / "ts-messy", cat).as_dict()
    assert first == second


def test_notebook_code_cells_are_scanned(cat, tmp_path):
    """Whole categories of AI work live in notebooks.

    Before this, .ipynb matched no extension, so a Jupyter-first AI repo reported
    "no AI" — and did so SILENTLY, since the files were never even counted as
    skipped.
    """
    import json as _json

    (tmp_path / "analysis.ipynb").write_text(
        _json.dumps({
            "cells": [
                {"cell_type": "markdown", "source": ["# not code\n"]},
                {"cell_type": "code", "source": ["import anthropic\n",
                                                 "c = anthropic.Anthropic()\n",
                                                 "c.messages.create(model='claude-opus-4-8')\n"]},
            ]
        }),
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    assert "anthropic.messages" in {s.rule_id for s in report.sites}
    assert any("notebook" in limit.lower() for limit in report.limits)


def test_notebook_outputs_are_never_read(cat, tmp_path):
    """A cached output can contain anything, and none of it is code that runs."""
    import json as _json

    (tmp_path / "out.ipynb").write_text(
        _json.dumps({
            "cells": [{
                "cell_type": "code",
                "source": ["print(1)\n"],
                "outputs": [{"text": ["client.messages.create(model='claude-opus-4-8')\n"]}],
            }]
        }),
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    assert "anthropic.messages" not in {s.rule_id for s in report.sites}


def test_chat_role_values_are_not_processes(cat, tmp_path):
    """`role: "user"` appears in every messages array ever written."""
    (tmp_path / "chat.py").write_text(
        'messages = [{"role": "user", "content": x}, {"role": "assistant", "content": y}]\n'
        'profile: str = "Architect"\n',
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    found = {n for names in report.labels.values() for n in names}
    assert "Architect" in found, "an agent framework's profile IS a process name"
    assert not ({"user", "assistant"} & found)


def test_a_litellm_import_is_not_a_proxy(cat, tmp_path):
    """Measured across ten real repositories: the old bare-"litellm" match fired
    33 times with zero true positives — every hit an import, a UI component name
    or a dropdown entry. False-red bias is deliberate, but a rule that is only
    ever wrong teaches readers to skim past the whole section."""
    (tmp_path / "a.py").write_text("from aider.llm import litellm\n", encoding="utf-8")
    (tmp_path / "b.py").write_text('PROVIDERS = ["litellm", "openai"]\n', encoding="utf-8")
    report = scan(tmp_path, cat)
    assert "gateway.litellm-proxy" not in {s.rule_id for s in report.sites}


def test_a_real_litellm_proxy_url_still_fires(cat, tmp_path):
    (tmp_path / "c.py").write_text(
        'client = OpenAI(base_url="http://localhost:4000/v1")\n', encoding="utf-8"
    )
    report = scan(tmp_path, cat)
    assert "gateway.litellm-proxy" in {s.rule_id for s in report.sites}


def test_a_reexported_package_is_still_an_import(cat, tmp_path):
    """`from aider.llm import litellm` — a heavy dependency behind a local shim so
    the import can be deferred. Without this the main model call of a 47k-star
    coding agent is invisible: `litellm.completion(...)` sits in plain sight while
    no file in the repo appears to import litellm at all."""
    (tmp_path / "shim.py").write_text("import litellm\n", encoding="utf-8")
    (tmp_path / "models.py").write_text(
        "from shim import litellm\n\ndef go(**kw):\n    return litellm.completion(**kw)\n",
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    hit = [s for s in report.sites if s.rule_id == "litellm.completion" and s.file == "models.py"]
    assert hit, "the call site must be found, not only the shim"


def test_a_deferred_import_by_string_counts(cat, tmp_path):
    """importlib.import_module("litellm") keeps a slow package off the startup
    path. It is a real import whose only trace is a string literal."""
    (tmp_path / "lazy.py").write_text(
        'import importlib\n'
        'litellm = importlib.import_module("litellm")\n'
        'r = litellm.completion(model="gpt-4o")\n',
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    assert "litellm.completion" in {s.rule_id for s in report.sites}


def test_notebook_magics_do_not_break_the_parse(cat, tmp_path):
    """`!pip install …` on line 1 is not Python.

    One of them failed the parse of a whole notebook, and every import-gated rule
    then silently could not fire in it — 15 of one repository's 39 notebooks were
    losing their imports this way, costing 20 model call sites.
    """
    import json as _json

    (tmp_path / "nb.ipynb").write_text(
        _json.dumps({"cells": [
            {"cell_type": "code", "source": ["!pip install anthropic\n", "%matplotlib inline\n"]},
            {"cell_type": "code", "source": ["import anthropic\n",
                                             "anthropic.Anthropic().messages.create(model='x')\n"]},
        ]}),
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    assert "anthropic.messages" in {s.rule_id for s in report.sites}
    assert not any("would not parse" in limit for limit in report.limits)


def test_huggingface_dataset_columns_are_not_processes(cat, tmp_path):
    """`features["input_ids"]` is the datasets column-schema API, ubiquitous in ML
    code. It filled a register with `seq_len` and `out_text` as AI processes — six
    of seven "named processes" in one repository were tensor fields."""
    (tmp_path / "t.py").write_text(
        'x = features["input_ids"]\ny = features["seq_len"]\n'
        'z = AI_PROCESSES["real-thing"]\n',
        encoding="utf-8",
    )
    found = {n for names in scan(tmp_path, cat).labels.values() for n in names}
    assert "real-thing" in found
    assert not ({"input_ids", "seq_len"} & found)


def test_an_unparsed_notebook_is_not_called_a_python_file(cat, tmp_path):
    """Misattributing its own failure sends a reader looking in the wrong place."""
    import json as _json

    (tmp_path / "bad.ipynb").write_text(
        _json.dumps({"cells": [{"cell_type": "code", "source": ["def broken(:\n"]}]}),
        encoding="utf-8",
    )
    report = scan(tmp_path, cat)
    assert any("notebook(s) would not parse" in limit for limit in report.limits)


def test_a_method_named_eval_is_not_pythons_eval(cat, tmp_path):
    """`model.eval()` is PyTorch's eval MODE, and it appears in every ML repository
    ever written. Reporting it as arbitrary code execution makes the whole
    protected-action section noise."""
    (tmp_path / "m.py").write_text(
        "def load():\n    return model.eval(), tokenizer\n", encoding="utf-8"
    )
    execs = [s for s in scan(tmp_path, cat).sites if s.action == "execute-code"]
    assert not execs, f"unexpected: {[(s.rule_id, s.evidence) for s in execs]}"


def test_bare_eval_still_fires(cat, tmp_path):
    (tmp_path / "e.py").write_text("def go(src):\n    return eval(src)\n", encoding="utf-8")
    assert any(s.action == "execute-code" for s in scan(tmp_path, cat).sites)


def test_a_recipient_change_is_found_and_is_not_the_send(cat, tmp_path):
    """`alter-recipients` sat in the closed vocabulary with no rule at all, so a
    repo that rewrites who its mail reaches produced an empty section — and
    receipting's declared `contact-extraction` reach had nothing to corroborate.

    The bar the rules have to clear is firing where `comms.email.sdk` does not.
    Changing an audience and sending to it are different lines here, and a rule
    that could not separate them would be a second copy of the send row.
    """
    (tmp_path / "m.ts").write_text(
        "import { WebClient } from '@slack/web-api'\n"
        "import nodemailer from 'nodemailer'\n"
        "const opts = {}\n"
        "opts.bcc = extracted\n"
        "await web.conversations.invite({ channel, users: picked })\n"
        "await nodemailer.createTransport({}).sendMail(opts)\n",
        encoding="utf-8",
    )
    sites = scan(tmp_path, cat).sites
    changed = {s.line for s in sites if s.action == "alter-recipients"}
    sent = {s.line for s in sites if s.action == "external-comms"}
    assert changed == {4, 5}
    assert sent == {6}
    assert not (changed & sent), "a recipient change is not the send"


def test_a_plain_send_is_never_a_recipient_change(cat, tmp_path):
    """The duplicate trap, and the reason there is no rule for the `to:` of a send.

    Every send names a recipient. A rule reading that argument fires on all of
    them — including `to: 'ops@corp.com'`, which no model chose — so the whole
    section becomes `external-comms` under a second name.
    """
    (tmp_path / "s.ts").write_text(
        "import { Resend } from 'resend'\n"
        "const resend = new Resend(k)\n"
        "await resend.emails.send({ from: 'a@b.c', to: contact.email, html: prose })\n",
        encoding="utf-8",
    )
    sites = scan(tmp_path, cat).sites
    assert any(s.action == "external-comms" for s in sites)
    assert not [s for s in sites if s.action == "alter-recipients"]


def test_only_a_recipient_header_counts_and_only_when_written(cat, tmp_path):
    """`add_header` also sets Content-Type, and `.to` is read far more often than
    it is assigned. Both read as recipient changes until `requires` narrowed them."""
    (tmp_path / "h.py").write_text(
        "import smtplib\n"
        "from email.message import EmailMessage\n"
        "msg = EmailMessage()\n"
        "msg.add_header('Content-Type', 'text/html')\n"
        "addr = msg.to\n"
        "msg.add_header('Bcc', chosen)\n",
        encoding="utf-8",
    )
    hits = [s.line for s in scan(tmp_path, cat).sites if s.action == "alter-recipients"]
    assert hits == [6]


def test_prose_in_a_comment_is_not_a_finding(cat, tmp_path):
    """A comment reading "need to set the packages to run this code block" fired
    the subprocess rule on the word "run". A report whose findings include English
    sentences is one a reader stops trusting on the first page."""
    (tmp_path / "c.py").write_text(
        "import os\n# need to set the packages to run this code block\nx = 1\n",
        encoding="utf-8",
    )
    hits = [s for s in scan(tmp_path, cat).sites if s.action == "execute-code"]
    assert not hits, f"unexpected: {[(s.rule_id, s.evidence) for s in hits]}"


def test_profile_takes_no_suffix(cat, tmp_path):
    """`profile:` names an agent; `profileId` is an export profile and
    `profile_name` is an AWS credentials profile. The suffix pattern put both into
    a customer-facing register as AI processes, and the completeness gate caught
    it on the first regeneration after the `profile` key was added."""
    (tmp_path / "a.ts").write_text("const x = { profileId: 'internal-control-v1' }\n", encoding="utf-8")
    (tmp_path / "b.py").write_text('session = boto3.Session(profile_name="r2")\n', encoding="utf-8")
    (tmp_path / "c.py").write_text('profile: str = "Architect"\n', encoding="utf-8")
    found = {n for names in scan(tmp_path, cat).labels.values() for n in names}
    assert "Architect" in found
    assert not ({"internal-control-v1", "r2"} & found)
