from __future__ import annotations

from pathlib import Path


def test_core_package_does_not_import_extractx_eval() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders: list[Path] = []
    for path in (root / "src" / "extractx").rglob("*.py"):
        if "extractx_eval" in path.read_text():
            offenders.append(path.relative_to(root))

    assert offenders == []


def test_eval_package_not_in_core_wheel_package_list() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text()

    assert 'packages = ["src/extractx"]' in pyproject
    assert "src/extractx_eval" not in pyproject


def test_core_package_does_not_publish_domain_correlation_surface() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders: list[tuple[Path, str]] = []
    forbidden = ("business_entity_id", "CorrelationContext")
    for path in (root / "src" / "extractx").rglob("*.py"):
        text = path.read_text()
        for term in forbidden:
            if term in text:
                offenders.append((path.relative_to(root), term))

    assert offenders == []
