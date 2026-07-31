# heldby

**Find every place AI runs in your codebase, and name what stands between each model's output and a real-world effect.**

You get an *AI register*: a table you can hand to an auditor, paste into a vendor
questionnaire, or publish as a trust-centre page.

```
| Process        | Class    | Held by                                          |
|----------------|----------|--------------------------------------------------|
| matching       | Decide   | sign gate; totals recomputed; threshold else human|
| support-chat   | Converse | closed loop; no tool use at all                   |
| closing-draft  | Write    | a named person picks recipients and clicks Send   |
| report-writer  | Write    | **nothing**                                       |
```

That last row is the one worth having. A register that can't record a gap is a brochure.

---

## Run it

You need [`uv`](https://docs.astral.sh/uv/). Your repo needs nothing — no dependencies
installed, no build, no config. heldby writes nothing unless you run `adopt`.

**The full audit.** Classification means reading the code around each call, so it runs in
[Claude Code](https://claude.com/claude-code):

```
/plugin marketplace add receipting/heldby
```

```
/plugin install heldby@heldby
```

Then from your repo, `/heldby` — or just ask for an AI register and it triggers itself. It
sweeps, reads the code around every call site, argues with its own answers, and writes
`ai-register.md`. Every row it drafts is marked † until you review it.

You get `ai-register.md`, `ai-register.json`, and — with `--out-html` — a
self-contained HTML register that prints to PDF from any browser (Cmd-P). One
file, no assets, nothing to install: heldby has a single dependency and a PDF
library would drag in a system toolchain, which is the fastest way to stop a tool
being pointed at unfamiliar repos.

**The inventory alone**, no Claude Code:

```bash
uvx heldby scan .
```

Every candidate model call, every protected action near it, and what the sweep couldn't
see. `--json` to pipe it somewhere.

---

## The four classes

| Class | The rule |
|---|---|
| **Read** | Turns a document or message into structured data. No person required — but the output must be checkable against something real: a column that exists in the file, an account already on file, a total that reconciles. If it can't be checked against the world, it isn't Read. |
| **Converse** | Answers the person who asked — them and you, nobody else. No separate review, because the person who asked is the person who judges the answer as they read it. |
| **Decide** | Acts with a consequential real-world effect and no person on the fast path, *by design*. Bounded by deterministic gates plus a configured threshold; everything outside the threshold goes to a queue a person works. |
| **Write** | Produces prose the system carries to someone else. A named person edits and releases it, and the record says who. |

Risk rises left to right: **Read < Converse < Decide < Write**. A process spanning two
classes gets the stricter one.

<p align="center">
  <img src="diagrams/four-classes.svg" alt="Four classes of AI use — Read, Decide, Converse, Write — each with what stands between the model output and the real world" width="100%">
</p>

### Why four and not two

Input/output is the obvious split and it breaks on the first hard case. A payment-matching
engine reads nothing a person will see and writes nothing a person will read — **and it
moves money**. File it under input and you've classified your only money-moving process as
low risk. File it under output and you've committed to a human reviewing every allocation,
which is the whole job the software exists to remove.

Classes are defined by the gate, not the technology. Two call sites using the same model on
the same document belong to different classes if what they can reach differs.

### The closed-loop test

Converse is the class people abuse, because "it's just a chatbot, the user reads it" is the
easiest way to dodge review. All three must hold:

1. **A person started it** — not a cron, not a queue, not another system.
2. **It reaches only the asker and the operator.** Both parties to the conversation may
   hold it. What breaks the loop is a *third* party the asker didn't address.
3. **It does nothing.** No payment, no send, no write to a system of record, no row another
   process later acts on.

Fail one and it's Write. The third test is where real systems fail: watch for model output
persisted and fed back into a later prompt. Nothing was sent, no money moved, and the
model's prose is now durable state shaping future decisions with nobody in the loop.

---

## What we learned running it on fifteen repos

Finance, news, deep research, resume bots, content pipelines. 150 model call sites, 60
register rows, 10 held by nothing. Five things came up again and again.

**Capability decides more than controls do.** Three content-generation repos looked
identical on paper. All three are safe, and none of them because of a control — no publish
client exists in any of them. One has a `ready_to_upload` flag that nothing consumes.
Another's prompts insist the piece is "ready for publication" on a platform it has no client
for. *"This repo cannot send anything"* is worth ten paragraphs about output validation.

**A model reviewing a model is not review.** Four of fifteen had one. The clearest was an
evaluation agent asked to score its own work 1–10 inside its own prompt, where no code ever
read the score. heldby never credits this, and neither should you.

**Names lie in both directions.** One repo had six files in `src/agents/` that call no model
at all, including `risk_management_agent` — pure arithmetic. Another looked AI-free in
TypeScript because every model call was in Rust. Inventory by call site, not by name.

**The gap is usually missing wiring, not a gate.** One repo has an unauthenticated ACH
endpoint with no amount cap. Nothing reaches it — because no model in the repo has
tool-calling. That's an accident, not a control, and the register says so.

**Some of the worst findings aren't about AI.** One repo pools every visitor's API key into
a process-global and spends it for strangers. Another writes model prose into HTML with
unescaped `str.replace`. Both surfaced from a sweep looking for model calls.

---

## "Held by" — the load-bearing column

Name the specific control between the model's output and the effect. It has to survive an
auditor reading the code next to it.

**Good** — you could go and check each one, and a specific code change would falsify it:

- *Sign alignment blocks a debit matching a credit; totals are recomputed from the invoice
  rows rather than read from the model; only a high-confidence match inside the configured
  threshold auto-allocates.*
- *Ranks a closed list harvested deterministically by code — never proposes an address
  itself. Any address it returns that wasn't harvested is discarded.*
- *Three outcomes only, and every failure path defaults to the one that escalates.*

**Not controls**, however often they're offered as one:

| Claimed | Why not |
|---|---|
| "We have guardrails" | Names nothing. |
| An AI gateway or proxy | Meters and logs. It doesn't stand between output and effect. |
| A moderation or judge model | A model holding a model. |
| A retry | A differently wrong answer. |
| The model's own confidence score | Self-reported. |
| "The prompt tells it not to" | Not a mechanism. |
| A schema binding | Only if you check what happens when it fails. A silent fallback to free text removes the guarantee. |

If nothing holds it, write nothing. Not "under review", not "planned".

---

## The commands

Discovery, linting and emission are deterministic and gate a build with an exit code.
Classification needs a model reading code, so it lives in the skill and its result is a
reviewed artefact. CI checks that artefact is current; it never re-infers it.

| Command | What it does | Model? |
|---|---|---|
| `heldby scan <repo>` | Every candidate model call site, every protected action near it, and what it couldn't see | no |
| `heldby context <repo>` | The code a classifier needs per site — enclosing function, protected actions in the file, one-hop callees — ranked worst-first | no |
| *(the skill)* | Reads those packets and assigns class, reach and `held_by` | **yes** |
| `heldby register` | Renders the register (`--out-md`, `--out-json`, `--out-html`) plus an independent completeness check | no |
| `heldby lint <repo>` | Fails the build on a model call outside the designated gateway module | no |
| `heldby adopt <repo>` | Writes declarations and the gate into the repo, so the next run is declared not guessed | no |
| `heldby catalog` | Prints the detection surface; `--check-registries` resolves every package name | no |

### Discovery is tuned for recall

It emits candidates marked `confirmed` or `inferred` and expects the classify pass to clear
the false positives. An over-flagged site costs a reviewer a minute; a wrongly-cleared one
hides exactly the risk this exists to find, behind a clean report.

Two failure modes it will hit on real code, and reports rather than hides:

- **The factory.** Provider SDKs confined to a few client-construction files while the
  actual AI features live in modules importing no SDK at all. Both are reported, neither is
  linked — so trust the self-labelled process names over the call sites.
- **The provider registry.** A dozen vendors behind one `openai` import, separated only by
  `base_url` values in a config table. The model id is usually a runtime value, so "which
  model" is often legitimately unanswerable. The register says so rather than guessing.

### The completeness check

Every register carries one. An independent sweep, blind to the table, looks for AI the
register misses — and reports any file with a model call that no row claims. Label coverage
asks whether every name you found is written down; this asks whether every place a model
runs is accounted for, which is the question that catches a process you forgot entirely.

### Graduating off inference

`heldby adopt` writes the findings back as declarations plus a lint gate, so a new AI call
can't ship without declaring what it is — it won't compile. It **ratchets**: one gateway
module is designated and every existing bypass is baselined, so the gate is green on day one
and can only tighten. It generates the *typed wrapper*, not just the declaration, because
the load-bearing part is the `feature: AIFeature` parameter rather than the list.

---

## What it is not

- **Not taint analysis**, and it must never imply it is. Reachability is a reported claim
  with a confidence and a tier, never a proof.
- **Not a model-accuracy evaluator.** Accuracy is the wrong axis.
- **Not a prompt-injection scanner** — though it flags where untrusted input reaches a
  prompt, because that's reach.
- **Not a compliance certification, and not legal advice.**

Every finding carries a confidence, and every report states in plain words what it couldn't
see. If a sweep skips a language or excludes tests, it says so with counts — silent caps
read as "covered everything".

---

## The catalogue

Detection is data, not prose baked into a prompt, so you can extend it by pull request
without touching any logic.

```bash
uvx heldby catalog                     # the entire detection surface
uvx heldby catalog --check-registries  # resolve every name against npm and PyPI
```

Two rules make it trustworthy.

**Imports and registries are different things.** `imports` is what appears in an import
statement; `registry` is what a package index knows the distribution as. They genuinely
differ — the Google SDK is `@google/genai` on npm, `google-genai` on PyPI, `google.genai` in
a Python import. Conflating them reports "no AI detected" over a Python LangChain codebase.

**The catalogue only grows.** A rule is never deleted and its match never narrowed; a
superseded package is marked `deprecated: true` and keeps matching, because repos on the old
SDK still exist and a deleted rule is a silent blind spot. That's not theoretical: one repo
in our sweep is detected *only* by a deprecated-SDK rule.

A nightly job checks every catalogued name against npm and PyPI and opens a PR for what it
finds. It may only add rules — a gate refuses anything that removes or narrows one, because
that change needs a person.

Rules live in [`src/heldby/catalog/`](src/heldby/catalog/). New frameworks and providers are
the most useful contribution you can send.

---

## Who built this, and why

heldby comes out of [receipting.ai](https://receipting.ai), which reconciles insurance
premium-trust-account payments. About twenty AI processes across six repos, several of which
touch money.

We had to answer this for real, in front of an auditor, with a contract requiring us to
notify a customer before adding any new AI service. Three attempts got us here:

1. **A grep sweep.** Missed an entire repository.
2. **A call-graph reach analyser.** It graded a well-known agent that shells out to the host
   as safe, because the agent reached its model through an interface and the call graph
   severed there — and its own uncertainty instrumentation reported zero doubt at exactly
   the point it had gone blind. It also had no Python support, which is why a script calling
   a provider outside the gateway went unnoticed for months.
3. **Declaring it in the code**, with a lint gate that fails the build when an AI call ships
   without a declaration. That's what we run now.

The lesson: **for code you control, declaring and enforcing beats inferring.** Inference is
the right tool for a repo you don't control — which is exactly where you are when you first
point this at a codebase. So heldby infers first, then offers to write the findings back as
declarations, so the next run is declared rather than guessed.

The other lesson, from the register itself: a live incident auto-allocated four wrong
invoices because the code trusted a total the model had added up. Totals are now recomputed
from the invoice rows. That one sentence is what a "held by" column is for, and no accuracy
score would ever have surfaced it.

MIT licensed. Copyright © 2026 Managed Functions Pty Ltd.
