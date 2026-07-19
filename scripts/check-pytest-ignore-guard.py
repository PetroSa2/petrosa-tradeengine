#!/usr/bin/env python3
"""Guard the pytest ``--ignore`` escape hatch in service CI workflows (#976 AC1/AC4).

Context
-------
Petrosa service repos call the reusable ``ci-pipeline.yml`` and pass
``pytest-extra-args`` containing ``--ignore=tests/<file>`` entries. Because CI is
effectively the *sole* automated merge gate (``required_approving_review_count: 0``
on several services), anyone can silence a failing test by adding one line to that
ignore list. This script is the machine-checkable half of the #976 governance
decision: it fails when a test file is silenced without an audit-trail entry, and
it *hard*-fails when a file on the "must never be ignored" allowlist appears in the
ignore list.

It is intentionally repo-portable: point ``--workflow`` at any caller workflow
(e.g. ``.github/workflows/ci-checks.yml``) and ``--policy`` at that repo's guard
policy file. This lets ``petrosa_k8s`` own the tool while each service wires it into
its own CI in a follow-up sub-ticket.

Policy file (YAML)
------------------
::

    # .github/pytest-ignore-guard.yaml
    documented_ignores:          # audit trail — every ignored file needs a reason
      tests/test_api.py: "flaky network fixture, tracked in #NNN"
    must_not_ignore:             # AC4 — orphan/OCO/watchdog suites must always run
      - tests/test_orphan_resilience.py
      - tests/test_oco_placement.py
      - tests/test_watchdog.py

Exit codes
----------
* ``0`` — every ignored file is documented and no protected file is ignored.
* ``1`` — a protected (must-not-ignore) file is in the ignore list, OR an ignored
  file has no audit-trail entry. (Blocking.)
* ``2`` — invocation / configuration error (bad paths, malformed policy).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is in requirements-dev
    yaml = None  # type: ignore[assignment]

# Matches --ignore=<path> and --ignore <path> (both pytest-accepted forms).
_IGNORE_RE = re.compile(r"--ignore(?:=|\s+)(\S+)")


def extract_ignored_paths(workflow_text: str) -> list[str]:
    """Return every path passed via ``--ignore`` anywhere in the workflow text.

    We scan the raw text rather than parsing the YAML structure because
    ``pytest-extra-args`` is a folded scalar (``>-``) whose ``--ignore`` tokens
    span multiple physical lines; a flat regex over the joined text is both
    simpler and robust to formatting.
    """
    # Strip trailing YAML line-continuation artifacts and collapse whitespace so a
    # folded block scalar reads as one logical string.
    flattened = " ".join(workflow_text.split())
    return [m.group(1).rstrip(",") for m in _IGNORE_RE.finditer(flattened)]


def load_policy(policy_path: Path) -> tuple[dict[str, str], set[str]]:
    """Load documented-ignores map and must-not-ignore set from a policy file."""
    if yaml is None:
        raise RuntimeError("PyYAML is required to parse the policy file")
    data = yaml.safe_load(policy_path.read_text()) or {}
    documented = data.get("documented_ignores") or {}
    if not isinstance(documented, dict):
        raise ValueError("documented_ignores must be a mapping of path -> reason")
    must_not = data.get("must_not_ignore") or []
    if not isinstance(must_not, list):
        raise ValueError("must_not_ignore must be a list of paths")
    return {str(k): str(v) for k, v in documented.items()}, {str(p) for p in must_not}


def check(
    ignored: list[str],
    documented: dict[str, str],
    must_not_ignore: set[str],
) -> list[str]:
    """Return a list of violation messages. Empty list == clean."""
    violations: list[str] = []
    for path in ignored:
        if path in must_not_ignore:
            violations.append(
                f"PROTECTED: '{path}' is on the must-not-ignore allowlist "
                f"(orphan/OCO/watchdog suites must always run) but appears in "
                f"--ignore. Remove it from the ignore list."
            )
        elif path not in documented:
            violations.append(
                f"UNDOCUMENTED: '{path}' is ignored but has no audit-trail entry "
                f"in the guard policy's documented_ignores. Add a reason (and a "
                f"tracking issue) before silencing this test."
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow",
        required=True,
        help="Path to the caller CI workflow (e.g. .github/workflows/ci-checks.yml)",
    )
    parser.add_argument(
        "--policy",
        required=True,
        help="Path to the guard policy YAML (documented_ignores + must_not_ignore)",
    )
    args = parser.parse_args(argv)

    workflow_path = Path(args.workflow)
    policy_path = Path(args.policy)

    if not workflow_path.is_file():
        print(f"error: workflow file not found: {workflow_path}", file=sys.stderr)
        return 2
    if not policy_path.is_file():
        print(f"error: policy file not found: {policy_path}", file=sys.stderr)
        return 2

    try:
        documented, must_not_ignore = load_policy(policy_path)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ignored = extract_ignored_paths(workflow_path.read_text())
    violations = check(ignored, documented, must_not_ignore)

    if violations:
        print(
            f"pytest-ignore-guard: {len(violations)} violation(s) in {workflow_path}:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(
        f"pytest-ignore-guard: OK — {len(ignored)} ignored file(s), all documented, "
        f"no protected file silenced ({workflow_path})."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
