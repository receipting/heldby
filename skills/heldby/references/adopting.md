# Adopting — stop inferring, start declaring

Offer this only when the user **owns** the code and can mandate something about it.

Inference is the right tool for a repo you do not control — which is exactly where
you are on the first run. For your own code, **declaring and enforcing beats
inferring**: it is complete, it works in every language, and it fails on the exact
line instead of reporting "no path found".

That is not a preference, it is a result. The predecessor of this tool measured
reach from the call graph and was retired because for owned code it lost on every
axis: it could not see an in-app assistant whose client was built by a factory and
passed to another module, it had no Python support (which is how a script calling a
provider directly, outside the gateway everything was supposed to route through,
went unnoticed for months), and when it was wrong it said "no path found" rather
than naming a line.

## What it does

```bash
uv run --directory ${CLAUDE_SKILL_DIR}/../.. heldby adopt <repo>
```

Writes three files into the repo:

- **`ai-register.ts`** or **`ai_register.py`** — the declaration. Vendored, not a
  package: no install, no registry to authenticate against, no version to keep in
  step. A shared dependency is the part of this that would rot.
- **`heldby.yml`** — one designated gateway module, plus the scope of the gate.
- **`.heldby-baseline.json`** — every pre-existing bypass, accepted deliberately.

Then `heldby lint` fails the build on a model call made anywhere but the gateway
module.

## The two design choices worth understanding

### It generates the wrapper, not just the declaration

The load-bearing part is **not** the list of processes. It is that every call site
must pass a `feature` argument whose type is `keyof typeof AI_PROCESSES`:

```ts
export const AI_PROCESSES = defineProcesses({ /* … */ })
export type AIFeature = keyof typeof AI_PROCESSES

export function modelMeta(feature: AIFeature) { /* … */ }
```

So the chain is: the lint gate means the only place a model client exists is the
gateway module → the gateway module's entry points take `feature: AIFeature` →
`AIFeature` is the union of declared names → **you cannot reach a model without
adding a declaration.** It will not compile.

Without the wrapper, none of that holds and the declaration is a comment that
happens to typecheck. If you hand-roll this, the typed parameter is the part you
must not skip.

### It ratchets rather than gating

A gate that fails on forty pre-existing call sites the day it lands gets deleted
the same day. So `adopt` designates **one** gateway module and records every other
model call in the baseline. The gate passes immediately, and the baseline can only
shrink.

Note what it deliberately does *not* do: promote every file that currently calls a
model to be a gateway module. That would make the gate green instantly and
meaningless — it green-lights the bypasses by renaming them, and then reports
"every model call is made in the gateway" about a repo with calls scattered across a
dozen files.

## After it runs — none of this is optional

1. **Fill in `does` and `heldBy`.** They are generated **empty**, on purpose. An
   empty control renders as **nothing** in the register, which is the honest state
   of a process nobody has written a control for. Do not fill it with "TODO" or
   "under review" — leave it empty until there is a real mechanism to name.
2. **Check every `class`.** They are all stubbed `read`, which is wrong for
   anything that can reach a protected action.
3. **Route each baselined call through the gateway module**, passing the typed
   feature, then delete its baseline entry. This is the ratchet tightening.
4. **Add `heldby lint` to CI.**

## Then the register generates itself

Once declarations are filled in, the register is produced from the code rather than
guessed, and the next audit is a regeneration rather than a project:

```bash
uv run --directory ${CLAUDE_SKILL_DIR}/../.. heldby register \
  --classification heldby-estate.yml --out-md ai-register.md
```

Point `heldby register` at a classification file whose content mirrors the
declarations. The sweep still runs, with declarations hidden from it, so the
register carries a completeness claim — "we swept the code and found no undeclared
AI process" — rather than only an inventory.

That converts "tell us before you add an AI service" from something an annual sweep
discovers into a compile error, and it is the only part of this that keeps being
true without anyone remembering to re-run it.

## What to warn the user about

- **The lint gate exempts the gateway module entirely.** The one file that may
  reach a model is the one file with no gate on it. That is deliberate and it is
  also the residual risk; say so rather than letting it be discovered.
- **A declaration is a claim.** `reaches: []` records what is known, and cannot
  find a path nobody knew about. The lint gate narrows that; it is not taint
  analysis and must not be described as any.
- **The vocabulary of protected actions is closed on purpose.** An open one becomes
  a place to write reassuring prose. If a genuinely new action is needed, add it to
  the generated type deliberately — do not widen the field to `string`.
