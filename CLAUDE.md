# Contract Clause Reviewer

A command-line tool that reads contract text files, checks their clauses against a
configurable "risky terms" checklist, and flags the matches so a reviewer can see
which parts of a contract need a closer look.

Reviewing a contract is a three-step pipeline, not a single command. A fast substring
scan and a reasoning-based semantic pass each write a findings file, and a third script
merges the two into one verdict — `standard` or `needs-legal-review`.

## Status

This is a **learning project**, built session-by-session. The review pipeline is
end-to-end runnable today: all three steps exist, all four directories below are
populated, and `.claude/` wires the whole thing together as a slash command.

Still absent by design: any test suite, any `.gitignore`, and any dependency beyond the
Python standard library. Nothing in the project is committed yet apart from the original
`Test.py` placeholder, which is staged for deletion and is not part of the design.

## Structure

```
contracts/          Four sample contract text files, plain text, hard-wrapped at ~88 cols
checklist/          risky-terms.json — the risky-terms config (5 categories + metadata)
src/                classify.py (pattern scan) and generate-summary.py (merge + verdict)
output/             Generated reports and findings files (safe to delete and regenerate)
answer-key.txt      Hand-verified expected results for every sample contract
.claude/            Subagent, slash command, hooks, and settings that drive the pipeline
CLAUDE.md           This file
```

## The review pipeline

Three steps, in order. Each consumes what the previous one wrote, so they cannot be run
in parallel or out of order. `.claude/commands/route.md` (`/route <contract-filename>`)
is the wrapper that runs all three.

**1. Pattern scan** — `python src/classify.py contracts/sample-contract-1.txt`

Case-insensitive substring matching of the checklist's `patterns[]` against the contract.
Overwrites `output/classify-findings.json`. Fast, exact, and blind to rewording.

The contracts are hard-wrapped, so most triggering phrases straddle a line break in the
file. `classify.py` collapses every whitespace run to a single space before matching —
without that normalization almost nothing matches.

**2. Semantic pass** — the `clause-analyzer` subagent (`.claude/agents/clause-analyzer.md`)

Judges each category on *spirit and intent* rather than exact wording, using the
category's `description` as its guide, to catch reworded risky clauses step 1 misses. It
has only `Read` and `Grep` and **cannot write files** — its entire response is a bare
JSON object, and saving that verbatim to `output/subagent-findings.json` is the caller's
job. Anything wrapped in a code fence or padded with prose is rejected downstream.

Its statuses are `confirmed`, `possible`, and `not_present`. `possible` means the
analyzer was unsure and does not flag a category on its own.

**3. Merge into a verdict** — `python src/generate-summary.py sample-contract-1.txt`

Applies the **union rule**: a category is flagged if *either* findings file reports it as
`confirmed`. Writes `output/review-summary-<contract-name>.md` with the verdict, the
flagged categories, which pass confirmed each, and a plain-English explanation.

### The argument formats differ on purpose

`classify.py` takes a **path** (`contracts/sample-contract-1.txt`) and exits 1 if it is
not a real file. `generate-summary.py` takes a **bare filename**
(`sample-contract-1.txt`). Do not normalize these to match each other.

## The checklist

`checklist/risky-terms.json` — version 1, 5 categories, each with an `id`, `name`,
`severity` (`high` or `medium`), a prose `description`, and a `patterns[]` list.

| id | severity |
| --- | --- |
| `auto-renewal-without-notice` | medium |
| `unlimited-liability` | high |
| `unilateral-termination` | high |
| `exclusivity` | medium |
| `broad-indemnification` | high |

The `matching` block records the contract both passes honor: plain phrases, matched
case-insensitively as substrings, against whitespace-collapsed text. **No regex at this
stage** — if a pattern needs regex, that is a checklist-format change, not a quiet
addition.

Note that `description` is load-bearing, not a comment: it is what the semantic pass
reasons against. A new category with a thin description will underperform in step 2.

## Sample contracts and the answer key

Each sample exists to exercise something specific, and `answer-key.txt` records the
hand-verified expected result for each, with the exact triggering language and the
reasoning. It is the ground truth for judging whether a change to the checklist or the
matcher helped or regressed.

| file | expected | role |
| --- | --- | --- |
| `sample-contract-1.txt` | 3 categories | Vendor-favorable. Auto-renewal, unlimited liability, broad indemnification. |
| `sample-contract-2.txt` | 1 (borderline) | Mostly balanced, with one narrow consent-based exclusivity clause a human would likely accept. |
| `sample-contract-3.txt` | 0 | The all-clean negative control. Any hit here is a false positive. |
| `sample-contract-4.txt` | 1 category | Balanced SaaS terms with one deliberately abusive termination section — the only positive case for `unilateral-termination`. |

The samples also plant **intentional near-misses** that must not match: "arising out of
or relating to" against the pattern "arising out of or in any way related to",
"non-exclusive" containing "exclusive" as a substring, and mutual termination rights
against unilateral ones. When changing the matcher, re-run all four contracts — a change
that improves recall on contract 1 often breaks contract 3.

`answer-key.txt` also flags one **known checklist gap**, unfixed on purpose: contract 1's
Section 9.1/9.2 asymmetry (Provider capped, Client uncapped) has no category, so it is
expected *not* to be flagged.

## `.claude/` — what enforces the pipeline

- `commands/route.md` — the `/route` slash command; runs all three steps in order.
- `agents/clause-analyzer.md` — the step 2 subagent. Tools: `Read`, `Grep`. Model: sonnet.
- `hooks/validate-checklist.py` — standalone CLI. Six checks over the two findings files:
  both exist, both are valid JSON, both cover every checklist category, both are newer
  than the contract, and (given an explicit contract argument) both actually reviewed
  *that* contract. Exit 0 pass, 1 fail.
- `hooks/pretooluse-route-gate.py` — wraps the validator as a `PreToolUse` gate on step 3,
  translating its exit 1 into the exit 2 that actually blocks a tool call. Wired in
  `settings.json` with `"if": "Bash(python src/generate-summary.py*)"`, so it **matches on
  that command text** — rewording the command, adding flags, or wrapping it in another
  shell invocation silently bypasses the gate. Fails **open** on an unparseable payload.
- `hooks/clause-analyzer-read-gate.py` — armed from the subagent's own frontmatter, so it
  applies only while that subagent runs. Blocks `Read` and `Grep` calls that would reach
  `answer-key.txt`, including a `Grep` with no path (which defaults to the project root,
  where the key lives). Fails **closed**. A semantic pass that has seen the key is not an
  independent second opinion.

The two gates deliberately fail in opposite directions: the route gate would rather let a
step through than block on a malformed payload, while the read gate would rather break
loudly than leak the answer key.

## Conventions

- Input contracts are plain text files under `contracts/`.
- The checklist lives in `checklist/` as JSON so it can be edited without touching code.
  When adding a risky term, add it to the JSON rather than hardcoding it in `src/`.
- Anything written to `output/` is a generated artifact — never a source of truth.
- Keep the CLI runnable from the project root. Both scripts resolve their paths relative
  to the file, not the working directory, so they work from anywhere — but the documented
  invocations assume the root and the hook matcher depends on that spelling.
- Standard library only. Ask before adding a dependency or a new top-level directory —
  the shape above is deliberate and minimal.
- Prefer small, self-contained changes that leave the tool in a runnable state.
