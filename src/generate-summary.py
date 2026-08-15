"""Merge both review passes into one verdict and write a Markdown summary.

Reads the two findings files the review passes produce:
    output/classify-findings.json   written by src/classify.py
    output/subagent-findings.json   the clause-analyzer subagent's JSON, saved

Union rule: a category is flagged if EITHER file reports status "confirmed" for
it. The subagent's third status, "possible", is deliberately not enough on its
own - it means the analyzer was unsure, and an unsure hit should not by itself
route a contract to legal.

Usage:
    python src/generate-summary.py sample-contract-2.txt

Exit codes: 0 = summary written, 1 = a findings file is missing or malformed.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_PATH = PROJECT_ROOT / "checklist" / "risky-terms.json"
OUTPUT_DIR = PROJECT_ROOT / "output"

# (label used in the report, that pass's findings file)
SOURCES = [
    ("classify.py", OUTPUT_DIR / "classify-findings.json"),
    ("subagent", OUTPUT_DIR / "subagent-findings.json"),
]

CONFIRMED = "confirmed"


def die(message):
    print(f"generate-summary: {message}", file=sys.stderr)
    sys.exit(1)


def rel(path):
    """Project-relative path for readable messages, absolute if outside the repo."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def plural(count, singular, plural_form):
    return f"{count} {singular if count == 1 else plural_form}"


def load_document(path):
    if not path.is_file():
        die(f"missing findings file: {rel(path)} - run that review pass first")
    try:
        # utf-8-sig, not utf-8: subagent-findings.json is saved by hand, and on
        # Windows that often means a UTF-8 BOM (PowerShell's Out-File adds one).
        # utf-8-sig strips a BOM if present and is identical to utf-8 if not.
        with open(path, encoding="utf-8-sig") as f:
            document = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        die(f"{rel(path)} is not valid JSON: {exc}")
    if not isinstance(document, dict):
        die(f"{rel(path)} should be a JSON object, not a {type(document).__name__}")
    return document


def parse_findings(path, document):
    """Return (every category id seen, {confirmed category id: severity}).

    The full id set is only needed as a fallback denominator for "N of M
    categories" when the checklist itself can't be read.
    """
    findings = document.get("findings")
    if not isinstance(findings, list):
        die(f'{rel(path)} has no "findings" list')

    seen, confirmed = set(), {}
    for position, entry in enumerate(findings):
        if not isinstance(entry, dict):
            die(f"{rel(path)}: findings[{position}] is not an object")
        category_id = entry.get("category_id")
        if not isinstance(category_id, str) or not category_id:
            die(f'{rel(path)}: findings[{position}] has no "category_id"')
        seen.add(category_id)
        if entry.get("status") == CONFIRMED:
            confirmed[category_id] = entry.get("severity")
    return seen, confirmed


def load_checklist():
    """[(id, display name)] in checklist order.

    Optional enrichment: it supplies readable names, a stable ordering, and the
    category total. If it can't be read the summary still builds from the
    findings files alone, just with bare ids.
    """
    try:
        with open(CHECKLIST_PATH, encoding="utf-8") as f:
            return [(c["id"], c["name"]) for c in json.load(f)["categories"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return []


def build_paragraph(contract_name, verdict, flagged, total):
    """The plain-English "what this means" paragraph."""
    if verdict == "standard":
        return (
            f"{contract_name} is routed to **standard**. Neither review pass "
            f"confirmed any of the {total} checklist categories, so nothing on "
            f"the current checklist calls for a lawyer's time. Read this as a "
            f"checklist result rather than a legal opinion: it means no known "
            f"risky pattern matched, not that the contract carries no risk."
        )

    high = [item for item in flagged if item["severity"] == "high"]
    both = [item for item in flagged if item["source"] == "both"]
    single = [item for item in flagged if item["source"] != "both"]

    sentences = [
        f"{contract_name} is routed to **needs-legal-review** because "
        f"{len(flagged)} of {total} checklist categories came back confirmed."
    ]
    if high:
        names = ", ".join(item["name"] for item in high)
        sentences.append(f"{len(high)} of them are high severity: {names}.")
    if both and single:
        sentences.append(
            f"Both passes agreed on "
            f"{plural(len(both), 'category', 'categories')}, while "
            f"{plural(len(single), 'category', 'categories')} came from only "
            f"one pass - under the union rule a single confirmation is enough "
            f"to flag, since the two passes look for different things."
        )
    elif both:
        sentences.append(
            "Both the pattern scan and the clause-analyzer subagent agreed on "
            "every flagged category."
        )
    else:
        sentences.append(
            "Each flagged category was confirmed by only one of the two passes; "
            "under the union rule one confirmation is enough to flag, since the "
            "two passes look for different things."
        )
    sentences.append(
        "Someone from legal should read the flagged clauses in full, in context, "
        "before this is signed."
    )
    return " ".join(sentences)


def build_markdown(contract_name, verdict, flagged, total):
    lines = [
        f"# Review summary: {contract_name}",
        "",
        f"- **Contract:** {contract_name}",
        f"- **Verdict:** {verdict}",
        f"- **Flagged:** {len(flagged)} of {total} checklist categories",
        "",
        "## Flagged categories",
        "",
    ]
    if flagged:
        for item in flagged:
            severity = item["severity"] or "unknown"
            lines.append(
                f"- **{item['name']}** (`{item['id']}`) - severity {severity} "
                f"- confirmed by: {item['source']}"
            )
    else:
        lines.append("_None. Neither pass confirmed any checklist category._")
    lines += [
        "",
        "## What this means",
        "",
        build_paragraph(contract_name, verdict, flagged, total),
        "",
        "---",
        "",
        "Generated by `src/generate-summary.py` from "
        "`output/classify-findings.json` and `output/subagent-findings.json`. "
        "A category is flagged when either pass reports it as confirmed; the "
        "subagent's `possible` status does not flag on its own.",
        "",
    ]
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print("Usage: python src/generate-summary.py <contract-filename>",
              file=sys.stderr)
        return 1

    # Accept either a bare filename or a path - the two findings files disagree
    # about which one they record, so compare on the filename alone.
    contract_name = Path(sys.argv[1]).name
    stem = Path(contract_name).stem
    if not stem:
        die(f"not a usable contract filename: {sys.argv[1]}")

    checklist = load_checklist()
    names = dict(checklist)

    seen_ids, passes = set(), []
    for label, path in SOURCES:
        document = load_document(path)
        reviewed = document.get("contract")
        if reviewed and Path(reviewed).name != contract_name:
            # Not fatal - just make it impossible to misread the summary as
            # covering a contract this pass never looked at.
            print(f"generate-summary: warning: {rel(path)} reviewed "
                  f"{Path(reviewed).name}, not {contract_name}", file=sys.stderr)
        found, confirmed = parse_findings(path, document)
        seen_ids |= found
        passes.append((label, confirmed))

    flagged_ids = set()
    for _, confirmed in passes:
        flagged_ids |= confirmed.keys()

    # Checklist order first, then anything the checklist doesn't know about.
    ordered = [cid for cid, _ in checklist if cid in flagged_ids]
    ordered += sorted(flagged_ids - set(names))

    flagged = []
    for category_id in ordered:
        labels = [label for label, confirmed in passes if category_id in confirmed]
        severity = next(
            (confirmed[category_id] for _, confirmed in passes
             if confirmed.get(category_id)),
            None,
        )
        flagged.append({
            "id": category_id,
            "name": names.get(category_id, category_id),
            "severity": severity,
            "source": "both" if len(labels) == len(SOURCES) else labels[0],
        })

    total = len(checklist) if checklist else len(seen_ids)
    verdict = "needs-legal-review" if flagged else "standard"

    output_path = OUTPUT_DIR / f"review-summary-{stem}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(build_markdown(contract_name, verdict, flagged, total))

    print(f"Verdict: {verdict} ({len(flagged)} of {total} categories flagged)")
    print(f"Wrote summary to {rel(output_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
