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
    _completeness,
    _unattributed_sites,
)
from .scan import ScanReport

#: Print-first. A4 with a real margin, because the common destination is a PDF
#: someone reads on a phone or hands to an auditor — not a web page.
CSS = """
:root { --ink:#1a1a24; --muted:#6b6b80; --rule:#e2e2ea; --accent:#5b3df5; --gap:#b3261e; }
* { box-sizing:border-box; }
body { margin:0; padding:32px 28px 56px; color:var(--ink); background:#fff;
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  max-width:52rem; margin-inline:auto; }
h1 { font-size:26px; line-height:1.2; margin:0 0 4px; letter-spacing:-.02em; }
h2 { font-size:17px; margin:34px 0 10px; padding-bottom:5px; border-bottom:1px solid var(--rule);
  color:var(--accent); letter-spacing:-.01em; }
h3 { font-size:14px; margin:22px 0 8px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--muted); }
p, li { margin:0 0 10px; }
ol, ul { padding-left:20px; margin:0 0 12px; }
code { font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; background:#f4f4f8;
  padding:1px 4px; border-radius:3px; }
strong { font-weight:650; }
table { border-collapse:collapse; width:100%; margin:0 0 14px; font-size:13.5px; }
th { text-align:left; background:#f4f4f8; font-weight:650; font-size:12px;
  text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
th, td { padding:7px 9px; border-bottom:1px solid var(--rule); vertical-align:top; }
td:first-child { white-space:nowrap; }
/* The scope table's third column was collapsing to a ribbon: with auto layout the
   browser hands width to whichever column has the longest unbreakable run, and the
   Item column is all code. Fixed layout and explicit widths instead — 20/40/40, so
   the two prose columns get equal room and the identifier column takes what it
   needs. */
table.scope { table-layout:fixed; }
table.scope td:first-child { white-space:normal; }
table.scope th:nth-child(1), table.scope td:nth-child(1) { width:20%; }
table.scope th:nth-child(2), table.scope td:nth-child(2) { width:40%; }
table.scope th:nth-child(3), table.scope td:nth-child(3) { width:40%; }
/* A long identifier must wrap rather than force a column wider than its share. */
table.scope code { overflow-wrap:anywhere; word-break:break-word; }
.banner { background:#fff8e6; border:1px solid #f0d999; border-radius:6px;
  padding:10px 13px; margin:0 0 18px; font-size:13.5px; }
.classes { list-style:none; padding:0; margin:0 0 12px; }
.classes li { padding:5px 0 5px 11px; border-left:3px solid var(--accent); margin-bottom:5px; }
.lede { color:var(--muted); margin-bottom:18px; }
.gap { color:var(--gap); font-weight:650; }
.dagger { color:var(--muted); }
.note { font-size:12.5px; color:var(--muted); }
footer { margin-top:36px; padding-top:12px; border-top:1px solid var(--rule);
  font-size:12px; color:var(--muted); }
@media print {
  @page { size:A4; margin:16mm 14mm; }
  body { padding:0; max-width:none; font-size:10.5pt; }
  h2 { break-after:avoid; page-break-after:avoid; }
  h3 { break-after:avoid; page-break-after:avoid; }
  tr, li { break-inside:avoid; page-break-inside:avoid; }
  .banner { break-inside:avoid; }
  a { color:inherit; text-decoration:none; }
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


def render_html(register: Register, scans: dict[str, ScanReport]) -> str:
    generated = register.generated or date.today().strftime("%-d %B %Y")
    drafted = register.drafted
    processes = register.sorted_processes()
    files = sum(r.files_scanned for r in scans.values())
    models = sum(len(r.model_sites) for r in scans.values())
    actions = sum(len(r.action_sites) for r in scans.values())

    o: list[str] = []
    add = o.append
    add("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    add('<meta name="viewport" content="width=device-width,initial-scale=1">')
    add(f"<title>AI register — {html_mod.escape(register.org)}</title>")
    add(f"<style>{CSS}</style></head><body>")

    add(f"<h1>AI register — {html_mod.escape(register.org)}"
        f"{' — DRAFT' if drafted else ''}</h1>")
    if drafted:
        add(f'<div class="banner">Machine-drafted. {len(drafted)} of '
            f"{len(register.processes)} rows are unreviewed, marked †. Reviewing a row "
            "removes the mark; rewording it doesn't.</div>")

    add("<p>Ask one question first: <strong>when this is wrong, does anyone outside "
        "the organisation find out?</strong></p><ul class=\"classes\">")
    for key in TIER_ORDER:
        add(f"<li><strong>{TIER_LABEL[key]}</strong> — {TIER_LINE[key]}</li>")
    add("</ul>")
    add("<p>Each tier splits in two, by what the model is doing:</p><ul class=\"classes\">")
    for key in CLASS_ORDER:
        add(f"<li><strong>{CLASS_LABEL[key]}</strong> ({TIER_LABEL[CLASS_TIER[key]]}) "
            f"— {CLASS_LINE[key]}</li>")
    add("</ul>")
    add('<p class="lede">Risk rises left to right: Read &lt; Converse &lt; Decide &lt; Send. '
        "A process spanning two classes gets the stricter one.</p>")

    if register.summary.strip():
        add(f"<p>{_md(register.summary)}</p>")
    add(f"<p>The sweep read {files} files and found {models} model call sites and "
        f"{actions} protected-action call sites.</p>")

    add("<table><thead><tr><th>Process</th><th>Class</th><th>Held by</th></tr></thead><tbody>")
    for p in processes:
        mark = ' <span class="dagger">†</span>' if p.source == "drafted" else ""
        cell = ('<span class="gap">nothing</span>' if not p.has_control else _md(p.cell))
        add(f"<tr><td><code>{html_mod.escape(p.name)}</code>{mark}</td>"
            f"<td>{CLASS_LABEL.get(p.ai_class, p.ai_class)}</td><td>{cell}</td></tr>")
    add("</tbody></table>")
    if drafted:
        add('<p class="note">† machine-drafted, unreviewed.</p>')
    gaps = [p for p in processes if not p.has_control]
    if gaps:
        add(f'<p><span class="gap">{len(gaps)} process(es) have nothing holding them.</span> '
            "The gap is the finding.</p>")
    crossed = [p for p in processes if p.contradicts_tier]
    if crossed:
        add(f'<p><span class="gap">{len(crossed)} process(es) are filed as staying inside '
            "the organisation while reaching something outside it.</span> Either the class "
            "is wrong or the reach list is, and which one it is changes what has to be "
            "built:</p><ul>")
        for p in crossed:
            reaches = ", ".join(f"<code>{html_mod.escape(r)}</code>" for r in p.reaches)
            add(f"<li><code>{html_mod.escape(p.name)}</code> — filed "
                f"<strong>{CLASS_LABEL.get(p.ai_class, p.ai_class)}</strong> "
                f"({TIER_LABEL[p.tier]}), reaches {reaches}</li>")
        add("</ul>")

    if register.key_findings:
        add("<h2>Worth knowing</h2><ol>")
        for finding in register.key_findings:
            add(f"<li>{_md(finding)}</li>")
        add("</ol>")

    add("<h2>The detail</h2>")
    for key in CLASS_ORDER:
        group = [p for p in processes if p.ai_class == key]
        if not group:
            continue
        add(f"<h3>{CLASS_LABEL[key]} — {len(group)}</h3>")
        for p in group:
            mark = ' <span class="dagger">†</span>' if p.source == "drafted" else ""
            add(f"<p><strong><code>{html_mod.escape(p.name)}</code></strong>{mark} · "
                f"{html_mod.escape(p.model)} · {html_mod.escape(p.repo)}</p>")
            if p.does:
                add(f"<p>{_md(p.does)}</p>")
            if p.reaches:
                add(f'<p class="note">Reaches: {html_mod.escape(", ".join(p.reaches))}</p>')
            held = _md(p.held_by) if p.has_control else '<span class="gap">Nothing.</span>'
            add(f"<p><em>Held by:</em> {held}</p>")

    if register.protected_actions:
        add("<h2>What this repo can and can't do</h2><ul>")
        for action, prose in register.protected_actions.items():
            add(f"<li><strong>{html_mod.escape(action)}</strong> — {_md(prose)}</li>")
        add("</ul>")

    if register.scope_notes or register.excluded:
        add("<h2>Scope</h2>")
        for note in register.scope_notes:
            add(f"<p>{_md(note)}</p>")
        if register.excluded:
            add("<p>Outside scope, named rather than dropped:</p>")
            add('<table class="scope"><thead><tr><th>Item</th><th>What it is</th>'
                "<th>Why it's out</th>"
                "</tr></thead><tbody>")
            for item in register.excluded:
                add(f"<tr><td><code>{html_mod.escape(str(item.get('name', '?')))}</code></td>"
                    f"<td>{_md(str(item.get('what', '')))}</td>"
                    f"<td>{_md(str(item.get('why', '')))}</td></tr>")
            add("</tbody></table>")

    undeclared, unfound = _completeness(register, scans)
    unattributed = _unattributed_sites(register, scans)
    add("<h2>Completeness check</h2>")
    add("<p>An independent sweep, blind to the table above, looked for AI this register "
        "misses.</p>")
    if undeclared:
        add(f'<p><span class="gap">{len(undeclared)} process(es) in the code are not in this '
            "register:</span> " + ", ".join(f"<code>{html_mod.escape(n)}</code>"
                                            for n in undeclared) +
            ". That is a register defect.</p>")
    else:
        add("<p><strong>No undeclared AI process found.</strong> Every name the code uses is "
            "in the table or the out-of-scope list.</p>")
    if unattributed:
        add(f"<p>{len(unattributed)} file(s) contain a model call no row claims:</p><ul>")
        for path in unattributed:
            add(f"<li><code>{html_mod.escape(path)}</code></li>")
        add("</ul>")
    if unfound:
        add(f"<p>{len(unfound)} row(s) could not be located from the code alone (" +
            ", ".join(f"<code>{html_mod.escape(n)}</code>" for n in unfound) +
            ") — usually a naming mismatch.</p>")

    add("<h2>What this can't see</h2><ul>")
    for limit in _aggregate_limits(scans):
        add(f"<li>{_md(limit)}</li>")
    add("</ul>")

    digests = sorted({f"{p['id']}@{p['digest'][:8]}"
                      for r in scans.values() for p in r.provenance})
    add(f"<footer>Generated {generated} by heldby. Not taint analysis, not a certification, "
        f"not legal advice.<br>Detection surface: {html_mod.escape(', '.join(digests))}."
        "</footer>")
    add("</body></html>")
    return "\n".join(o)
