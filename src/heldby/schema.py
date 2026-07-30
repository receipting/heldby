"""The catalogue vocabulary, and the rules for what a rule may say.

Every vocabulary here is CLOSED and validated at load time. That is deliberate:
the moment a catalogue entry can carry free prose, the catalogue becomes a style
guide. Descriptive prose belongs in a register row's `held_by`, which a person or
a model writes about a specific call site — never in the detection data.

Two ideas in here are worth reading before adding a rule.

**Imports and registries are different things.** `imports` is what appears in an
import statement and is what the scanner matches. `registry` is what a package
index knows the distribution as, and is what the nightly freshness job resolves.
They genuinely differ: the Google SDK is `@google/genai` on npm, the distribution
`google-genai` on PyPI, and the module `google.genai` in a Python import. A single
`package` field cannot express that, and a catalogue that assumed npm names would
report "no AI detected" over a Python codebase built on LangChain.

**The catalogue only grows.** A rule is never deleted and its match is never
narrowed; a superseded package is marked `deprecated: true` and keeps matching,
because repos on the old SDK still exist and a deleted rule is a silent blind
spot. Renames are therefore additive, which is what makes the nightly job safe to
merge unattended — see `heldby catalog --assert-additive`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- closed vocabularies ----------------------------------------------------

#: What kind of nondeterminism a root introduces. Only `model` is AI; the rest
#: are carried because a register that cannot see a clock or a random number
#: cannot explain why a "deterministic" gate is not one.
KINDS = frozenset({"model", "random", "clock", "network", "io", "env"})

#: Which ecosystem an import specifier belongs to. Drives the scanner.
ECOSYSTEMS = frozenset({"ts", "py"})

#: Which package index a name can be resolved against. Drives freshness.
REGISTRIES = frozenset({"npm", "pypi"})

#: Protected actions — things worth protecting a model's output from reaching.
#: The base vocabulary. A target repo may declare its own additions in its
#: `heldby.yml`; the set is closed within any single run so that it stays a
#: vocabulary rather than a place to write reassurance.
ACTIONS = frozenset(
    {
        "move-money",
        "external-comms",
        "alter-recipients",
        "alter-records",
        "grant-access",
        "delete-data",
        "execute-code",
        "execute-code-sandboxed",
        "write-file",
        "publish-public",
        "decide-about-person",
        "deploy",
    }
)

#: How bad an unfettered instance of the action is. Two levels on purpose: a
#: finer scale invites arguing about the level instead of about the gap.
SEVERITIES = frozenset({"critical", "high"})

#: Suggested fixes a sink rule may offer. Closed, and checked against the
#: action: `sandbox` is only meaningful for code execution.
REMEDIES = frozenset(
    {
        "literal-argv",
        "constant-payload",
        "sandbox",
        "human-release",
        "threshold",
        "closed-list",
        "deterministic-recompute",
    }
)

#: The lethal-trifecta roles, kept from attesting: untrusted input, a secret,
#: and a way out. A rule may name the part it plays.
TRIFECTA_ROLES = frozenset({"untrusted-input", "secret-source", "external-comms"})

REMEDIES_FOR_ACTION: dict[str, frozenset[str]] = {
    "execute-code": frozenset({"literal-argv", "constant-payload", "sandbox"}),
    "execute-code-sandboxed": frozenset({"literal-argv", "constant-payload"}),
}


def normalise_ident(name: str) -> str:
    """Fold an identifier so one rule spans naming conventions.

    `generate_content` and `generateContent` are the same API in two languages.
    Writing both into every rule doubles the catalogue and guarantees drift, so
    identifiers are compared case-folded with separators removed instead.
    """
    return name.replace("_", "").replace("-", "").lower()


# --- the rule ---------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """How a rule recognises a site. Every field is optional; at least one set.

    `url_contains` and `url_literal` are separate because they answer different
    questions. `url_contains` fires on a URL reaching a network call — evidence
    of a live model call over hand-rolled HTTP, which is how a lot of real AI
    ships and what an import-based scan always misses. `url_literal` fires on
    the string appearing anywhere, which is evidence of a *configured provider*:
    fourteen vendors can hide behind one `openai` import, separated only by a
    `base_url` sitting in a dict, and the base URL is the only tell.
    """

    member: tuple[str, ...] = ()
    symbol: tuple[str, ...] = ()
    ambient: str | None = None
    construct: bool = False
    url_contains: tuple[str, ...] = ()
    url_literal: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    #: A regex that must ALSO appear on the matching line. Exists for the case
    #: where a name alone cannot distinguish two very different operations:
    #: `open(p)` reads a file and `open(p, "w")` writes one, and reporting every
    #: read as a write turns the protected-action section into noise.
    requires: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.member,
                self.symbol,
                self.ambient,
                self.construct,
                self.url_contains,
                self.url_literal,
                self.env,
            )
        )


@dataclass(frozen=True)
class Rule:
    """One catalogued signature.

    `imports` maps an ecosystem to the specifiers that appear in import
    statements. `registry` maps a package index to the distribution names the
    freshness job resolves. Both are plural because one logical API often ships
    under several names, and the deprecated ones must keep matching.
    """

    id: str
    kind: str
    match: Match
    imports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    registry: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deprecated: bool = False
    note: str | None = None
    model_arg: str | None = None
    # sink-only
    action: str | None = None
    severity: str | None = None
    payload_arg: int | None = None
    remedies: tuple[str, ...] = ()
    trifecta_role: str | None = None

    def identity(self) -> tuple:
        """The part of a rule the grow-only gate compares.

        `note` and `deprecated` are excluded: annotating a rule or marking a
        package superseded does not change what the rule detects, so the nightly
        job may do both. Anything that changes what is *matched* is a narrowing
        and needs a person.
        """
        return (
            self.id,
            self.kind,
            self.match,
            tuple(sorted((k, v) for k, v in self.imports.items())),
            self.action,
            self.severity,
            self.payload_arg,
            tuple(sorted(self.remedies)),
            self.trifecta_role,
        )
