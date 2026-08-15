# Contract Clause Reviewer — Technical Documentation

**Version:** 1.0 · **Generated:** 2026-08-15 · **Checklist schema:** v1 (updated 2026-08-14)
**Runtime:** Python 3.14.6, standard library only · **Platform verified on:** Windows 11 / PowerShell

---

## Table of contents

1. [What this project is](#1-what-this-project-is)
2. [Architecture](#2-architecture)
3. [Repository layout — file by file](#3-repository-layout--file-by-file)
4. [The review pipeline in detail](#4-the-review-pipeline-in-detail)
5. [Data contracts (file formats)](#5-data-contracts-file-formats)
6. [The checklist](#6-the-checklist)
7. [The `.claude/` control plane](#7-the-claude-control-plane)
8. [Validation and the two gates](#8-validation-and-the-two-gates)
9. [Test corpus and ground truth](#9-test-corpus-and-ground-truth)
10. [Design decisions and their rationale](#10-design-decisions-and-their-rationale)
11. [Failure modes and error handling](#11-failure-modes-and-error-handling)
12. [Operations: running, extending, debugging](#12-operations-running-extending-debugging)
13. [Known gaps and current state](#13-known-gaps-and-current-state)
14. [Glossary](#14-glossary)

---

## 1. What this project is

A command-line tool that reads a plain-text contract, checks its clauses against a
configurable "risky terms" checklist, and routes the contract to one of two verdicts:

| Verdict | Meaning |
| --- | --- |
| `standard` | No checklist category was confirmed by either review pass. |
| `needs-legal-review` | At least one category was confirmed. A human lawyer should read it. |

The defining architectural idea is that **one review pass is not enough**. A substring
matcher is fast and exact but blind to rewording; a language model is good at rewording
but can hallucinate a match. So the tool runs both, independently, and merges them under
a **union rule** — a category is flagged if *either* pass confirms it.

This is a learning project built session by session. It is end-to-end runnable today.

### Non-goals

- It is not a legal opinion. The summary text says so explicitly.
- It does not parse contract structure (sections, parties, defined terms). It works on
  flat normalized text.
- It has no test suite, no dependencies beyond the standard library, and no database.

---

## 2. Architecture

### 2.1 Component diagram

```
                          ┌────────────────────────────┐
                          │  checklist/risky-terms.json│
                          │  5 categories:             │
                          │   id, name, severity,      │
                          │   description, patterns[]  │
                          └──────┬──────────────┬──────┘
                                 │              │
                    patterns[]   │              │  description
                    (exact)      │              │  (semantic)
                                 ▼              ▼
   contracts/         ┌────────────────┐  ┌──────────────────────┐
   sample-*.txt ─────▶│ STEP 1         │  │ STEP 2               │
        │             │ src/classify.py│  │ clause-analyzer      │
        └────────────▶│ substring scan │  │ subagent (LLM, Read+ │
                      │ deterministic  │  │ Grep only, no write) │
                      └───────┬────────┘  └──────────┬───────────┘
                              │                      │
                              ▼                      ▼
              output/classify-findings.json   (JSON in the response;
                              │                the caller saves it)
                              │                      │
                              │                      ▼
                              │        output/subagent-findings.json
                              │                      │
                              └──────────┬───────────┘
                                         │
                          ╔══════════════▼═══════════════╗
                          ║ PreToolUse gate              ║
                          ║ pretooluse-route-gate.py     ║
                          ║   └─▶ validate-checklist.py  ║
                          ║       6 checks, fails OPEN   ║
                          ╚══════════════╤═══════════════╝
                                         │ (exit 0 = allow)
                                         ▼
                              ┌──────────────────────┐
                              │ STEP 3               │
                              │ generate-summary.py  │
                              │ UNION RULE + verdict │
                              └──────────┬───────────┘
                                         ▼
                     output/review-summary-<contract-name>.md
```

### 2.2 Layers

| Layer | Contents | Property |
| --- | --- | --- |
| **Configuration** | `checklist/risky-terms.json` | Data, not code. Edited without touching `src/`. |
| **Input** | `contracts/*.txt` | Read-only plain text, hard-wrapped ~88 cols. |
| **Deterministic engine** | `src/classify.py`, `src/generate-summary.py` | Pure stdlib Python. Reproducible. |
| **Reasoning engine** | `.claude/agents/clause-analyzer.md` | LLM subagent. Non-deterministic by nature. |
| **Control plane** | `.claude/commands/`, `.claude/hooks/`, `.claude/settings.json` | Orchestration + enforcement. |
| **Artifacts** | `output/**` | Generated. Never a source of truth. Safe to delete. |
| **Ground truth** | `answer-key.txt` | Hand-verified expected results. *(See §13 — currently absent from disk.)* |

### 2.3 Two independent passes, deliberately asymmetric

|  | Step 1 — pattern scan | Step 2 — semantic pass |
| --- | --- | --- |
| Implementation | Python, `str.find()` | LLM subagent (model: sonnet) |
| Reads from checklist | `patterns[]` | `description` (primary), `patterns[]` (examples) |
| Deterministic | Yes | No |
| Cost / latency | Milliseconds | Seconds, one model call |
| Statuses emitted | `confirmed`, `not_present` | `confirmed`, `possible`, `not_present` |
| Can write files | Yes (`output/classify-findings.json`) | **No** — tools are `Read`, `Grep` only |
| Failure mode | False negatives on reworded clauses | False positives on plausible-sounding text |

The mismatch in statuses is intentional: only the semantic pass can be *unsure*, and its
`possible` status is deliberately **not enough to flag on its own**.

---

## 3. Repository layout — file by file

```
Contract_Clause_Review/
├── CLAUDE.md                          Project instructions for Claude Code
├── TECHNICAL_DOCUMENTATION.md         This file
├── answer-key.txt                     Ground truth (see §13 — missing on disk)
│
├── checklist/
│   └── risky-terms.json               85 lines. v1. 5 categories + matching metadata.
│
├── contracts/
│   ├── sample-contract-1.txt          185 lines. Master Services Agreement. Vendor-favorable.
│   ├── sample-contract-2.txt          206 lines. Professional Services Agreement. Borderline.
│   ├── sample-contract-3.txt          233 lines. Consulting Services Agreement. Clean control.
│   └── sample-contract-4.txt          228 lines. Software Subscription Agreement. Termination case.
│
├── src/
│   ├── .gitkeep
│   ├── classify.py                    129 lines. Step 1: pattern scan.
│   ├── generate-summary.py            257 lines. Step 3: merge + verdict.
│   └── __pycache__/                   Build residue. Not ignored — see §13.
│
├── output/                            All generated. Safe to delete and regenerate.
│   ├── .gitkeep
│   ├── classify-findings.json         Step 1 output. Overwritten every run.
│   ├── subagent-findings.json         Step 2 output. Written by the caller, not the subagent.
│   ├── review-summary-sample-contract-1.md
│   ├── review-summary-sample-contract-2.md
│   └── review-summary-sample-contract-3.md
│
└── .claude/
    ├── settings.json                  Wires the PreToolUse route gate.
    ├── settings.local.json            Local per-command permission allowlist.
    ├── agents/
    │   └── clause-analyzer.md         Step 2 subagent definition + its own hook.
    ├── commands/
    │   └── route.md                   /route slash command. Runs all three steps.
    └── hooks/
        ├── validate-checklist.py      199 lines. Standalone CLI. Six checks.
        ├── pretooluse-route-gate.py   141 lines. Wraps the validator as a blocking gate.
        └── clause-analyzer-read-gate.py  140 lines. Keeps the subagent off the answer key.
```

### 3.1 `src/classify.py` — step 1

**Purpose:** case-insensitive substring scan of every checklist pattern against the
contract, writing a machine-readable findings file and a human-readable stdout report.

**Invocation:** `python src/classify.py contracts/sample-contract-1.txt` — takes a
**path**; exits 1 if it is not a real file.

| Constant | Value |
| --- | --- |
| `PROJECT_ROOT` | `Path(__file__).resolve().parent.parent` |
| `CHECKLIST_PATH` | `<root>/checklist/risky-terms.json` |
| `FINDINGS_PATH` | `<root>/output/classify-findings.json` |

Paths resolve relative to the *file*, not the working directory, so the script runs from
anywhere.

**Functions:**

| Function | Contract |
| --- | --- |
| `load_checklist()` | Parses the checklist JSON. Raises on malformed input (no soft failure). |
| `load_contract_text(path)` | Reads UTF-8 and applies `re.sub(r"\s+", " ", raw)`. **Load-bearing** — see below. |
| `find_matches(text, categories)` | Returns `[(category, matched_patterns, snippets)]` for every category, including empty ones. |
| `build_findings(contract_path, results)` | Shapes results into the findings dict. Status is `confirmed` iff ≥1 pattern matched. |
| `write_findings(findings)` | `mkdir -p` on `output/`, writes indent-2 JSON plus a trailing newline. |
| `print_report(...)` | Human stdout: flagged categories, severity, matched patterns, `N of M` summary. |
| `main()` | Argument count check → file existence check → load → match → print → write. Returns 0 or 1. |

**Whitespace normalization is the single most important line in the file.** The sample
contracts are hard-wrapped at ~88 columns, so a phrase like
`"shall automatically renew for successive one-year terms"` is physically split across
lines. Without collapsing whitespace runs to a single space before matching, almost
nothing matches.

**Matching semantics:** lowercase both sides, then `haystack.find(pattern.lower())`.
This is **first-occurrence only** — one snippet per pattern, not per occurrence. Snippets
are sliced out of the *normalized* text at the found offset, preserving original casing
but not original line breaks. Multiple matched patterns within one category are joined
into `matched_text` with `"; "`.

**No regex.** The checklist's `matching.notes` field records this as a contract both
passes honor. A pattern that needs regex is a checklist-format change, not a quiet
addition to the matcher.

### 3.2 `src/generate-summary.py` — step 3

**Purpose:** merge both findings files under the union rule and write the Markdown
summary.

**Invocation:** `python src/generate-summary.py sample-contract-1.txt` — takes a **bare
filename**, not a path. (It tolerates a path by taking `Path(arg).name`, but the
documented form is the bare filename, and the hook matcher depends on that spelling.)

**Exit codes:** `0` = summary written · `1` = a findings file is missing or malformed.

| Function | Contract |
| --- | --- |
| `die(message)` | Prints `generate-summary: <msg>` to stderr and `sys.exit(1)`. |
| `rel(path)` | Project-relative POSIX path for messages; absolute if outside the repo. |
| `plural(n, sing, plur)` | Trivial pluralization for prose. |
| `load_document(path)` | Reads with **`utf-8-sig`**. Dies on missing file, bad JSON, or non-object root. |
| `parse_findings(path, doc)` | Returns `(all category ids seen, {confirmed id: severity})`. Dies on a missing/invalid `findings` list, a non-object entry, or a missing/empty `category_id`. |
| `load_checklist()` | **Optional enrichment.** Returns `[(id, name)]`, or `[]` on any error — the summary still builds from the findings files alone, just with bare ids as names. |
| `build_paragraph(...)` | The plain-English "What this means" text. Branches on verdict, high-severity count, and both-vs-single-pass agreement. |
| `build_markdown(...)` | Assembles the report: header block, flagged list, explanation, provenance footer. |
| `main()` | Orchestrates; warns (non-fatally) on a contract-name mismatch; writes `output/review-summary-<stem>.md`. |

**`utf-8-sig` is deliberate, not incidental.** `subagent-findings.json` is saved by hand,
and on Windows that often means a UTF-8 BOM (PowerShell's `Out-File` adds one).
`utf-8-sig` strips a BOM if present and behaves identically to `utf-8` if not. Note the
asymmetry: `validate-checklist.py` reads the same file with plain `utf-8`, so a BOM makes
the validator fail while the summary script would have succeeded.

**Severity provenance:** severity is read from the *findings files*, not the checklist —
`next((confirmed[id] ...), None)`, first non-empty wins across passes. A category the
checklist has since dropped still renders, with severity `unknown` if neither file
supplied one.

**Ordering:** flagged categories appear in checklist order first, then any unknown ids
sorted alphabetically. The denominator in "N of M" is the checklist size, falling back to
the count of distinct category ids seen across both findings files.

**Contract-name mismatch is a warning, not an error.** If a findings file names a
different contract, the script prints a stderr warning and continues — the gate in front
of it is what turns that into a hard block.

---

## 4. The review pipeline in detail

Three steps, strictly ordered. Each consumes what the previous one wrote, so they cannot
run in parallel or out of order. `/route <contract-filename>` wraps all three.

### Step 1 — Pattern scan

```powershell
python src/classify.py contracts/sample-contract-1.txt
```

Overwrites `output/classify-findings.json`. Fast, exact, blind to rewording.

### Step 2 — Semantic pass

Dispatch the `clause-analyzer` subagent with two paths: the contract
(`contracts/<file>`) and the checklist (`checklist/risky-terms.json`).

The subagent judges each category on **spirit and intent** rather than exact wording,
using the category's `description` as its guide. It has only `Read` and `Grep` and
**cannot write files** — its entire response is a bare JSON object, and saving that
verbatim to `output/subagent-findings.json` is the *caller's* job. Anything wrapped in a
code fence or padded with prose is rejected downstream, so the caller must strip that
before saving.

Its statuses are `confirmed`, `possible`, `not_present`.

### Step 3 — Merge into a verdict

```powershell
python src/generate-summary.py sample-contract-1.txt
```

This call passes through the PreToolUse gate (§8) before it runs. It then applies the
union rule and writes `output/review-summary-<contract-name>.md`.

### 4.1 The union rule

```
flagged(category) := classify.status == "confirmed"  OR  subagent.status == "confirmed"

verdict := "needs-legal-review"  if any category flagged
           "standard"            otherwise
```

`possible` never flags on its own. The rationale: an unsure hit should not consume a
lawyer's time, but a *confident* hit from either pass should, because the two passes look
for genuinely different things — one catches exact known-bad language, the other catches
paraphrase.

Each flagged entry records its `source`: `both`, `classify.py`, or `subagent`. That
provenance is what lets a reviewer calibrate trust in a given flag.

### 4.2 The argument formats differ on purpose

| Script | Argument | Validation |
| --- | --- | --- |
| `classify.py` | **path** — `contracts/sample-contract-1.txt` | `is_file()`, exit 1 if not |
| `generate-summary.py` | **bare filename** — `sample-contract-1.txt` | filename only, used for lookup and output naming |

Do not normalize these to match each other. Both hooks that need to bridge the two
spellings do so explicitly, via a `resolve_contract()` helper that tries
`<root>/<name>` and then `<root>/contracts/<basename>`.

---

## 5. Data contracts (file formats)

### 5.1 `output/classify-findings.json`

```json
{
  "contract": "sample-contract-3.txt",
  "source": "classify.py",
  "findings": [
    {
      "category_id": "auto-renewal-without-notice",
      "severity": "medium",
      "status": "not_present",
      "matched_text": null
    }
  ]
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `contract` | string | **Bare filename.** |
| `source` | string | Always `"classify.py"`. |
| `findings[]` | array | One entry per checklist category, always all of them. |
| `.category_id` | string | Must match a checklist `id`. |
| `.severity` | string | Copied from the checklist. |
| `.status` | enum | `confirmed` \| `not_present`. |
| `.matched_text` | string \| null | Matched snippets joined by `"; "`. |

### 5.2 `output/subagent-findings.json`

```json
{
  "contract": "contracts/sample-contract-3.txt",
  "findings": [
    {
      "category_id": "unilateral-termination",
      "severity": "high",
      "status": "not_present",
      "matched_text": "Either Party may terminate this Agreement for convenience...",
      "reasoning": "All termination rights in Section 12 are expressly granted to 'Either Party'..."
    }
  ]
}
```

Differences from the classify format, all intentional:

- `contract` is a **repo-relative path**, not a bare filename. Every consumer normalizes
  with `Path(name).name` or tries both spellings.
- No `source` field.
- Extra `reasoning` field — one sentence per category, including negatives.
- `status` may additionally be `possible`.
- `matched_text` may be populated even when `status` is `not_present` — the analyzer
  quotes the clause it examined and then explains why it *doesn't* match. Consumers must
  key off `status`, never off the presence of `matched_text`.

**Both files must contain an entry for every checklist category, including negatives.**
Check 4 of the validator enforces this. The negative results are what make the two passes
comparable.

### 5.3 `output/review-summary-<stem>.md`

Fixed five-part structure: title, metadata bullets (contract / verdict / flagged count),
`## Flagged categories` list (or an italic none-found line), `## What this means`
paragraph, and a provenance footer naming both input files and restating the union rule.

Real example (`sample-contract-1.txt`):

```markdown
# Review summary: sample-contract-1.txt

- **Contract:** sample-contract-1.txt
- **Verdict:** needs-legal-review
- **Flagged:** 3 of 5 checklist categories

## Flagged categories

- **Auto-renewal without adequate notice** (`auto-renewal-without-notice`) - severity medium - confirmed by: both
- **Unlimited liability** (`unlimited-liability`) - severity high - confirmed by: both
- **Broad indemnification** (`broad-indemnification`) - severity high - confirmed by: both
```

---

## 6. The checklist

`checklist/risky-terms.json` — version 1, updated 2026-08-14, five categories.

### 6.1 Top-level shape

```json
{
  "version": 1,
  "updated": "2026-08-14",
  "matching": {
    "mode": "phrase",
    "case_sensitive": false,
    "notes": "Patterns are plain phrases matched as case-insensitive substrings against
              normalized contract text (collapse whitespace before matching).
              No regex at this stage."
  },
  "categories": [ ... ]
}
```

The `matching` block is a **behavioural contract both passes honor**, not documentation.

### 6.2 Category schema

| Field | Type | Role |
| --- | --- | --- |
| `id` | string, kebab-case | Join key across all findings files. |
| `name` | string | Display name in the summary. |
| `severity` | `high` \| `medium` | Drives the "N of them are high severity" sentence. |
| `description` | prose | **Load-bearing** — this is what the semantic pass reasons against. |
| `patterns[]` | string[] | Exact phrases for step 1 only. |

`description` is not a comment. A new category with a thin description will underperform
in step 2 even if its `patterns[]` are good.

### 6.3 The five categories

| id | severity | patterns | What it catches |
| --- | --- | --- | --- |
| `auto-renewal-without-notice` | medium | 8 | Silent rollover into a new term with a short or easy-to-miss opt-out window. |
| `unlimited-liability` | high | 8 | Uncapped exposure, or an existing cap expressly waived. |
| `unilateral-termination` | high | 6 | Only one party can walk away, or can do so instantly without cause. |
| `exclusivity` | medium | 6 | One party barred from working with anyone else in a market/category/territory. |
| `broad-indemnification` | high | 6 | Indemnity sweeping in claims of any kind, including the indemnified party's own fault. |

**34 patterns total.** Representative examples:

- `auto-renewal-without-notice` → `"shall automatically renew"`, `"evergreen term"`,
  `"thirty (30) days prior to the end of the then-current term"`
- `unlimited-liability` → `"unlimited liability"`, `"shall not be subject to any cap"`,
  `"waives any limitation of liability"`
- `unilateral-termination` → `"may terminate this agreement at any time, with or without cause"`,
  `"sole and absolute discretion to terminate"`, `"shall have no right to terminate"`
- `exclusivity` → `"exclusive provider"`, `"right of first refusal"`,
  `"shall not engage any third party"`
- `broad-indemnification` → `"any and all claims of any kind whatsoever"`,
  `"arising out of or in any way related to"`, `"without regard to fault"`

Note how specific several patterns are — `"arising out of or in any way related to"` is
worded precisely to *avoid* matching the innocuous boilerplate
`"arising out of or relating to"` planted in the samples.

---

## 7. The `.claude/` control plane

### 7.1 `commands/route.md` — the `/route` slash command

Frontmatter carries only a `description`. The body runs the three steps in order against
`$ARGUMENTS` (a bare contract filename), with explicit instructions to:

- stop and ask if `$ARGUMENTS` is empty — never guess a contract;
- never run the steps in parallel or skip ahead;
- stop and report on any step failure rather than continuing with stale files;
- prefix step 1's argument with `contracts/` but pass step 3 a bare filename;
- strip any code fence or prose from the subagent's response before saving it;
- run step 3's command **exactly as written**, because the gate matches on command text;
- if the gate blocks, say which check failed and what is being re-run — never work
  around the gate.

Final output to the user: verdict, flagged categories with their confirming pass, and the
summary path — a few lines, with the detail left in the file.

### 7.2 `agents/clause-analyzer.md` — the step 2 subagent

```yaml
name: clause-analyzer
tools: Read, Grep
model: sonnet
hooks:
  PreToolUse:
    - matcher: "Read|Grep"
      hooks:
        - type: command
          command: "python .claude/hooks/clause-analyzer-read-gate.py"
```

The system prompt tells it to base findings **only** on the contract and the checklist,
names `answer-key.txt` as off limits, judge each category on spirit/intent using
`description` as the real guide, and be conservative about near-misses — with three
worked examples: mutual obligations are not unilateral ones, `"non-exclusive"` is the
opposite of exclusive, and standard boilerplate is not automatically the risky version of
a similar-sounding phrase. When genuinely unsure: `possible`, with a reason.

Output discipline is stated twice and reinforced with a self-check ("if you find yourself
writing a sentence that isn't inside a JSON string value, stop"). An entry is required for
**every** category, negatives included.

A subagent declaring its own hook in frontmatter is the key mechanism here: the gate is
armed only while this subagent runs, leaving the main session unaffected.

### 7.3 `settings.json`

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(python src/generate-summary.py*)",
            "command": "python .claude/hooks/pretooluse-route-gate.py"
          }
        ]
      }
    ]
  }
}
```

The `if` field **matches on command text**. Rewording the command, adding flags, or
wrapping it in another shell invocation silently bypasses the gate. This is the reason
`route.md` insists on the exact spelling.

### 7.4 `settings.local.json`

A local, uncommitted-by-convention permission allowlist. Three narrow entries:

```
Bash(python src/classify.py contracts/sample-contract-1.txt)
Bash(python src/classify.py contracts/sample-contract-2.txt)
Bash(python src/generate-summary.py sample-contract-3.txt)
```

Per-contract rather than wildcarded — a deliberate friction, and a reminder that
running a *new* contract will prompt for permission.

---

## 8. Validation and the two gates

### 8.1 `hooks/validate-checklist.py` — the six checks

Standalone CLI: `python .claude/hooks/validate-checklist.py [contract-file]`
Exit `0` = all passed, `1` = a check failed. Each failure prints `CHECK n FAILED` plus
every problem found by that check, then exits — checks are sequential, not accumulated
across stages.

| # | Check | Notes |
| --- | --- | --- |
| 1/2 | Both findings files exist | Same test on two files; names whichever is absent. |
| 3 | Both are valid JSON | Read with plain `utf-8` (see the BOM asymmetry in §11). |
| 4 | Both cover every checklist category | Reports "covers X of Y; missing: …". |
| 5 | Both files are newer than the contract | `st_mtime` strictly greater. Catches stale reruns. |
| 6 | Both reviewed the *requested* contract | **Only with an explicit argument.** |

**Argument-free vs. argument mode.** With no argument, each file's contract is taken from
its own `"contract"` field, the two files may legitimately name different contracts, and
check 6 is skipped. Pass a contract explicitly and both files are held to that one: check
5 measures freshness against it, and check 6 rejects a file generated for something else.

**Check 6 exists because check 5 cannot catch it.** Given an override, check 5 compares
mtimes against that contract and never looks at the `"contract"` field — so findings left
over from a *different* contract sail through as long as they are recent. Check 6 closes
that hole using `samefile()`, which sees through both accepted path spellings and Windows
case differences.

`resolve_contract()` handles the format disagreement: `classify.py` writes a bare
filename, the subagent writes a repo-relative path, so both `<root>/<name>` and
`<root>/contracts/<basename>` are tried.

### 8.2 `hooks/pretooluse-route-gate.py` — the route gate (fails **open**)

Wraps the validator as a blocking `PreToolUse` gate on step 3.

Claude Code reads a PreToolUse hook's **exit code**, not its stdout:

| Exit | Effect |
| --- | --- |
| `0` | Allow the tool call |
| `2` | **Block** the call; stderr is fed back to Claude as the reason |
| other non-zero | Surfaced as an error, but the tool call still runs |

The validator exits `1` by design (it is also a standalone CLI), so this wrapper's whole
job is translating any non-zero validator exit into the `2` that actually blocks.

Flow: read JSON payload from stdin → bail unless `tool_name == "Bash"` → parse the
contract argument out of the command → resolve it to a real path → run the validator as a
subprocess → allow on `0`, else print a remediation message and return `2`.

`contract_from_command()` finds the token whose *basename* is `generate-summary.py` (so
`src/…` and `./src/…` both work), then returns the first following token that does not
start with `-`. It uses `shlex.split()` and returns `None` on an unbalanced quote.

**Fail-open cases**, each with a stderr note: an unparseable payload, and a command with
no extractable contract argument. There is also a **partial-degradation** case — if the
name parses but points at nothing on disk, the validator is run *argument-free*, holding
each findings file to its own `"contract"` field. Weaker, but it still catches missing,
incomplete, and stale files.

### 8.3 `hooks/clause-analyzer-read-gate.py` — the read gate (fails **closed**)

Armed from the subagent's own frontmatter, so it applies only while `clause-analyzer`
runs. The main session can still read the answer key.

**Why a hook and not a simpler mechanism** — documented in the file itself:

- A subagent's `tools:` field takes bare tool names, not path patterns. `Read(contracts/**)`
  would be silently ignored rather than enforced.
- A `permissions.deny` rule in `settings.json` *is* enforced, but session-wide — it would
  hide the answer key from everyone.
- `PreToolUse` is the only per-subagent hard boundary.

Both of the subagent's tools can surface file contents, so both are gated:

- **`Read`** — blocked when `file_path` resolves to the answer key.
- **`Grep`** — blocked when the search path would sweep the answer key in, **including an
  omitted path**. An omitted `path` defaults to the working directory, which is the
  project root, which is where the answer key lives — so absence is treated as the root,
  not as safe.

`resolve()` makes relative paths absolute against the project root and calls `.resolve()`,
collapsing `..` and following symlinks — a path aimed at the key the long way round still
lands on it. `hits_answer_key()` matches the project's own key, any file with that name
anywhere, and any directory at or above it.

**This gate fails CLOSED.** An unparseable payload, a non-dict `tool_input`, or an
unresolvable target is blocked, not waved through. The reasoning: a boundary that silently
skips itself on a malformed payload is not a boundary, and loud breakage is the cheaper
failure here — the subagent only ever reads three or four files.

The block message doesn't just refuse; it explains why (an analyzer that has seen the key
isn't an independent second opinion) and redirects to `contracts/` and
`checklist/risky-terms.json`.

### 8.4 The two gates fail in opposite directions — on purpose

| Gate | Direction | Rationale |
| --- | --- | --- |
| Route gate | **Open** | Would rather let a step through than block the pipeline on a malformed payload. The worst case is a summary built from imperfect inputs. |
| Read gate | **Closed** | Would rather break loudly than leak the answer key. The worst case of failing open is a silently invalid experiment. |

This asymmetry is the clearest expression of the project's threat model: pipeline
availability is cheap to recover, evaluation integrity is not.

---

## 9. Test corpus and ground truth

Each sample exercises something specific. `answer-key.txt` records the hand-verified
expected result for each, with exact triggering language and reasoning — it is the ground
truth for judging whether a change to the checklist or the matcher helped or regressed.

| File | Type | Expected | Role |
| --- | --- | --- | --- |
| `sample-contract-1.txt` | Master Services Agreement | **3 categories** | Vendor-favorable. Auto-renewal, unlimited liability, broad indemnification. |
| `sample-contract-2.txt` | Professional Services Agreement | **1 (borderline)** | Mostly balanced; one narrow consent-based exclusivity clause a human would likely accept. |
| `sample-contract-3.txt` | Consulting Services Agreement | **0** | The all-clean negative control. Any hit here is a false positive. |
| `sample-contract-4.txt` | Software Subscription Agreement | **1 category** | Balanced SaaS terms with one deliberately abusive termination section — the only positive case for `unilateral-termination`. |

### 9.1 Planted near-misses (must NOT match)

| Contract text | Nearby pattern | Why it must not match |
| --- | --- | --- |
| `"arising out of or relating to"` | `"arising out of or in any way related to"` | Standard boilerplate vs. the genuinely broad version. |
| `"non-exclusive"` | `"exclusive…"` patterns | Contains `exclusive` as a substring but means the opposite. |
| `"Either Party may terminate…"` | unilateral-termination patterns | Mutual rights are not unilateral ones. |

These are why the semantic pass's system prompt names all three explicitly as
conservatism examples, and why the pattern list is worded so precisely.

**Whenever the matcher changes, re-run all four contracts.** A change that improves recall
on contract 1 often breaks contract 3.

### 9.2 Known checklist gap (unfixed on purpose)

Contract 1's Section 9.1/9.2 asymmetry — Provider capped, Client uncapped — has no
corresponding category, so it is expected *not* to be flagged. `answer-key.txt` records
this as a deliberate gap rather than a bug, which keeps a genuine limitation visible
instead of quietly papered over.

### 9.3 Verified current outputs

The three summaries in `output/` match the answer key:

| Contract | Verdict | Flagged | Confirmed by |
| --- | --- | --- | --- |
| 1 | `needs-legal-review` | 3 of 5 | all three by **both** passes |
| 2 | `needs-legal-review` | 1 of 5 (`exclusivity`) | **both** |
| 3 | `standard` | 0 of 5 | — |

Contract 4 has no summary in `output/` yet; it has not been run through the pipeline.

Note the shape of the contract-3 subagent output: every category is `not_present`, yet
four of the five carry a populated `matched_text` quoting the *exculpatory* clause
(the fee cap, the "Either Party" termination right, the "Non-Exclusive Relationship"
heading). That is the analyzer showing its work on the negative controls.

---

## 10. Design decisions and their rationale

| Decision | Rationale |
| --- | --- |
| **Two passes, union rule** | The failure modes are complementary — exact matching misses paraphrase, an LLM invents matches. Union favors recall, appropriate when the cost of a miss (a bad contract signed) exceeds the cost of a false positive (a lawyer's hour). |
| **`possible` doesn't flag** | Preserves a middle register. Without it the analyzer would be forced to round uncertainty up to `confirmed` or down to `not_present`, and either direction loses information. |
| **Subagent cannot write files** | Keeps a non-deterministic component out of the artifact-writing path. Every file on disk is written by deterministic code or by an explicit human/orchestrator action. |
| **Checklist as JSON, not code** | Legal knowledge changes faster than the matcher. A new risky term is a data edit. |
| **`description` drives the semantic pass** | Prevents the semantic pass from degenerating into a slower version of the substring scan. |
| **No regex** | Keeps patterns writable by non-programmers and matching behavior predictable. A regex need is a schema-change conversation. |
| **Whitespace normalization** | Without it the hard-wrapped corpus defeats substring matching almost entirely. |
| **Paths relative to `__file__`** | Scripts work from any working directory. |
| **Differing argument formats** | Encodes the semantic difference: step 1 *opens* a file, step 3 *identifies* a review. |
| **Gate matches on command text** | Cheap and explicit — at the cost of being bypassable by rewording, which `route.md` calls out loudly rather than hiding. |
| **Opposite fail directions** | See §8.4. |
| **Standard library only** | A learning project should have no install step. |
| **Everything in `output/` is disposable** | No generated file is ever a source of truth. |

---

## 11. Failure modes and error handling

### 11.1 Hard failures (exit 1)

| Condition | Where | Message |
| --- | --- | --- |
| Wrong argument count | both scripts | `Usage: …` |
| Contract path not a file | `classify.py` | `Error: no such file: <path>` |
| Findings file missing | `generate-summary.py` | `missing findings file: … - run that review pass first` |
| Findings file not valid JSON | `generate-summary.py` | `… is not valid JSON: <exc>` |
| Findings root not a JSON object | `generate-summary.py` | `… should be a JSON object, not a <type>` |
| No `findings` list | `generate-summary.py` | `… has no "findings" list` |
| A finding is not an object | `generate-summary.py` | `findings[n] is not an object` |
| Missing/empty `category_id` | `generate-summary.py` | `findings[n] has no "category_id"` |
| Unusable contract filename | `generate-summary.py` | `not a usable contract filename: …` |
| Any of the six checks | `validate-checklist.py` | `CHECK n FAILED` + itemized problems |

### 11.2 Soft failures (warn and continue)

| Condition | Behavior |
| --- | --- |
| Checklist unreadable during step 3 | `load_checklist()` swallows `OSError`/`JSONDecodeError`/`KeyError`/`TypeError` and returns `[]`. Summary still builds with bare ids and a findings-derived denominator. |
| Findings file names a different contract | stderr warning; summary still written. The gate is what makes this fatal in practice. |
| Route gate: unparseable payload | Allow, with a stderr note. |
| Route gate: no contract argument found | Allow, with a stderr note. |
| Route gate: contract argument names a nonexistent file | Run the validator argument-free (weaker but non-empty check). |

### 11.3 Sharp edges worth knowing

- **BOM asymmetry.** `generate-summary.py` reads findings with `utf-8-sig`;
  `validate-checklist.py` reads them with plain `utf-8`. A hand-saved
  `subagent-findings.json` carrying a BOM will therefore **fail check 3 and block step 3**,
  even though step 3 itself would have parsed it fine. If the gate reports invalid JSON on
  a file that looks correct, suspect a BOM first.
- **First-occurrence-only matching.** `classify.py` records one snippet per pattern. A
  pattern appearing five times yields one entry. Fine for flagging; not a concordance.
- **Snippets come from normalized text.** `matched_text` will not reproduce the contract's
  original line breaks.
- **`matched_text` present on negatives.** In subagent output, a populated `matched_text`
  does not imply a match. Always key off `status`.
- **Gate bypass by rewording.** Any spelling of the step-3 command other than
  `python src/generate-summary.py …` skips the route gate entirely and silently.
- **`classify.py` overwrites unconditionally.** There is exactly one
  `classify-findings.json`; reviewing a second contract destroys the first's findings.
  The freshness and contract-match checks exist precisely because of this.
- **No concurrency safety.** Two reviews in parallel will interleave writes to the same
  two findings files.

---

## 12. Operations: running, extending, debugging

### 12.1 Full review, via the slash command

```
/route sample-contract-1.txt
```

### 12.2 Full review, manually (PowerShell, from project root)

```powershell
python src/classify.py contracts/sample-contract-1.txt
# then dispatch clause-analyzer and save its JSON verbatim to:
#   output/subagent-findings.json      (no code fence, no prose, no BOM)
python .claude/hooks/validate-checklist.py contracts/sample-contract-1.txt   # optional pre-check
python src/generate-summary.py sample-contract-1.txt
```

When writing the subagent JSON on Windows, prefer `Set-Content -Encoding utf8NoBOM` (or an
editor set to UTF-8 without BOM) over `Out-File`, for the reason in §11.3.

### 12.3 Regenerating everything

`output/` is entirely disposable:

```powershell
Remove-Item output/*.json, output/*.md -ErrorAction SilentlyContinue
```

Then re-run the pipeline per contract. Keep `output/.gitkeep`.

### 12.4 Adding a risky-terms category

1. Add the object to `checklist/risky-terms.json` — `id`, `name`, `severity`,
   `description`, `patterns[]`.
2. Write the **description** carefully. It is what step 2 reasons against; a thin one
   underperforms regardless of how good `patterns[]` is.
3. Keep patterns as plain phrases. No regex.
4. Check the new patterns against the planted near-misses in §9.1.
5. Re-run **all four** contracts and diff against `answer-key.txt`.
6. Note that existing findings files now fail check 4 (missing the new category) until
   regenerated — which is the validator working as intended.

### 12.5 Adding a sample contract

1. Plain text under `contracts/`, hard-wrapped ~88 columns to match the corpus.
2. Add its hand-verified expected result to `answer-key.txt`, with the exact triggering
   language and the reasoning.
3. Consider what it uniquely exercises — a sample that duplicates an existing one adds
   runtime, not coverage.
4. Expect a permission prompt: `settings.local.json` allowlists specific commands.

### 12.6 Debugging checklist

| Symptom | First things to check |
| --- | --- |
| Step 3 blocked by the gate | Read the stderr — it names the failing check. Usually: subagent findings not saved, saved with a fence, or stale. |
| "not valid JSON" on a file that looks fine | BOM. See §11.3. |
| "covers 4 of 5 categories" | Subagent omitted a category, or the checklist gained one after the findings were written. |
| "is older than … - stale" | The contract was edited after the review ran. Re-run both passes. |
| "was generated for X, not Y" | Findings left over from another contract. Re-run both passes for this one. |
| False positive on contract 3 | A pattern is too loose. Check it against the near-miss table. |
| Nothing matches at all | Whitespace normalization broken, or the contract isn't UTF-8. |
| Gate never fires | The command text was reworded and no longer matches the `if` field. |

### 12.7 Running the validator standalone

```powershell
python .claude/hooks/validate-checklist.py                                  # loose: each file vs its own contract field
python .claude/hooks/validate-checklist.py contracts/sample-contract-1.txt  # strict: both files vs this contract
```

---

## 13. Known gaps and current state

### 13.1 Absent by design

- **No test suite.** The four sample contracts plus `answer-key.txt` serve as a manual
  regression corpus.
- **No `.gitignore`.**
- **No dependencies** beyond the Python standard library.
- **No packaging, no CI, no logging framework.**

### 13.2 Discrepancies observed at documentation time (2026-08-15)

These are recorded as findings, not fixed here.

1. **`answer-key.txt` is not present on disk.** It is referenced by `CLAUDE.md` (as ground
   truth), by `clause-analyzer-read-gate.py` (as the protected file), and by the subagent's
   own system prompt. The read gate still functions — it blocks on filename and on any
   directory at or above the expected location, so a `Grep` at the project root is still
   refused — but the corpus currently has no ground-truth file to diff against. Restoring
   it is a prerequisite for §12.4 step 5.
2. **`src/__pycache__/generate-summary.cpython-314.pyc` is tracked-adjacent build
   residue.** With no `.gitignore`, it is visible to `git status`. Its existence also
   implies `generate-summary` was imported at some point rather than only executed.
3. **`.claude/agents/clause-analyzer.md` lines 34–37 begin with a stray `+`.** Leftover
   diff markers inside the prompt body. They are inert but they do reach the model as
   literal text.
4. **Contract 4 has never been run through the pipeline** — no
   `review-summary-sample-contract-4.md` exists. It is the only positive case for
   `unilateral-termination`, so that category has no end-to-end verification.
5. **Git state:** branch `main`, two commits (`8fc1c76 Mini project Done`,
   `48b8641 Initial`). The only change is a staged deletion of `Test.py`, an original
   placeholder that is not part of the design. Everything described in this document is
   otherwise uncommitted.

### 13.3 Natural next steps

- Restore `answer-key.txt` and run contract 4 end to end.
- Add a `.gitignore` for `__pycache__/` and (optionally) `output/*.json`, `output/*.md`.
- Align the encoding used by `validate-checklist.py` with `generate-summary.py`
  (`utf-8-sig`) to remove the BOM asymmetry.
- Add a category for the Section 9.1/9.2 liability-cap asymmetry, closing the known gap.
- A thin regression runner that loops all four contracts and diffs against the answer key.

### 13.4 Project conventions to preserve

- Input contracts are plain text under `contracts/`.
- New risky terms go in the JSON, never hardcoded in `src/`.
- Anything in `output/` is a generated artifact, never a source of truth.
- Keep the CLI runnable from the project root; both scripts resolve paths relative to the
  file, but the documented invocations and the hook matcher assume the root.
- Standard library only. Ask before adding a dependency or a new top-level directory.
- Prefer small, self-contained changes that leave the tool runnable.

---

## 14. Glossary

| Term | Meaning |
| --- | --- |
| **Pattern scan** | Step 1. Deterministic case-insensitive substring matching by `classify.py`. |
| **Semantic pass** | Step 2. LLM judgement on spirit/intent by the `clause-analyzer` subagent. |
| **Union rule** | A category is flagged if *either* pass reports it `confirmed`. |
| **Verdict** | `standard` or `needs-legal-review`. |
| **Findings file** | One pass's per-category results as JSON, under `output/`. |
| **Category** | One risky-terms entry: id, name, severity, description, patterns. |
| **Near-miss** | Contract text deliberately close to a pattern that must *not* match. |
| **Fails open** | On internal error, allow the action (route gate). |
| **Fails closed** | On internal error, block the action (read gate). |
| **PreToolUse hook** | A Claude Code hook run before a tool call; exit `0` allows, exit `2` blocks. |
| **Answer key** | `answer-key.txt` — hand-verified expected results; ground truth for regressions. |

---

*Generated from a full read of every source file in the repository. Line counts, exit
codes, field names, and quoted messages are taken from the code as it stands on
2026-08-15.*
