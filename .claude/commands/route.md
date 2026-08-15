---
description: Review a contract end to end - pattern scan, semantic pass, merged verdict
---

Route the contract named in `$ARGUMENTS` to `standard` or `needs-legal-review`.

`$ARGUMENTS` is a bare contract filename, e.g. `sample-contract-2.txt`. If it is
empty, stop and ask which contract to review - do not guess or pick one.

Run all three steps below, in order. Each one consumes what the previous step
wrote, so do not skip ahead or run them in parallel. If a step fails, stop and
report the failure rather than continuing with stale files from an earlier run.

## 1. Pattern scan

```
python src/classify.py contracts/$ARGUMENTS
```

Note the `contracts/` prefix: `classify.py` takes a **path** to the contract and
exits 1 if it is not a real file. This overwrites `output/classify-findings.json`.

## 2. Semantic pass

Dispatch the `clause-analyzer` subagent to catch reworded risky clauses that the
substring scan misses. Give it both paths it expects:

- contract: `contracts/$ARGUMENTS`
- checklist: `checklist/risky-terms.json`

The subagent has only `Read` and `Grep` - it **cannot write files**. Its entire
response is a JSON object, and saving it is your job: write that JSON verbatim to
`output/subagent-findings.json`.

Before saving, confirm the response is a bare JSON object. If it arrived wrapped
in a code fence or with prose around it, strip that so the file contains only the
JSON. Step 3 and the validator both reject anything else.

## 3. Merge into a verdict

```
python src/generate-summary.py $ARGUMENTS
```

A **bare filename** here, not a path - this script's argument format differs from
`classify.py`'s on purpose.

Run this command exactly as written. A PreToolUse hook in `settings.json` matches
on this command text to gate the step, so rewording it, adding flags, or wrapping
it in another shell invocation will silently bypass the gate.

The script applies the union rule - a category is flagged if **either** findings
file confirms it - and writes `output/review-summary-<contract-name>.md`.

## Then report

Tell the user the verdict, the flagged categories with which pass confirmed each,
and the path to the summary file. Keep it to a few lines; the summary file has the
detail.

If the hook blocks step 3, it means the two findings files are missing, incomplete,
or older than the contract. Say which check failed and what you are re-running -
do not try to work around the gate.
