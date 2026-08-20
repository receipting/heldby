"""Render the register as one self-contained HTML file that prints to PDF.

The register is a document people read, and until this existed heldby emitted
markdown and left the reading surface to whoever ran it. Every artefact this
project has actually put in front of a person was a PDF.

**Why not a PDF writer.** WeasyPrint and its relatives need pango, cairo and
gdk-pixbuf — native libraries, a Homebrew install on macOS, a distro package
list on Linux. heldby has one dependency and runs under `uvx` with nothing
installed in the target repo; the moment producing a report needs a system
toolchain, the tool stops being pointed at unfamiliar codebases, which is the
only thing it is for. A browser is already on every machine and already knows
how to make a PDF.

So: one file, no external requests, a print stylesheet, and Cmd-P. The output
must survive being emailed as a single attachment with no assets alongside it,
which is why the CSS is inline and there are no fonts, images or scripts.

**The tier and the class are the spine.** The register used to print the class
as a bare word in a table cell, which made the one question the framework asks —
does this cross out of the organisation — something a reader had to reconstruct
from a legend further up the page. Every row now carries a chip reading
`Act · Send`, tinted by tier, and the framework block above the table is four
cards in risk order under the two tier headings. Colour does the grouping and the
words do the work, so the document survives being printed in greyscale.
"""

from __future__ import annotations

import html as html_mod
import re
from datetime import date

from .render import (
    CLASS_LABEL,
    CLASS_LINE,
    CLASS_ORDER,
    CLASS_TIER,
    TIER_LABEL,
    TIER_LINE,
    TIER_ORDER,
    Register,
    _aggregate_limits,
    _explained_by_scope,
    _completeness,
    _unattributed_sites,
)
from .scan import ScanReport

#: Print-first. A4 with a real margin, because the common destination is a PDF
#: someone reads on a phone or hands to an auditor — not a web page.
#:
#: Two colour rules hold the palette together. Tier sets the hue — Inform is
#: cool, Act is warm — so the split the whole framework turns on is visible
#: before a word is read. Red is reserved: it means a gap, and nothing else, so
#: that a reader skimming for red finds only the rows that have nothing holding
#: them.
CSS = """
:root {
  --ink:#15161c; --muted:#63657a; --soft:#8a8c9e; --rule:#e4e4ee;
  --panel:#f7f7fb; --panel-line:#ececf4;
  --accent:#4f39d9;
  --gap:#b3261e; --gap-bg:#fdeceb; --gap-line:#f3c9c5;
  --inform:#0e7490; --act:#b45309;
  --read-ink:#0f766e; --read-bg:#effbf8; --read-line:#b6e5dc;
  --converse-ink:#0e7490; --converse-bg:#edf9fd; --converse-line:#b0e1ef;
  --decide-ink:#b45309; --decide-bg:#fff8eb; --decide-line:#f2dcb2;
  --send-ink:#9a3412; --send-bg:#fef2ed; --send-line:#f4ccb7;
}
* { box-sizing:border-box; }
body { margin:0 auto; padding:34px 28px 56px; color:var(--ink); background:#fff;
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  max-width:56rem; }
h1 { font-size:29px; line-height:1.15; margin:0; letter-spacing:-.025em; }
h2 { font-size:17px; margin:38px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--rule);
  color:var(--accent); letter-spacing:-.01em; }
h3 { font-size:12px; margin:24px 0 10px; text-transform:uppercase; letter-spacing:.09em;
  color:var(--muted); font-weight:700; }
p, li { margin:0 0 10px; }
ol, ul { padding-left:20px; margin:0 0 12px; }
code { font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; background:var(--panel);
  padding:1px 4px; border-radius:3px; }
strong { font-weight:650; }
a { color:var(--accent); }

/* --- masthead ------------------------------------------------------------ */
.eyebrow { font-size:11.5px; text-transform:uppercase; letter-spacing:.16em; font-weight:700;
  color:var(--accent); margin:0 0 7px; }
.masthead { border-bottom:2px solid var(--ink); padding-bottom:14px; margin-bottom:20px; }
.subline { color:var(--muted); font-size:13.5px; margin:8px 0 0; }

/* --- the counts strip ---------------------------------------------------- */
.stats { display:flex; flex-wrap:wrap; gap:10px; margin:0 0 22px; padding:0; list-style:none; }
.stat { flex:1 1 8.5rem; border:1px solid var(--rule); border-radius:7px; padding:10px 12px;
  background:var(--panel); }
.stat .n { display:block; font-size:24px; font-weight:700; letter-spacing:-.02em; line-height:1.1; }
.stat .k { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--muted); margin-top:3px; font-weight:650; }
.stat.is-gap { background:var(--gap-bg); border-color:var(--gap-line); }
.stat.is-gap .n, .stat.is-gap .k { color:var(--gap); }

/* --- the framework: two tiers, four classes, in risk order --------------- */
.question { font-size:16px; margin:0 0 14px; }
.tier { border:1px solid var(--rule); border-radius:8px; padding:12px 14px 4px; margin:0 0 12px;
  border-left:4px solid var(--tier-ink,var(--rule)); }
.tier-inform { --tier-ink:var(--inform); }
.tier-act { --tier-ink:var(--act); }
.tier-head { font-size:14px; margin:0 0 10px; }
.tier-head b { color:var(--tier-ink); font-size:15px; letter-spacing:-.01em; }
.tier-head span { color:var(--muted); }
.classes { display:flex; flex-wrap:wrap; gap:10px; padding:0; margin:0 0 10px; list-style:none; }
.klass { flex:1 1 13rem; border:1px solid var(--k-line); background:var(--k-bg);
  border-radius:7px; padding:9px 11px; }
.klass-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px;
  margin-bottom:4px; }
.klass-n { font-size:19px; font-weight:700; color:var(--k-ink); letter-spacing:-.02em; }
.klass-line { font-size:12.5px; line-height:1.45; color:var(--ink); margin:0; }
.k-read { --k-ink:var(--read-ink); --k-bg:var(--read-bg); --k-line:var(--read-line); }
.k-converse { --k-ink:var(--converse-ink); --k-bg:var(--converse-bg); --k-line:var(--converse-line); }
.k-decide { --k-ink:var(--decide-ink); --k-bg:var(--decide-bg); --k-line:var(--decide-line); }
.k-send { --k-ink:var(--send-ink); --k-bg:var(--send-bg); --k-line:var(--send-line); }

/* --- the chip: tier and class in one token ------------------------------- */
.chip { display:inline-block; padding:2px 9px; border-radius:999px; font-size:11.5px;
  font-weight:650; letter-spacing:.01em; white-space:nowrap; border:1px solid var(--k-line);
  background:var(--k-bg); color:var(--k-ink);
  -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.chip .tier-part { opacity:.72; font-weight:600; }

/* --- the table ----------------------------------------------------------- */
table { border-collapse:collapse; width:100%; margin:0 0 14px; font-size:13.5px; }
th { text-align:left; background:var(--panel); font-weight:650; font-size:11px;
  text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }
th, td { padding:8px 9px; border-bottom:1px solid var(--rule); vertical-align:top; }
table.register { table-layout:fixed; }
/* The Class column holds a nowrap chip, and `Inform · Converse` is the longest one
   the vocabulary can produce. Below ~20% it overflows its cell at print size and runs
   into the Held by prose, so the width is set by that chip rather than by the header. */
table.register th:nth-child(1), table.register td:nth-child(1) { width:24%; }
table.register th:nth-child(2), table.register td:nth-child(2) { width:20%; }
table.register th:nth-child(3), table.register td:nth-child(3) { width:56%; }
table.register td:first-child code { overflow-wrap:anywhere; }
table.scope { table-layout:fixed; }
table.scope th:nth-child(1), table.scope td:nth-child(1) { width:20%; }
table.scope th:nth-child(2), table.scope td:nth-child(2) { width:40%; }
table.scope th:nth-child(3), table.scope td:nth-child(3) { width:40%; }
table.scope code { overflow-wrap:anywhere; word-break:break-word; }

/* --- callouts ------------------------------------------------------------ */
.banner { background:#fff8e6; border:1px solid #f0d999; border-radius:7px;
  padding:10px 13px; margin:0 0 18px; font-size:13.5px;
  -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.alarm { background:var(--gap-bg); border:1px solid var(--gap-line); border-left:4px solid var(--gap);
  border-radius:7px; padding:11px 13px; margin:0 0 16px;
  -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.alarm p:last-child, .alarm ul:last-child { margin-bottom:0; }
.clear { background:#f2fbf6; border:1px solid #c3e8d3; border-left:4px solid #17803d;
  border-radius:7px; padding:11px 13px; margin:0 0 16px;
  -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.clear p:last-child { margin-bottom:0; }
.gap { color:var(--gap); font-weight:650; }
.dagger { color:var(--soft); }
.note { font-size:12.5px; color:var(--muted); }
.lede { color:var(--muted); margin-bottom:18px; }

/* --- the detail: one card per pathway ------------------------------------ */
.row { border:1px solid var(--rule); border-radius:8px; padding:12px 14px; margin:0 0 11px;
  border-left:4px solid var(--k-ink,var(--rule)); }
.row-head { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:5px; }
.row-name { font-size:14.5px; font-weight:650; background:none; padding:0; }
.row-meta { font-size:12px; color:var(--muted); margin:0 0 8px; }
.row-meta .sep { color:var(--soft); }
.row-does { margin:0 0 9px; }
.held { border-top:1px dashed var(--rule); padding-top:8px; margin:0; font-size:13.5px; }
.held-label { display:block; font-size:10.5px; text-transform:uppercase; letter-spacing:.09em;
  font-weight:700; color:var(--muted); margin-bottom:3px; }
.held-gap { color:var(--gap); font-weight:650; }

footer { margin-top:38px; padding-top:12px; border-top:1px solid var(--rule);
  font-size:11.5px; color:var(--muted); }

@media print {
  @page { size:A4; margin:15mm 13mm; }
  body { padding:0; max-width:none; font-size:10.2pt; }
  h2 { break-after:avoid; page-break-after:avoid; }
  h3 { break-after:avoid; page-break-after:avoid; }
  tr, li, .row, .klass, .stat, .tier { break-inside:avoid; page-break-inside:avoid; }
  .banner, .alarm, .clear { break-inside:avoid; page-break-inside:avoid; }
  a { color:inherit; text-decoration:none; }
}
@media (max-width:640px) {
  body { padding:22px 16px 40px; }
  .klass, .stat { flex-basis:100%; }
  table.register th:nth-child(1), table.register td:nth-child(1) { width:32%; }
  table.register th:nth-child(2), table.register td:nth-child(2) { width:22%; }
  table.register th:nth-child(3), table.register td:nth-child(3) { width:46%; }
}
"""

_INLINE = (
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"<strong>\1</strong>"),
    (re.compile(r"(?<![`\w])`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)"), r'<a href="\2">\1</a>'),
)


def _md(text: str) -> str:
    """The small subset of markdown that appears in register prose.

    Escaped first, so a stray angle bracket in a code snippet cannot become a
    tag — the register quotes real source lines, and some of them contain HTML.
    """
    out = html_mod.escape(text.strip())
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def _esc(text: str) -> str:
    return html_mod.escape(str(text))


def _chip(ai_class: str) -> str:
    """`Act · Send` — the tier and the class in one token.

    Both words, always. The tint alone would be a legend a reader has to hold in
    their head, and the document has to survive a greyscale printer.
    """
    tier = CLASS_TIER.get(ai_class, "act")
    label = CLASS_LABEL.get(ai_class, ai_class)
    known = "read converse decide send".split()
    kind = ai_class if ai_class in known else "send"
    return (
        f'<span class="chip k-{kind}"><span class="tier-part">{TIER_LABEL[tier]} · </span>'
        f"{_esc(label)}</span>"
    )


def _stat(number: int, label: str, gap: bool = False) -> str:
    cls = "stat is-gap" if gap else "stat"
    return f'<li class="{cls}"><span class="n">{number}</span><span class="k">{_esc(label)}</span></li>'


def render_html(register: Register, scans: dict[str, ScanReport]) -> str:
    generated = register.generated or date.today().strftime("%-d %B %Y")
    drafted = register.drafted
    processes = register.sorted_processes()
    files = sum(r.files_scanned for r in scans.values())
    models = sum(len(r.model_sites) for r in scans.values())
    actions = sum(len(r.action_sites) for r in scans.values())
    by_class = {key: [p for p in processes if p.ai_class == key] for key in CLASS_ORDER}
    by_tier = {key: [p for p in processes if p.tier == key] for key in TIER_ORDER}
    gaps = [p for p in processes if not p.has_control]
    crossed = [p for p in processes if p.contradicts_tier]

    o: list[str] = []
    add = o.append
    add('<!doctype html><html lang="en"><head><meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width,initial-scale=1">')
    add(f"<title>AI register — {_esc(register.org)}</title>")
    add(f"<style>{CSS}</style></head><body>")

    # --- masthead ------------------------------------------------------------
    add('<header class="masthead"><p class="eyebrow">AI register</p>')
    add(f"<h1>{_esc(register.org)}{' — DRAFT' if drafted else ''}</h1>")
    add(f'<p class="subline">Every place a model runs, what class of use it is, and what '
        f"stands between its output and a real-world effect. Generated {_esc(generated)}."
        "</p></header>")

    if drafted:
        add(f'<div class="banner">Machine-drafted. {len(drafted)} of '
            f"{len(register.processes)} rows are unreviewed, marked †. Reviewing a row "
            "removes the mark; rewording it doesn't.</div>")

    # --- the counts ----------------------------------------------------------
    add('<ul class="stats">')
    add(_stat(len(processes), "AI pathways"))
    add(_stat(len(by_tier["act"]), "Act — cross out"))
    add(_stat(len(by_tier["inform"]), "Inform — stay inside"))
    add(_stat(len(gaps), "Held by nothing", gap=bool(gaps)))
    add("</ul>")

    if register.summary.strip():
        add(f"<p>{_md(register.summary)}</p>")

    # --- the framework -------------------------------------------------------
    add("<h2>How each pathway is filed</h2>")
    add('<p class="question">Ask one question first: <strong>when this is wrong, does '
        "anyone outside the organisation find out?</strong></p>")
    for tier in TIER_ORDER:
        add(f'<div class="tier tier-{tier}">')
        add(f'<p class="tier-head"><b>{TIER_LABEL[tier]}</b> — <span>{TIER_LINE[tier]}</span></p>')
        add('<ul class="classes">')
        for key in CLASS_ORDER:
            if CLASS_TIER[key] != tier:
                continue
            add(f'<li class="klass k-{key}"><div class="klass-head">{_chip(key)}'
                f'<span class="klass-n">{len(by_class[key])}</span></div>'
                f'<p class="klass-line">{_md(CLASS_LINE[key])}</p></li>')
        add("</ul></div>")
    add('<p class="lede">Risk rises left to right: Read &lt; Converse &lt; Decide &lt; Send. '
        "A pathway spanning two classes is filed under the stricter one.</p>")

    # --- the table -----------------------------------------------------------
    add("<h2>The register</h2>")
    add(f"<p>The sweep read {files} files and found {models} model call sites and "
        f"{actions} protected-action call sites.</p>")
    add('<table class="register"><thead><tr><th>Pathway</th><th>Class</th><th>Held by</th>'
        "</tr></thead><tbody>")
    for p in processes:
        mark = ' <span class="dagger">†</span>' if p.source == "drafted" else ""
        cell = '<span class="gap">nothing</span>' if not p.has_control else _md(p.cell)
        add(f"<tr><td><code>{_esc(p.name)}</code>{mark}</td>"
            f"<td>{_chip(p.ai_class)}</td><td>{cell}</td></tr>")
    add("</tbody></table>")
    if drafted:
        add('<p class="note">† machine-drafted, unreviewed.</p>')

    if gaps:
        add('<div class="alarm"><p><span class="gap">'
            f"{len(gaps)} pathway(s) have nothing holding them.</span> The gap is the "
            "finding.</p><ul>")
        for p in gaps:
            add(f"<li><code>{_esc(p.name)}</code> — {_chip(p.ai_class)}</li>")
        add("</ul></div>")
    else:
        add('<div class="clear"><p><strong>Every pathway has a named control.</strong> '
            "Each one below points at a mechanism in the code, not at a policy.</p></div>")
    if crossed:
        add(f'<div class="alarm"><p><span class="gap">{len(crossed)} pathway(s) are filed as '
            "staying inside the organisation while reaching something outside it.</span> "
            "Either the class is wrong or the reach list is, and which one it is changes "
            "what has to be built:</p><ul>")
        for p in crossed:
            reaches = ", ".join(f"<code>{_esc(r)}</code>" for r in p.reaches)
            add(f"<li><code>{_esc(p.name)}</code> — filed {_chip(p.ai_class)}, "
                f"reaches {reaches}</li>")
        add("</ul></div>")

    if register.key_findings:
        add("<h2>Worth knowing</h2><ol>")
        for finding in register.key_findings:
            add(f"<li>{_md(finding)}</li>")
        add("</ol>")

    # --- the detail ----------------------------------------------------------
    add("<h2>Every pathway in detail</h2>")
    for tier in TIER_ORDER:
        if not by_tier[tier]:
            continue
        add(f'<div class="tier tier-{tier}"><p class="tier-head"><b>{TIER_LABEL[tier]}</b> '
            f'— <span>{TIER_LINE[tier]}</span></p></div>')
        for key in CLASS_ORDER:
            if CLASS_TIER[key] != tier or not by_class[key]:
                continue
            add(f"<h3>{CLASS_LABEL[key]} — {len(by_class[key])}</h3>")
            for p in by_class[key]:
                mark = ' <span class="dagger">†</span>' if p.source == "drafted" else ""
                add(f'<article class="row k-{key}"><div class="row-head">'
                    f'<code class="row-name">{_esc(p.name)}</code>{mark}{_chip(p.ai_class)}</div>')
                meta = [_esc(p.model), _esc(p.repo)]
                if p.reaches:
                    meta.append("reaches " + ", ".join(f"<code>{_esc(r)}</code>" for r in p.reaches))
                add('<p class="row-meta">' + ' <span class="sep">·</span> '.join(meta) + "</p>")
                if p.does:
                    add(f'<p class="row-does">{_md(p.does)}</p>')
                held = (_md(p.held_by) if p.has_control
                        else '<span class="held-gap">Nothing.</span>')
                add(f'<div class="held"><span class="held-label">Held by</span>{held}</div>')
                add("</article>")

    if register.protected_actions:
        add("<h2>What this code can and can't do</h2>")
        add('<p class="note">Capability, not reach. What the system is able to do at all, '
            "whether or not a model gets near it.</p><ul>")
        for action, prose in register.protected_actions.items():
            add(f"<li><strong>{_esc(action)}</strong> — {_md(prose)}</li>")
        add("</ul>")

    if register.lifecycle:
        add("<h2>What holds the AI as it changes</h2>")
        add('<p class="note">Every row above names what holds one output. This is what holds '
            "the estate: what stops a process appearing that nothing holds. None of these is "
            "credited in the <em>Held by</em> column, because a control there has to stand "
            "between one output and one effect.</p><ul>")
        for leg, prose in register.lifecycle.items():
            add(f"<li><strong>{_esc(leg)}</strong> — {_md(prose)}</li>")
        add("</ul>")

    if register.scope_notes or register.excluded:
        add("<h2>Scope</h2>")
        for note in register.scope_notes:
            add(f"<p>{_md(note)}</p>")
        if register.excluded:
            add("<p>Outside scope, named rather than dropped:</p>")
            add('<table class="scope"><thead><tr><th>Item</th><th>What it is</th>'
                "<th>Why it's out</th></tr></thead><tbody>")
            for item in register.excluded:
                add(f"<tr><td><code>{_esc(item.get('name', '?'))}</code></td>"
                    f"<td>{_md(str(item.get('what', '')))}</td>"
                    f"<td>{_md(str(item.get('why', '')))}</td></tr>")
            add("</tbody></table>")

    # --- completeness --------------------------------------------------------
    undeclared, unfound = _completeness(register, scans)
    unattributed = _unattributed_sites(register, scans)
    add("<h2>Completeness check</h2>")
    add("<p>An independent sweep, blind to the table above, looked for AI this register "
        "misses.</p>")
    if undeclared:
        add(f'<div class="alarm"><p><span class="gap">{len(undeclared)} pathway(s) in the code '
            "are not in this register:</span> "
            + ", ".join(f"<code>{_esc(n)}</code>" for n in undeclared)
            + ". That is a register defect.</p></div>")
    else:
        add('<div class="clear"><p><strong>No undeclared AI pathway found.</strong> Every name '
            "the code uses is in the table or the out-of-scope list, and every file with a "
            "model call in it is claimed by a row.</p></div>")
    if unattributed:
        explained = {p: _explained_by_scope(p, register) for p in unattributed}
        # A deliberate, documented exclusion is not a defect and must not render as
        # one — the reader was told what it is two inches up the page.
        box = "clear" if all(explained.values()) else "alarm"
        tail = (", and the Scope section above accounts for each"
                if all(explained.values()) else "")
        add(f'<div class="{box}"><p>{len(unattributed)} file(s) contain a model call no row '
            f"claims{tail}:</p><ul>")
        for path in unattributed:
            why = explained[path]
            note = f' — see <em>{_esc(why)}</em> above' if why else ""
            add(f"<li><code>{_esc(path)}</code>{note}</li>")
        add("</ul></div>")
    if unfound:
        add(f"<p>{len(unfound)} row(s) could not be located from the code alone ("
            + ", ".join(f"<code>{_esc(n)}</code>" for n in unfound)
            + ") — usually a naming mismatch.</p>")

    add("<h2>What this can't see</h2><ul>")
    for limit in _aggregate_limits(scans):
        add(f"<li>{_md(limit)}</li>")
    add("</ul>")

    digests = sorted({f"{p['id']}@{p['digest'][:8]}"
                      for r in scans.values() for p in r.provenance})
    add(f"<footer>Generated {_esc(generated)} by heldby. Not taint analysis, not a "
        f"certification, not legal advice.<br>Detection surface: {_esc(', '.join(digests))}."
        "</footer>")
    add("</body></html>")
    return "\n".join(o)
