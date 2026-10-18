"""PostToolUse hook: warns about module-level functions in src/ without a `# module-level:` note.

Rule: docs/standards/python-czysty-kod.md, section 2. Behaviour belongs to the class that owns
the concept; a justified module-level function carries `# module-level: <reason>` in the
comment block right above its `def` (or above its first decorator). The hook never blocks;
it only tells Claude.

After Edit/Write it checks the edited file. After Bash, which can write files through any
script, it checks every Python file under src/.
"""

import ast
import json
import os
import sys
from pathlib import Path

MARKER = "# module-level:"


def unmarked_functions(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except OSError, SyntaxError, ValueError:
        return []
    lines = source.splitlines()
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        # The marker may start any line of the comment block right above the function.
        comment = []
        index = first - 2
        while index >= 0 and lines[index].lstrip().startswith("#"):
            comment.append(lines[index].strip())
            index -= 1
        if not any(line.startswith(MARKER) for line in comment):
            found.append(f"{path.name}:{node.lineno} {node.name}()")
    return found


def files_to_check(payload: dict[str, object]) -> list[Path]:
    if payload.get("tool_name") == "Bash":
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
        return sorted((root / "src").rglob("*.py"))
    tool_input = payload.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    path = Path(str(file_path or ""))
    return [path] if path.suffix == ".py" and "src" in path.parts else []


def main() -> None:
    payload = json.load(sys.stdin)
    unmarked = [name for path in files_to_check(payload) for name in unmarked_functions(path)]
    if not unmarked:
        return
    message = (
        "Module-level functions without a `# module-level: <reason>` note: "
        + ", ".join(unmarked)
        + ". Move each into the class that owns the concept (method, private method, "
        "classmethod factory, model method), or justify it with the marker in the comment "
        "right above (docs/standards/python-czysty-kod.md, section 2)."
    )
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}},
        sys.stdout,
    )


if __name__ == "__main__":
    main()
