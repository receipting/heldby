# How heldby writes

The register is read by developers. Developers don't like reading, and they close
anything that smells like AI. Every sentence heldby produces — the summary, the
findings, every `held_by` — follows this file. So does the tool's own output.

## The shape of the document

- The table is the product. It comes first. Everything else supports it.
- Lead with the load-bearing claim, then stop.
- One idea per sentence. Short sentences. A clipped line next to a longer one —
  uniform sentence length is a machine tell.
- Name the file and the line. "base.py:410 runs pip install" beats a paragraph
  about supply-chain risk.
- If a section won't change what the reader does, cut it.

## Banned words

The stylometric tells. If one of these appears, the sentence gets rewritten:

delve, underscore, nuanced, multifaceted, intricate, crucial, pivotal, foster,
robust, seamless, leverage, streamline, holistic, landscape, journey, navigate
(figurative), realm, testament, showcase, boasts, ever-evolving, comprehensive
(as praise), significant (without a number), "in today's world".

Also banned: firstly/secondly, notably, importantly, it's worth noting,
furthermore, moreover, ultimately, in summary, arguably, perhaps, hopefully.

## Banned shapes

- **The rule of three.** "Fast, safe, and reliable." Say one thing, or say two.
- **"Not just X — it's Y."** Say what it is.
- **The dramatic setup.** No rhetorical questions, no "imagine if".
- **Copula avoidance.** Not "serves as", "is designed to", "acts as". Use *is*,
  *has*, *does*.
- **Em-dash chains.** One per paragraph, to admit a complication. Three is a tell.
- **The tidy closer.** No summary sentence that restates the section. Stop when
  the point is made.
- **Hedging a fact.** A checked claim is stated. An unchecked one says
  "unverified", not "arguably".

## The register's own rules

- A gap says **nothing**. Never "under review", "planned", or "mitigated by
  design". The gap is the finding.
- A capability statement beats a reassurance. "This repo cannot move money" is
  worth ten "payments are secured".
- Credit what you disproved. "We tried to make this scarier and failed" is more
  useful than the scary version, and more credible than silence.
- Every claim a reader could check carries the file and line to check it.
- Limits are stated once, plainly, as bullets. Not as an essay about epistemology.
