"""Regression tests for the license metadata of the published package.

See https://github.com/edihasaj/tuspyserver/issues/105

The repository has always shipped a ``LICENSE`` file, but ``pyproject.toml``
declared no license, so the wheels and sdists uploaded to PyPI carried no
license metadata and bundled no license text. These tests pin the PEP 639
declaration that fixes that.
"""

from pathlib import Path

import pytest

tomllib = pytest.importorskip(
    "tomllib", reason="TOML parsing needs Python 3.11+ (or a tomli backport)"
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_LICENSE = "MIT"


@pytest.fixture(scope="module")
def project_metadata() -> dict:
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]


def test_license_is_declared_as_an_spdx_expression(project_metadata: dict) -> None:
    # A plain string (not a table) is what makes uv_build emit
    # ``License-Expression`` instead of silently dropping the license.
    assert project_metadata.get("license") == EXPECTED_LICENSE


def test_license_files_are_declared_and_present(project_metadata: dict) -> None:
    license_files = project_metadata.get("license-files")
    assert license_files, "license-files must be set so the text ships in the dist"

    for pattern in license_files:
        assert list(PROJECT_ROOT.glob(pattern)), f"no file matches {pattern!r}"


def test_license_text_matches_the_declared_license() -> None:
    text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith(f"{EXPECTED_LICENSE} License")
