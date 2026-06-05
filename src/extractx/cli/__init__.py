"""cli entry points per docs/architecture.md §16.

`main` is wired to the `extractx` script in `pyproject.toml`
(`[project.scripts] extractx = "extractx.cli:main"`).
"""

from extractx.cli.run import main

__all__ = ["main"]
