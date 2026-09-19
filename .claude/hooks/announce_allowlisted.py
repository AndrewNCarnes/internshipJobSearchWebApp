"""
PreToolUse hook (notice only): print a *** line when a shell command matches
one of the project's allowlisted patterns in .claude/settings.json, so
auto-approved commands are visible rather than silent.

It makes no permission decision -- approval comes from permissions.allow.
"""
import json
import re
import sys

ALLOWLISTED = [
    r"python (-u )?probe_board\.py\b",
    r"python (-u )?dry_run\.py\b",
    r"(Get-Content|Select-Object|Get-ChildItem)\b",
    r"git (log|status|diff)\b",
    r"python --version$",
]


def main():
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") not in ("Bash", "PowerShell"):
            return
        command = " ".join(((data.get("tool_input") or {})
                            .get("command", "")).split())
        if any(re.match(p, command) for p in ALLOWLISTED):
            short = command if len(command) <= 140 else command[:137] + "..."
            print(json.dumps({"systemMessage": f"*** allowlisted: {short}"}))
    except Exception:
        return


if __name__ == "__main__":
    main()
