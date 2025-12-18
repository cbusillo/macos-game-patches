# Python Style Quick Reference

- Target Python 3.12+; use type hints everywhere.
- Do not add `from __future__ import annotations` (redundant for our target).
  Prefer quoting forward refs or using `typing.TYPE_CHECKING` when needed.
- Prefer small, single-purpose functions; use early returns to keep nesting shallow.
- Use f-strings for formatting; avoid `%` or `format()`.
- Avoid blanket `except Exception`; catch specific exceptions.
- Favor descriptive identifiers; add comments only when intent is non-obvious.
- Keep I/O at the edges; separate pure logic from filesystem/process calls
  when possible.
- When adding commands/entrypoints, ensure `src/` is on `sys.path`
  (see existing CLIs).
- Update related docs when code structure or behavior changes
  (README, per-game docs, TECHNICAL).
