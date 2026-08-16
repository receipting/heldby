# Verifying — try to refute your own answers

Do not skip this phase. Every defect found in the tool that produced this framework
was found by running it, not by reasoning about it — and the same holds for the
classifications it produces.

**Prompt yourself to refute, not to confirm.** You have just written a set of
classifications. Your incentive now is to find them wrong. A finding that survives
a genuine attempt to kill it is worth ten that were never challenged.

## The two checks that catch most errors

### 1 · Anything Read or Converse near a protected action is misclassified until proven otherwise

Read and Converse are the Inform tier — the classes that claim nothing leaves the
organisation. That makes them where errors accumulate, in both directions honestly:
from wishful thinking, and from a scanner that could not see the path.

**Part of this check is now arithmetic rather than judgement.** A row filed Read or
Converse whose `reaches` list is non-empty is a contradiction on its face, and
`heldby register` reports it above the table without anyone having to notice. Use
that as the floor, not the ceiling: it catches rows that *declared* a reach and kept
the gentle label. It cannot catch the row that under-declared its reach, which is
what the questions below are for.

For every process you classified Read or Converse, ask:

- Does its module also perform a protected action?
- Does anything it returns get passed to a function that does?
- Does anything downstream *treat its output as authoritative* — an id used as a
  lookup key, a total used in arithmetic, a name used to select a recipient?

If yes to any, you must be able to say **specifically** why it is still Read or
Converse. "It only returns data" is not an answer; everything only returns data.
The answer has to be a mechanism: the ids are filtered against the real list, the
totals are recomputed, the address must appear in a deterministically harvested set.

If you cannot name that mechanism, the class is wrong or the `held_by` is empty.
Either is a finding.

### 2 · Every Converse claim gets the three tests run explicitly, one at a time

Do not assess "is this a closed loop" holistically — that is how Converse becomes
the class everything hides in. Run them separately and write down each answer.

**Test 1 — a person started it.** Find the entry point. Is it a request handler
driven by a user action, or is it a cron, a queue consumer, a webhook, a retry
worker, or another service? If you cannot find the entry point, the answer is not
"probably a person".

**Test 2 — it reaches only the asker and the operator.** Trace where the response
goes. A response streamed to the asker's screen *and* written to the operator's
support system is still closed — both are parties to the conversation. A response
that reaches a broker, a counterparty, another tenant, or any address the asker did
not choose is **not**.

**Test 3 — it does nothing.** The one people fail. Look specifically for:

- A write to any store, including a log or a file, that something later reads.
- A row, event or message another process consumes.
- Anything persisted that is served to someone else afterwards.
- **The model's own output being fed back into a later prompt.** This is the
  quiet one. Nothing was sent, no money moved, and yet the model's prose is now
  durable state shaping future decisions with nobody in the loop.

Fail any one and it is **Send**.

## Clear the false positives the sweep is known to produce

The sweep is tuned for recall, so some of what it hands you is not a process at all. These
four recur, and every one of them appeared on the first cold run against a real repository.
Exclude them **with a reason** rather than deleting them — a reader who runs the sweep
themselves will see the same labels and needs to know they were considered.

- **Graph entry and utility nodes.** Node registration is normally the best available list
  of an agent app's real processes, which is exactly why a `start_node` that only seeds
  state comes with it.
- **Dictionary keys and field names.** The sweep reads self-labelling metadata and cannot
  tell a process name from an output key without the surrounding code. You can.
- **UI labels.** A tab or component named after a feature is not that feature.
- **Things named "agent" that call no model.** The worst of the four, because the name and
  the directory both argue for inclusion. One real repository had six of twenty-one files in
  `src/agents/` making no model call at all — including one called
  `risk_management_agent`, which was pure arithmetic. Check for an actual call before
  writing a row; a name-based inventory overstates by a third.

Overstating is not the safe direction here. A register padded with non-AI rows invites the
reader to discount the ones that matter.

## Also refute these

**The class is too kind.** For each process, ask what the worst plausible wrong
output would do. If the answer involves money, a third party, a deletion, an access
grant, or a person's outcome, the class is probably one step too low.

**The `held_by` is unfalsifiable.** Read each one and ask: what specific change to
this codebase would make this sentence false? If nothing would — if it would remain
true no matter what the code did — it is not describing a control. Rewrite or empty
it.

**The `held_by` credits the wrong thing.** Check it against the list of things that
look like controls and are not, in [classifying.md](classifying.md). Gateways,
moderation models, retries, self-reported confidence and prompt instructions are
the common five.

**The model id is invented.** Most real code selects the model at runtime —
`model: this.config.model`, or a provider registry keyed by config. If you wrote a
specific model id, confirm it is actually a literal at that site. If it is not,
write "selected at runtime" and say where the selection happens. Guessing a model
id to fill a column is exactly the kind of quiet fabrication that discredits
everything around it.

**Completeness in the other direction.** The scan gave you candidates. Ask what
class of AI use it *structurally could not* find:

- A model reached through an interface or an injected dependency, where no file
  imports a provider SDK.
- A model call in a language the sweep does not read.
- An AI feature behind a flag, or on an unmerged branch.
- A vendor API that is AI-backed but does not look like it — document
  understanding, OCR, transcription, classification, recommendation, embeddings.
  An insurer running OCR through a cloud service has a Read-class AI use whether or
  not anyone there calls it AI.

Whatever you cannot rule out goes in the limits section, in plain words.

## The standard to hold

A verdict is one of:

- **Confirmed** — you read the code and the classification holds.
- **Plausible** — the shape supports it; the value flow is not proven.
- **Refuted** — the original classification was wrong. Say so and correct it.

Default to the weaker verdict when you are unsure. An over-flagged site costs a
reviewer a minute. A wrongly-cleared site hides exactly the risk this exists to
find, and hides it behind a clean report.

## Finally, write the limits

The report says, in plain words, what the analysis cannot see. Include at minimum:

- That this is **not taint analysis**, and `reaches` is a claim rather than a proof.
- Which languages were read and which were not.
- What was excluded — tests, generated files, nested checkouts — with counts.
  Silent caps read as "covered everything".
- How many candidate sites you actually read, out of how many the scan produced.
- Any process whose class you could not settle, and why.

A proof that names its own limits is the only kind worth publishing.
