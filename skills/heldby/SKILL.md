---
name: heldby
description: Audit a codebase for every place AI runs, classify each use as Read/Decide/Converse/Write, and name what stands between each model's output and a real-world effect. Produces an AI register for an auditor, a vendor questionnaire, or a trust-centre page. Use when asked to inventory AI usage, build an AI register, assess AI risk or governance, answer "where does AI run in this system", or prepare an AI disclosure.
when_to_use: Point it at any repository — TypeScript/JavaScript and Python are fully supported. Also use it to check an existing AI register is still complete.
allowed-tools: Bash(uv run --directory ${CLAUDE_SKILL_DIR}/../.. heldby *), Read, Grep, Glob
---

# heldby — find the AI, name what holds it

You are producing an **AI register**: a table naming every place a model runs in
this codebase, what class of use it is, and — the column that matters — the
specific control between the model's output and a real-world effect.

The framework, in one line: **stop grading the model, start naming what's in the
way.** Accuracy is the wrong axis, because the model will be wrong at a rate
nobody can drive to zero. The useful question is what it can reach when it is.

## The four phases

Run them in order. Phase 2 is the one that needs you rather than a scanner.

### 1 · Discover — deterministic, already built

```bash
uv run --directory ${CLAUDE_SKILL_DIR}/../.. heldby scan <repo> --json
```

This is catalogue-driven and needs no dependencies installed in the target. It is
tuned for **recall, not precision**: it emits candidates and marks them
`confirmed` or `inferred`. Expect false positives and clear them by reading code.

Read three things out of the output before anything else:

- `labels` — what the code calls its own AI features, from its observability
  metadata and graph-node names. **These, not file paths, are the register's rows.**
  One shared `invoke` helper can serve a dozen agents.
- `bypass_candidates` — a model reached outside a gateway the repo otherwise
  routes through. Usually the most actionable finding in the whole run.
- `limits` — what the sweep could not see. This travels into your final report
  verbatim. Never drop it.

Also note `dependencies` with `imported_anywhere: false` — an AI SDK in the
manifest that nothing imports is a loose end worth a line.

### 2 · Classify — this is your job

For each candidate site, **read the surrounding code** and decide:

1. Which of the four classes it is.
2. What it can reach.
3. **What holds it** — the specific, named control.

"The totals are recomputed from the invoice rows rather than read from the model"
is not a pattern match. It is reading code. A scanner cannot do this, which is why
the phase exists.

→ **Read [references/classifying.md](references/classifying.md) before you start.**
It carries the class definitions, the three closed-loop tests, the reachability
tiers, and worked examples of good and bad `held_by` text.

### 3 · Verify — try to refute your own answers

Do not skip this. It catches more errors than phase 2 produces confidence.

→ **Read [references/verifying.md](references/verifying.md).** Two checks catch
most mistakes: anything you classified Read or Converse that sits within reach of
a protected action is misclassified until proven otherwise, and every Converse
claim gets the three closed-loop tests run against it explicitly, one at a time.

### 4 · Report, and optionally adopt

Write the classification to a YAML file, then render the register:

```bash
uv run --directory ${CLAUDE_SKILL_DIR}/../.. heldby register \
  --classification <file>.yml --out-md ai-register.md --out-json ai-register.json
```

The renderer will not invent a control and will not soften a missing one: an empty
`held_by` prints as **nothing**, in bold. That is correct — leave it empty when
nothing holds it.

Add `--layout sections` for anything destined for print or PDF; the six-column
table is for a screen.

If the user owns this code and wants to stop guessing, offer `adopt`:
→ [references/adopting.md](references/adopting.md)

## The honesty rules — non-negotiable

These are the reason anyone will trust the output. Breaking one makes the whole
artefact worthless, because a register nobody can check is a brochure.

- **Every finding carries a confidence.** `confirmed` — you read the code and are
  sure. `inferred` — the shape is there, the value flow is not proven. `unknown` —
  say so. Never present inference as fact.
- **State the edge.** Say in plain words what the analysis could not see. This is
  **not taint analysis** and must never imply it is. `reaches` is a claim.
- **No silent truncation.** If you looked at 12 of 40 sites, say 12 of 40. A
  partial sweep reported as a complete one is worse than no sweep.
- **A gap is a finding, not a failure.** `held_by: nothing` is the most useful row
  a register can contain. Never soften it to "under review" or "planned".
- **Do not credit a gateway as a control.** A gateway meters and logs. It does not
  stand between the output and the effect.
- **Do not credit a moderation model as a control.** That is a model holding a
  model.

## Anti-goals — say these out loud in the report

- Not a model-accuracy evaluator.
- Not a prompt-injection scanner — though flag where untrusted input reaches a
  prompt, because that is reach.
- Not a compliance certification, and not legal advice.
- Not taint analysis, and it must never imply it is.

## Scale to the ask

"Is there any AI in here?" → phase 1, read the labels, answer in a paragraph.

"Build our AI register" → all four phases, every site, a written register.

"We have an audit / a vendor questionnaire" → all four phases plus the verify pass
run properly, and offer `adopt` so the answer stays true without another sweep.

## Two failure modes to expect

**The factory.** Provider SDKs confined to a few client-construction files while
the actual AI features live in modules that import no SDK at all. The scan reports
both and links neither. Trust the labels over the call sites.

**The provider registry.** A dozen vendors behind one `openai` import, separated
only by `base_url` values in a config table. The base URL is the only tell, and
the model id is frequently a runtime value — so "which model" is often
legitimately unanswerable. Write that rather than guessing one.
