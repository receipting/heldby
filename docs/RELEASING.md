# Releasing heldby to PyPI

Publishing uses **Trusted Publishing (OIDC)**. There is no API token in this repo, in its
secrets, or in anyone's password manager: GitHub proves its own identity to PyPI for the
duration of the job. Nothing to store, nothing to rotate, nothing to leak.

That is the whole reason to prefer it. A long-lived PyPI token that can publish under an
organisation's name is precisely the credential you do not want sitting in a settings page
for three years, and "we'll downscope it later" never happens.

## One-time setup

Steps 1–3 need a person with the PyPI account. They cannot be automated and should not be:
they are account creation and publisher authorisation.

### 1 · The PyPI account

Create an account at <https://pypi.org/account/register/> if there isn't one, and enable 2FA
(PyPI requires it for publishing).

Optionally create an **organisation** so the package is owned by receipting rather than by an
individual: <https://pypi.org/manage/organizations/>. Worth doing — it survives someone
leaving. Org accounts for companies are a paid tier; an individual owner with a second
maintainer added is a fine interim answer, and is strictly better than a single owner.

### 2 · Add a pending publisher for PyPI

`heldby` does not exist on PyPI yet, so this is a **pending** publisher — it authorises the
first upload, which then creates the project.

Go to <https://pypi.org/manage/account/publishing/> and add:

| Field | Value |
|---|---|
| PyPI project name | `heldby` |
| Owner | `receipting` |
| Repository name | `heldby` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

The environment name is not optional cosmetics. It scopes the trust to that GitHub
environment, so a workflow in this repo that is *not* the release job cannot publish even if
someone adds one.

### 3 · Add a pending publisher for TestPyPI

Same again at <https://test.pypi.org/manage/account/publishing/>, with environment name
`testpypi`. Separate account and separate registration — TestPyPI is a different service.

Skip this only if you are content for the first real publish to be the first publish anyone
has ever attempted.

### 4 · GitHub environments

The two environments already exist on the repo. On `pypi`, consider adding yourself as a
**required reviewer** (Settings → Environments → pypi → Required reviewers). That turns
publishing into something a human approves in the moment, which is the right posture for an
irreversible action: PyPI does not allow re-uploading a version you have deleted.

## Publishing

**Rehearse first.** Actions → `release` → Run workflow. That builds, tests, and publishes to
TestPyPI. Then check it actually installs from a clean environment:

```bash
uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ heldby --version
```

**Then the real thing.** Bump `version` in `pyproject.toml`, commit, and tag:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

The workflow refuses to publish if the tag and the version disagree, so
`pip install heldby==0.1.0` can only ever be the commit `v0.1.0` points at.

## What the build asserts before publishing

- The test suite passes. Publishing a package whose tests fail is how a broken release
  reaches people who trusted the version number.
- **The catalogue is inside the wheel.** The detection rules are YAML data files in the
  package. If they do not ship, `heldby scan` finds nothing at all — while still importing
  cleanly, so no other check catches it. This is the one packaging failure that would be
  both silent and total.
- The git tag matches the declared version.

## History

`0.1.0` published 30 July 2026 — the first release, and the one that created the project on
PyPI. The pending publisher became a real one at that point, so `docs` and the README now use
the short `uvx heldby` form.
