#!/usr/bin/env python
"""PreToolUse gate: block the route step unless the findings files check out.

Claude Code reads a PreToolUse hook's exit code, not its stdout:
    0 -> allow the tool call
    2 -> BLOCK the tool call, and feed stderr back to Claude as the reason
    other non-zero -> surfaced as an error but the tool call still runs

validate-checklist.py exits 1 on failure by design (it is also a standalone CLI),
so this wrapper translates any non-zero validator exit into the 2 that actually
blocks.

The hook payload arrives on stdin as JSON:
    {"hook_event_name": "PreToolUse", "tool_name": "Bash",
     "tool_input": {"command": "python src/generate-summary.py sample-contract-1.txt"},
     ...}

The contract filename is pulled out of that command and passed to the validator,
so both findings files are held to the contract actually being reviewed rather
than each to whatever contract it happens to name itself.

Fail-open cases, each with a note on stderr: a payload we cannot parse, and a
command we cannot pull a contract argument out of. The settings.json `if` field
already narrows this to the generate-summary.py call.
"""

import json
import shlex
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATOR = Path(__file__).resolve().parent / "validate-checklist.py"

# The script whose argument we are after, matched on filename alone so that
# "src/generate-summary.py" and "./src/generate-summary.py" both work.
SUMMARY_SCRIPT = "generate-summary.py"

ALLOW, BLOCK = 0, 2


def basename(token):
    """Last path segment of a command-line token, forward or back slashes."""
    return token.replace("\\", "/").rsplit("/", 1)[-1]


def contract_from_command(command):
    """Pull the contract argument out of a generate-summary.py invocation.

    Returns None if the command does not name the script, or names it with no
    positional argument after it - the caller treats that as "cannot tell what
    contract this is" and fails open.
    """
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quote. Not our business to repair it.
        return None

    for index, token in enumerate(tokens):
        if basename(token) != SUMMARY_SCRIPT:
            continue
        for candidate in tokens[index + 1:]:
            if candidate.startswith("-"):
                continue
            return candidate
        return None
    return None


def resolve_contract(name):
    """Locate the contract on disk, or None.

    generate-summary.py takes a bare filename but validate-checklist.py checks
    its argument with is_file(), so the bare name has to be turned into a real
    path first. Same two spellings validate-checklist.py itself tries.
    """
    candidates = [PROJECT_ROOT / name, PROJECT_ROOT / "contracts" / Path(name).name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Can't tell what tool this is, so don't stand in its way. Fail open with a
        # note rather than blocking the route step on a malformed payload.
        print("validate-checklist gate: unreadable hook payload, skipping check",
              file=sys.stderr)
        return ALLOW

    if payload.get("tool_name") != "Bash":
        return ALLOW

    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None

    name = contract_from_command(command)
    if name is None:
        print("validate-checklist gate: no contract argument found in the "
              "generate-summary.py call, skipping check", file=sys.stderr)
        return ALLOW

    argv = [sys.executable, str(VALIDATOR)]
    contract = resolve_contract(name)
    if contract is None:
        # The name parsed but points at nothing. Rather than skip the gate
        # entirely, run the validator argument-free: it then holds each findings
        # file to the contract named in its own "contract" field, which is a
        # weaker check but still catches missing, incomplete, and stale files.
        print(f"validate-checklist gate: no such contract {name}, checking each "
              f"findings file against its own contract field instead",
              file=sys.stderr)
    else:
        argv.append(str(contract))

    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode == 0:
        return ALLOW

    detail = (result.stderr or result.stdout).strip()
    print(
        "Blocked: the findings files are missing, incomplete, or stale.\n"
        f"{detail}\n"
        "Re-run `python src/classify.py <contract>` and the clause-analyzer pass, "
        "then retry.",
        file=sys.stderr,
    )
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
