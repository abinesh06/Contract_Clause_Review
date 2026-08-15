#!/usr/bin/env python
"""Validate that both review passes produced fresh, complete findings files.

Checks, in order:
  1. output/classify-findings.json exists
  2. output/subagent-findings.json exists
  3. both are valid JSON
  4. both contain an entry for every category in checklist/risky-terms.json
  5. both files are newer than the contract they reviewed
  6. both files reviewed the requested contract (only with an explicit argument)

Usage:
    python .claude/hooks/validate-checklist.py [contract-file]

With no argument, the contract for each findings file is taken from its own
"contract" field, so the two files may legitimately reference different
contracts, and check 6 is skipped. Pass a contract path explicitly to hold both
files to that one: check 5 then measures freshness against it, and check 6
rejects a findings file that was generated for some other contract.

Exit codes: 0 = all checks passed, 1 = a check failed.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHECKLIST_PATH = PROJECT_ROOT / "checklist" / "risky-terms.json"
FINDINGS_PATHS = [
    PROJECT_ROOT / "output" / "classify-findings.json",
    PROJECT_ROOT / "output" / "subagent-findings.json",
]


def fail(check, messages):
    """Print every problem found by one check, then exit non-zero."""
    print(f"CHECK {check} FAILED", file=sys.stderr)
    for message in messages:
        print(f"  - {message}", file=sys.stderr)
    sys.exit(1)


def rel(path):
    """Project-relative path for readable messages, absolute if outside the repo."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_contract(name):
    """Locate a contract from a findings file's "contract" field.

    classify.py writes a bare filename ("sample-contract-1.txt") while the
    subagent writes a repo-relative path ("contracts/sample-contract-1.txt"),
    so try both spellings.
    """
    candidates = [PROJECT_ROOT / name, PROJECT_ROOT / "contracts" / Path(name).name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def check_exist():
    missing = [f"missing: {rel(p)}" for p in FINDINGS_PATHS if not p.is_file()]
    if missing:
        # Checks 1 and 2 are the same test on two files; name whichever is absent.
        fail("1/2 (findings files exist)", missing)


def check_valid_json():
    documents, errors = {}, []
    for path in FINDINGS_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                documents[path] = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{rel(path)} is not valid JSON: {exc}")
    if errors:
        fail("3 (valid JSON)", errors)
    return documents


def check_categories(documents):
    with open(CHECKLIST_PATH, encoding="utf-8") as f:
        expected = [c["id"] for c in json.load(f)["categories"]]

    errors = []
    for path, document in documents.items():
        findings = document.get("findings")
        if not isinstance(findings, list):
            errors.append(f"{rel(path)} has no \"findings\" list")
            continue

        seen = [f.get("category_id") for f in findings if isinstance(f, dict)]
        missing = [c for c in expected if c not in seen]
        if missing:
            errors.append(
                f"{rel(path)} covers {len(set(seen) & set(expected))} of "
                f"{len(expected)} categories; missing: {', '.join(missing)}"
            )
    if errors:
        fail("4 (all checklist categories present)", errors)


def check_freshness(documents, override):
    errors = []
    for path, document in documents.items():
        contract = override
        if contract is None:
            name = document.get("contract")
            if not name:
                errors.append(f"{rel(path)} has no \"contract\" field to check against")
                continue
            contract = resolve_contract(name)
            if contract is None:
                errors.append(f"{rel(path)} names a contract that does not exist: {name}")
                continue

        if path.stat().st_mtime <= contract.stat().st_mtime:
            errors.append(
                f"{rel(path)} is older than {rel(contract)} "
                f"- stale, re-run the review"
            )
    if errors:
        fail("5 (findings newer than contract)", errors)


def check_contract_match(documents, override):
    """Both files must have reviewed the contract that was actually requested.

    Only meaningful with an explicit override: without one there is nothing to
    compare a file's "contract" field against, and the two files are allowed to
    name different contracts.

    Check 5 cannot catch this on its own - given an override it measures mtimes
    against that contract and never looks at the "contract" field, so findings
    left over from a different contract sail through as long as they are recent.
    """
    if override is None:
        return

    # rel() only strips the project prefix off absolute paths; resolving first
    # keeps the requested contract in the same forward-slash spelling as the
    # findings path it is being compared against.
    requested = rel(override.resolve())

    errors = []
    for path, document in documents.items():
        name = document.get("contract")
        if not name:
            errors.append(f'{rel(path)} has no "contract" field to compare against')
            continue

        reviewed = resolve_contract(name)
        if reviewed is None:
            errors.append(f"{rel(path)} names a contract that does not exist: {name}")
            continue

        # samefile() rather than == : it sees through the two spellings
        # resolve_contract() accepts, plus Windows case differences.
        if not reviewed.samefile(override):
            errors.append(
                f"{rel(path)} was generated for {rel(reviewed)}, not "
                f"{requested} - re-run the review for this specific contract"
            )
    if errors:
        fail("6 (findings match the requested contract)", errors)


def main():
    override = None
    if len(sys.argv) > 2:
        print("Usage: python .claude/hooks/validate-checklist.py [contract-file]",
              file=sys.stderr)
        return 1
    if len(sys.argv) == 2:
        override = Path(sys.argv[1])
        if not override.is_file():
            print(f"CHECK 5 FAILED", file=sys.stderr)
            print(f"  - no such contract file: {override}", file=sys.stderr)
            return 1

    check_exist()
    documents = check_valid_json()
    check_categories(documents)
    check_freshness(documents, override)
    check_contract_match(documents, override)

    print(f"All checks passed: {len(FINDINGS_PATHS)} findings files, "
          f"complete and newer than their contracts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
