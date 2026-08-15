#!/usr/bin/env python
"""PreToolUse gate: keep the clause-analyzer subagent away from answer-key.txt.

Wired up from `hooks:` in .claude/agents/clause-analyzer.md, so it is armed only
while that subagent is running. The main session is unaffected and can still read
the answer key.

Why a hook rather than a scope pattern: a subagent's `tools:` field takes bare
tool names, not path patterns, so `tools: Read(contracts/**)` would be ignored
rather than enforced. A `permissions.deny` rule in settings.json *is* enforced but
applies session-wide, which would hide the answer key from everyone. PreToolUse is
the only per-subagent hard boundary.

Claude Code reads a PreToolUse hook's exit code, not its stdout:
    0 -> allow the tool call
    2 -> BLOCK the tool call, and feed stderr back to the subagent as the reason

The payload arrives on stdin as JSON:
    {"hook_event_name": "PreToolUse", "tool_name": "Read",
     "tool_input": {"file_path": "..."}, ...}

Both of the subagent's tools can surface file contents, so both are gated:
  Read  - blocked when file_path resolves to the answer key.
  Grep  - blocked when its search path would sweep the answer key in, which
          includes an omitted path (that defaults to the project root, where the
          answer key lives). The block message points at contracts/ and
          checklist/ instead, which is all this subagent legitimately needs.

This gate fails CLOSED: a payload it cannot parse, or a call whose target it
cannot determine, is blocked rather than waved through. A boundary that silently
skips itself on a malformed payload is not a boundary, and loud breakage is the
cheaper failure here - the subagent only ever reads three or four files.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ANSWER_KEY = PROJECT_ROOT / "answer-key.txt"
PROTECTED_NAME = "answer-key.txt"

ALLOW, BLOCK = 0, 2

REASON = (
    "Blocked: the clause-analyzer subagent is not allowed to read "
    f"{PROTECTED_NAME}.\n"
    "Its findings have to be derived independently, from the contract and the "
    "checklist only, or the semantic pass stops being a real second opinion.\n"
    "Read the contract under contracts/ and checklist/risky-terms.json, and "
    "judge each category on the contract text itself."
)


def resolve(raw):
    """Absolute, symlink-free path for a tool argument, or None if unusable.

    Relative paths are taken against the project root, which is the subagent's
    working directory. resolve() also collapses '..' and follows symlinks, so a
    path aimed at the answer key the long way round still lands on it.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate.resolve()
    except (OSError, ValueError):
        return None


def hits_answer_key(path):
    """True if reading this path would expose the answer key.

    Matches the project's own answer key, any file with that name elsewhere, and
    any directory at or above it - a Grep rooted at the project root sweeps the
    answer key in just as surely as naming it outright.
    """
    if path == ANSWER_KEY or path.name == PROTECTED_NAME:
        return True
    return path.is_dir() and (path == ANSWER_KEY.parent or path in ANSWER_KEY.parents)


def block(detail):
    print(detail, file=sys.stderr)
    return BLOCK


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return block(
            "Blocked: clause-analyzer read gate could not parse the hook payload, "
            "so it cannot confirm this call stays clear of "
            f"{PROTECTED_NAME}. Failing closed."
        )

    tool = payload.get("tool_name")
    if tool not in ("Read", "Grep"):
        # The frontmatter matcher already narrows this; anything else is not ours.
        return ALLOW

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return block(
            f"Blocked: clause-analyzer read gate saw a {tool} call with no "
            "readable input, so it cannot confirm the target. Failing closed."
        )

    if tool == "Read":
        target = resolve(tool_input.get("file_path"))
        if target is None:
            return block(
                "Blocked: clause-analyzer read gate could not resolve the "
                "file_path on this Read call. Failing closed."
            )
        return block(REASON) if hits_answer_key(target) else ALLOW

    # Grep. An omitted path defaults to the working directory - the project root,
    # which holds the answer key - so absence is treated as the root, not as safe.
    target = resolve(tool_input.get("path") or ".")
    if target is None:
        return block(
            "Blocked: clause-analyzer read gate could not resolve the path on "
            "this Grep call. Failing closed."
        )
    if hits_answer_key(target):
        return block(
            f"{REASON}\n"
            "This Grep would have searched a directory containing "
            f"{PROTECTED_NAME}. Narrow it to contracts/ or checklist/ and retry."
        )
    return ALLOW


if __name__ == "__main__":
    sys.exit(main())
