from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]


def _local_paths_from_markdown(text: str) -> set[Path]:
    """Extract every local file path (examples/*.json, scripts/*.sh, docs/technical/*)
    referenced in markdown content."""
    paths: set[Path] = set()
    # Match markdown links: [text](path) or bare paths in code blocks
    for match in re.finditer(
        r"""
        (?:
            # Markdown link: [text](path)
            \]\(([^)]+)\)
            |
            # Inline code: `path`
            `([^`]+)`
        )
        """,
        text,
        re.VERBOSE,
    ):
        candidate = match.group(1) or match.group(2)
        if not candidate:
            continue
        # Strip leading ./ and trailing punctuation
        candidate = candidate.strip()
        if candidate.startswith("./"):
            candidate = candidate[2:]
        # Only consider paths matching the documented patterns
        if (
            candidate.startswith("examples/")
            and candidate.endswith(".json")
        ) or (
            candidate.startswith("scripts/")
            and candidate.endswith(".sh")
        ) or (
            candidate.startswith("docs/technical/")
        ):
            paths.add(Path(candidate))
    return paths


@pytest.fixture(scope="module")
def readme_text() -> str:
    path = REPO_ROOT / "README.md"
    assert path.exists(), f"README.md not found at {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def quickstart_text() -> str:
    path = REPO_ROOT / "QUICKSTART.md"
    assert path.exists(), f"QUICKSTART.md not found at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("doc_name", ["README.md", "QUICKSTART.md"])
def test_all_referenced_local_paths_exist(doc_name: str) -> None:
    """Assert every local file path referenced in README.md and QUICKSTART.md exists."""
    path = REPO_ROOT / doc_name
    assert path.exists(), f"{doc_name} not found"
    text = path.read_text(encoding="utf-8")
    referenced = _local_paths_from_markdown(text)
    missing: list[str] = []
    for ref in referenced:
        full = REPO_ROOT / ref
        if not full.exists():
            missing.append(str(ref))
    assert not missing, f"Missing files referenced in {doc_name}: {', '.join(missing)}"


def test_porter_forces_help_exits_zero() -> None:
    """Assert `uv run porter-forces --help` exits with code 0."""
    result = subprocess.run(
        [sys.executable, "-m", "uv", "run", "porter-forces", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"porter-forces --help exited {result.returncode}:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
