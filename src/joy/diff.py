"""Line-numbered file diffs for the terminal."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

CONTEXT = 3
MAX_ROWS = 60
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
_STYLE = {"add": "on #1c3a24", "del": "on #3f1d20", "ctx": "dim"}
_MARK = {"add": ("+", "bold green"), "del": ("-", "bold red"), "ctx": (" ", "")}


@dataclass(frozen=True)
class DiffRow:
    kind: str  # "add", "del", "ctx", or "gap" between distant hunks
    old: int | None  # line number before the change; None for added lines
    new: int | None  # line number after the change; None for deleted lines
    text: str


@dataclass(frozen=True)
class FileDiff:
    path: str
    created: bool
    added: int
    removed: int
    rows: tuple[DiffRow, ...]
    hidden: int = 0  # rows cut to keep long diffs readable


def build_diff(path: str, before: str | None, after: str) -> FileDiff | None:
    """Diff file text by line; before is None for a new file. None means no change."""
    if before == after:
        return None
    old = [] if before is None else before.splitlines()
    new = after.splitlines()
    # Edits usually touch one region: trim the shared head and tail so the matcher only
    # sees the changed middle plus its context.
    limit = min(len(old), len(new))
    head = 0
    while head < limit and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < limit - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    start = max(0, head - CONTEXT)
    a = old[start:len(old) - tail + min(tail, CONTEXT)]
    b = new[start:len(new) - tail + min(tail, CONTEXT)]
    rows: list[DiffRow] = []
    for index, group in enumerate(SequenceMatcher(None, a, b).get_grouped_opcodes(CONTEXT)):
        if index:
            rows.append(DiffRow("gap", None, None, ""))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [DiffRow("ctx", start + i1 + n + 1, start + j1 + n + 1, a[i1 + n])
                         for n in range(i2 - i1)]
            if tag in ("replace", "delete"):
                rows += [DiffRow("del", start + i + 1, None, a[i]) for i in range(i1, i2)]
            if tag in ("replace", "insert"):
                rows += [DiffRow("add", None, start + j + 1, b[j]) for j in range(j1, j2)]
    return FileDiff(
        path, before is None,
        sum(row.kind == "add" for row in rows), sum(row.kind == "del" for row in rows),
        tuple(rows[:MAX_ROWS]), sum(row.kind != "gap" for row in rows[MAX_ROWS:]),
    )


def _visible(text: str) -> str:
    # File contents and paths come from the model; keep escape sequences off the terminal.
    return _CONTROL.sub(
        lambda match: chr(0x2400 + ord(match[0])) if match[0] < " " else "�",
        text.expandtabs(4),
    )


def render_diff(diff: FileDiff) -> RenderableType:
    title = Text.assemble(
        ("● ", "bold cyan"),
        ("Created " if diff.created else "Edited ", "bold"),
        (_visible(diff.path), "bold"),
        "  ",
        (f"+{diff.added}", "green"),
    )
    if not diff.created:
        title.append(f" -{diff.removed}", style="red")
    body = Table.grid(expand=True, padding=(0, 1))
    # New files have no old line numbers, so skip that column.
    if not diff.created:
        body.add_column(width=len(str(max((row.old or 0 for row in diff.rows), default=0))),
                        justify="right", style="dim")
    body.add_column(width=len(str(max((row.new or 0 for row in diff.rows), default=0))),
                    justify="right", style="dim")
    body.add_column(width=1, justify="center")
    body.add_column(ratio=1, overflow="fold")

    def numbers(old: int | None, new: int | None) -> list[str]:
        return ([] if diff.created else [str(old or "")]) + [str(new or "")]

    for row in diff.rows:
        if row.kind == "gap":
            body.add_row(*numbers(None, None), "", Text("⋯", style="dim"))
            continue
        mark, mark_style = _MARK[row.kind]
        body.add_row(*numbers(row.old, row.new), Text(mark, style=mark_style),
                     Text(_visible(row.text)), style=_STYLE[row.kind])
    if diff.hidden:
        body.add_row(*numbers(None, None), "", Text(f"… {diff.hidden} more lines", style="dim"))
    return Group(title, body)
