#
#    This is ExplainDB, educational database systems materials.
#
#    Copyright (C) 2026 Prof. Dr. Jens Dittrich, Saarland University
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#

# Test modules use self-documenting method/class names and module-level
# fixtures, so pylint's naming and docstring checks are relaxed here.
# pylint: disable=invalid-name,missing-class-docstring,missing-function-docstring
"""Tests that every test folder below ``system/tests/`` has an ``__init__.py``."""

import tempfile
import unittest
from pathlib import Path

# Anchored to this test file so discovery works regardless of the caller's cwd.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
TESTS_DIR: Path = REPO_ROOT / "system" / "tests"


def folders_without_init(root: Path) -> list[Path]:
    """Return the folders below ``root`` that hide test modules from discovery.

    ``unittest discover`` only descends into folders that have an
    ``__init__.py``. A test module, i.e. a file matching ``test*.py`` (the
    default pattern of ``unittest discover``, which CI uses), is therefore
    found only if every folder between ``root`` and the module has one.
    ``root`` itself is exempt: discovery starts there, and ``system/tests``
    deliberately has no ``__init__.py``. ``__pycache__`` folders hold only
    ``.pyc`` files, which the pattern does not match. Symlinked folders are not
    followed (``unittest discover`` would follow them; ``system/tests`` has
    none).

    @param root: the folder that ``unittest discover`` starts from
    @return: the folders that lack an ``__init__.py``, sorted
    """
    missing: set[Path] = set()
    for test_file in root.rglob("test*.py"):
        folder: Path = test_file.parent
        while folder != root:
            if not (folder / "__init__.py").is_file():
                missing.add(folder)
            folder = folder.parent
    return sorted(missing)


class FoldersWithoutInitTest(unittest.TestCase):
    """Checks that no test folder below system/tests/ is skipped by unittest discover.

    This module cannot detect a missing ``system/tests/repo/__init__.py``:
    discovery would then skip this module too.
    """

    def test_reports_folders_without_init_on_the_way_to_a_test_module(
        self,
    ) -> None:
        """Folders without __init__.py on the way to a test module are reported; the root and folders without test modules are not."""
        with tempfile.TemporaryDirectory() as tmp:
            root: Path = Path(tmp)
            files: list[str] = [
                "test_top.py",  # directly in root: no __init__.py needed
                "ok/__init__.py",
                "ok/test_ok.py",
                "ok/__pycache__/test_ok.cpython-312.pyc",  # not a test module
                "flat/test_flat.py",  # flat/ lacks __init__.py
                "outer/inner/__init__.py",
                "outer/inner/test_inner.py",  # outer/ lacks __init__.py
                "helpers/util.py",  # no test module: no __init__.py needed
            ]
            for name in files:
                path: Path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            self.assertEqual(
                folders_without_init(root), [root / "flat", root / "outer"]
            )

    def test_every_test_folder_in_system_tests_has_an_init(self) -> None:
        """Every folder below system/tests/ that holds tests, or lies on the path to one, has an __init__.py."""
        self.assertEqual(folders_without_init(TESTS_DIR), [])


if __name__ == "__main__":
    unittest.main()
