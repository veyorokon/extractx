"""`extractx run` cli command per docs/architecture.md §16.

`main()` is the `extractx` script entry point. implementation is delegated
to a downstream cli task; this stub only reserves the contract.
"""


def main() -> None:
    """run the extractx cli. raises until a cli task wires up parsing and dispatch."""
    raise NotImplementedError("cli is a stub; implementation is delegated to a downstream task.")
