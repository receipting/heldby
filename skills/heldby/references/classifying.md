# Classifying a model call site

Read this before classifying. The classes are defined by **the gate, not the
technology**: two call sites using the same model on the same document belong to
different classes if what they can reach differs.

## The four classes

| Class | What the model does | The rule |
|---|---|---|
| **Read** | Turns a document or message into structured data | No person required — but the output must be checkable against something real: a column that exists in the file, an account already on file, a total that reconciles. **If it cannot be checked against the world, it is not Read.** |
| **Decide** | Proposes an action with a consequential real-world effect | No person on the fast path *by design*. Bounded by deterministic gates plus a configured threshold; everything outside the threshold goes to a queue a person works. |
| **Converse** | Answers the person who asked — them and the operator, nobody else | No separate review, because the person who asked is the person who judges the answer, as they read it. Only valid if the loop is genuinely closed — three tests below. |
| **Write** | Produces prose the system will carry to someone else | A named person edits and releases it, and the record says who. Nothing AI-written leaves unattended. |

**Where a process spans two classes, the stricter class governs.** Strictness
order: **Read < Converse < Decide < Write**.

### Why four and not two

Input/output is the obvious split and it breaks on the first hard case. A
payment-matching engine reads nothing a person will see and writes nothing a
person will read — **and it moves money**. File it under input and you have
classified the only money-moving process as low risk. File it under output and you
have committed to a human reviewing every allocation, which is the entire job the
software exists to remove.

## The closed-loop test — Converse only

Converse is the class people abuse, because "it's just a chatbot, the user reads
it" is the easiest way to dodge review. **All three must hold:**

1. **A person started it** — not a cron, not a queue, not another system.
2. **It reaches only the asker and the operator.** Both parties to the
   conversation may hold it: the asker reads the answer, and the operator may keep
   and read it (support tickets, logs, quality review). What breaks the loop is a
   **third** party the asker did not address.
3. **It does nothing.** No payment, no send, no write to a system of record, **no
   row another process later acts on.**

The line between Converse and Write is **who carries the output onward**. If a
person carries it, they reviewed it by definition — reading it *is* the review. If
the system carries it, stores it to serve later, or routes it to a third party,
that is Write.

**Fail any one test and it is Write.** A class you can file into to dodge review is
worse than no class at all.

### Test 3 is the one that catches people

Watch for the model's own output being persisted and then fed back into a later
prompt. A memory or reflection loop looks harmless — nothing was sent anywhere, no
money moved — but the model's prose is now durable state shaping future decisions
with no person in the loop. That is a row another process acts on. It is Write.

Real example: an agent writes its decision to an append-only log; a later run reads
resolved entries back into its prompt as "past lessons". Nobody would self-report
that as an AI risk. It fails test 3 twice over.

## Reachability tiers — report the tier honestly

You have no call graph. Say which tier you are claiming:

- **Direct** — the model output is passed to a protected call in the same function.
- **Module** — the call site's module also performs a protected action.
- **One hop** — a called function performs one.
- **Unknown** — beyond that. **Say so.** "Unknown" is a legitimate answer and is
  far better than a guess dressed as a finding.

## Protected actions

`held_by` only means something relative to an action worth protecting. Discover
this codebase's own rather than assuming a standard set.

| Action | Typical signature |
|---|---|
| Moves money or creates a financial obligation | payment/ledger SDKs, transfer and refund calls, invoice writes, order placement |
| Sends a message to a third party | email/SMS/push/chat SDKs, webhook posts |
| Writes to a system of record | DB writes, ERP/CRM/accounting API writes |
| Grants or changes access | IAM calls, role and permission mutations, token minting |
| Deletes data | destructive DB/storage operations |
| Executes code or commands | `eval`, `exec`, shell spawn, dynamic import, sandboxes |
| Publishes public content | CMS/social/site publish calls, merging to a default branch |
| Makes a consequential decision about a person | credit, hiring, claims, moderation — carries regulatory weight |

The last two often have **no signature at all** and must come from you reading the
code. A register containing only what a scanner can pattern-match is a register of
the AI a scanner recognises, not of the AI in the system.

**Capability is not reach.** "This codebase cannot move money at all" and "the
model is gated away from money" are very different claims, and only one is usually
true. Establish which.

## Writing `held_by`

This is the load-bearing column. It has to survive an auditor reading the code next
to it.

### Good — specific, checkable, falsifiable

> Sign alignment blocks a debit matching a credit; totals are recomputed from the
> invoice rows rather than read from the model; only a high-confidence match inside
> the configured threshold auto-allocates, and everything else goes to a human
> queue.

> Every column it names must exist in the file; the mapping is cached per header
> layout and reused rather than re-guessed.

> Ranks a closed list harvested deterministically by code — never proposes an
> address itself. Any address it returns that was not harvested is discarded.

> Three outcomes only, and every failure path defaults to the one that escalates.

Each one names a mechanism you could go and check. Each one would be *falsified* by
a specific change to the code.

### Bad — unfalsifiable

- "We have guardrails in place." — names nothing.
- "The model is highly accurate for this task." — wrong axis entirely.
- "Prompt engineering ensures correct output." — not a control.
- "All outputs are validated." — validated against *what*?
- "A human reviews the results." — which human, at which step, recorded where? If
  there is no record of who, there is no control.
- "Requests go through our AI gateway." — a gateway meters and logs. It does not
  stand between the output and the effect.
- "A moderation model screens the output." — that is a model holding a model.

### When nothing holds it

Write nothing. Leave the field empty. The register prints **nothing** in bold, and
that is the most useful row it can carry. Do not write "under review", "planned",
"mitigated by design" or "low risk by nature" — every one of those launders an
unknown into a reassurance, in the one column a reader is relying on.

## Things that look like controls and are not

- **A gateway or proxy.** Metering and logging, not a gate.
- **A moderation or judge model.** A model holding a model.
- **A schema or structured-output binding** — *unless* you check what happens when
  it fails. A fallback to free text when a provider cannot do structured output
  silently removes the guarantee, and that belongs in `held_by`.
- **A retry.** Retrying a wrong answer produces a differently wrong answer.
- **A confidence score the model produced itself.** Self-reported.
- **A sandbox**, when the question is whether code runs at all. A sandbox is a
  smaller blast radius, which is a different action, not a control on the original.
- **A prompt instruction.** "The prompt tells it not to" is not a mechanism.

## Output shape

Write one entry per process — not per call site:

```yaml
processes:
  - name: matching                 # what the code calls it, from `labels`
    class: decide
    model: claude-sonnet-4-6       # or "selected at runtime" if it genuinely is
    component: simplet
    does: Matches an incoming payment to the invoices it pays.
    reaches: [move-money]
    held_by: >
      Sign alignment blocks a debit matching a credit; totals are recomputed from
      the invoice rows rather than read from the model; only a high-confidence
      match inside the configured threshold auto-allocates.
    confidence: confirmed          # confirmed | inferred | unknown
    tier: direct                   # direct | module | one-hop | unknown
    evidence: app/lib/matching/ai.ts:351
```
