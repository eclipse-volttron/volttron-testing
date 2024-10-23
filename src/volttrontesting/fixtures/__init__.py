import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def get_pyproject_toml() -> Path:
    for parent in Path(os.getcwd()).parents:
        if (parent / "pyproject.toml").exists():
            return parent / "pyproject.toml"

    for parent in Path(__file__).parents:
        if (parent / "pyproject.toml").exists():
            return parent / "pyproject.toml"

    raise ValueError("Could not find pyproject.toml file tree.")
