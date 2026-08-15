"""Flag risky clauses in a contract text file.

Usage:
    python src/classify.py contracts/sample-contract-1.txt
"""

import json
import re
import sys
from pathlib import Path

# The checklist lives at <project root>/checklist/risky-terms.json. Resolving it
# relative to this file (not the current directory) means the script works no matter
# where it is run from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_PATH = PROJECT_ROOT / "checklist" / "risky-terms.json"

# Generated artifact: overwritten on every run, safe to delete.
FINDINGS_PATH = PROJECT_ROOT / "output" / "classify-findings.json"


def load_checklist():
    with open(CHECKLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_contract_text(path):
    """Read the contract and collapse every run of whitespace to a single space.

    The sample contracts are hard-wrapped at ~88 columns, so a phrase like
    "shall automatically renew for successive one-year terms" is split across
    several lines in the file. Without this normalization almost nothing matches.
    """
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    return re.sub(r"\s+", " ", raw)


def find_matches(text, categories):
    """Return [(category, [matched patterns], [matched snippets])] for every category.

    Categories with no hits get empty lists — the caller decides whether to show
    them. The snippets are the substrings as they actually appear in the contract
    (original casing), which is what the JSON report records as matched_text.
    """
    haystack = text.lower()
    results = []
    for category in categories:
        matched, snippets = [], []
        for pattern in category["patterns"]:
            start = haystack.find(pattern.lower())
            if start == -1:
                continue
            matched.append(pattern)
            snippets.append(text[start:start + len(pattern)])
        results.append((category, matched, snippets))
    return results


def build_findings(contract_path, results):
    """Shape the scan results into the JSON report, one entry per category."""
    return {
        "contract": contract_path.name,
        "source": "classify.py",
        "findings": [
            {
                "category_id": category["id"],
                "severity": category["severity"],
                "status": "confirmed" if matched else "not_present",
                "matched_text": "; ".join(snippets) if snippets else None,
            }
            for category, matched, snippets in results
        ],
    }


def write_findings(findings):
    FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FINDINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2)
        f.write("\n")


def print_report(contract_path, results, total_categories):
    print(f"Contract: {contract_path}")
    print(f"Checklist: {CHECKLIST_PATH.name}")
    print()

    flagged = [(category, matched) for category, matched, _ in results if matched]

    if not flagged:
        print("No risky clauses found.")
    else:
        for category, matched in flagged:
            print(f"[{category['severity'].upper()}] {category['name']}")
            print(f"  category id: {category['id']}")
            print(f"  matched {len(matched)} of {len(category['patterns'])} patterns:")
            for pattern in matched:
                print(f"    - \"{pattern}\"")
            print()

    print(f"Summary: {len(flagged)} of {total_categories} categories flagged.")


def main():
    if len(sys.argv) != 2:
        print("Usage: python src/classify.py <contract-file>", file=sys.stderr)
        return 1

    contract_path = Path(sys.argv[1])
    if not contract_path.is_file():
        print(f"Error: no such file: {contract_path}", file=sys.stderr)
        return 1

    checklist = load_checklist()
    categories = checklist["categories"]

    text = load_contract_text(contract_path)
    results = find_matches(text, categories)
    print_report(contract_path, results, len(categories))

    write_findings(build_findings(contract_path, results))
    print(f"Wrote findings to {FINDINGS_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
