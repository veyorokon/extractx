"""Command line entrypoint for live extractx smoke manifests."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from extractx.storage import LocalFilesystemStore
from pydantic import BaseModel

from .dataset import load_smoke_dataset
from .smoke import smoke_run_and_check


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    parser.print_help()
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extractx-eval",
        description="run extractx smoke manifests through the real extract(...) path",
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="run a local smoke manifest")
    run.add_argument("manifest", type=Path, help="path to a smoke dataset JSON manifest")
    run.add_argument(
        "--schema",
        action="append",
        default=[],
        metavar="SCHEMA_ID=MODULE:CLASS",
        help="register a pydantic schema class for a manifest schema_id",
    )
    run.add_argument(
        "--store-root",
        type=Path,
        default=Path(".extractx-smoke-runs"),
        help="directory where per-case replay stores are written",
    )
    run.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print JSON output",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    schema_registry = load_schema_registry(args.schema)
    manifest_path = cast("Path", args.manifest)
    store_root = cast("Path", args.store_root)
    cases = load_smoke_dataset(
        manifest_path,
        schema_registry=schema_registry,
        store_factory=lambda case_id: LocalFilesystemStore(store_root / case_id),
    )
    report = asyncio.run(smoke_run_and_check(cases))
    indent = 2 if args.pretty else None
    print(
        json.dumps(
            report.model_dump(mode="json"),
            indent=indent,
            sort_keys=True,
        ),
    )
    if report.total_errors or report.total_value_mismatches:
        return 1
    return 0


def load_schema_registry(
    schema_specs: Sequence[str],
) -> dict[str, type[BaseModel]]:
    registry: dict[str, type[BaseModel]] = {}
    for spec in schema_specs:
        schema_id, class_ref = _split_schema_spec(spec)
        if schema_id in registry:
            raise SystemExit(f"eval_cli.duplicate_schema: schema_id={schema_id!r}")
        schema_cls = _import_schema_class(class_ref)
        registry[schema_id] = schema_cls
    return registry


def _split_schema_spec(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise SystemExit(
            "eval_cli.invalid_schema: expected SCHEMA_ID=MODULE:CLASS, "
            f"got {spec!r}",
        )
    schema_id, class_ref = spec.split("=", 1)
    if schema_id == "" or class_ref == "":
        raise SystemExit(
            "eval_cli.invalid_schema: expected non-empty schema id and class ref",
        )
    return schema_id, class_ref


def _import_schema_class(class_ref: str) -> type[BaseModel]:
    if ":" not in class_ref:
        raise SystemExit(
            "eval_cli.invalid_schema: expected MODULE:CLASS, "
            f"got {class_ref!r}",
        )
    module_name, attr_name = class_ref.split(":", 1)
    if module_name == "" or attr_name == "":
        raise SystemExit(
            "eval_cli.invalid_schema: expected non-empty module and class names",
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SystemExit(
            f"eval_cli.import_failed: could not import {module_name!r}: {exc!s}",
        ) from exc
    attr = getattr(module, attr_name, None)
    if not isinstance(attr, type) or not issubclass(attr, BaseModel):
        raise SystemExit(
            "eval_cli.invalid_schema: "
            f"{class_ref!r} did not resolve to a pydantic BaseModel subclass",
        )
    return attr


if __name__ == "__main__":
    sys.exit(main())
