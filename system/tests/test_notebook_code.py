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
"""Tests for the CI helper ``.ci/notebook_code.py`` that turns notebooks into Python files for Semgrep.

``.ci`` is not an importable package, so the module is loaded from its file path.
"""

import ast
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

# Anchored to this test file so discovery works regardless of the caller's cwd.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SCRIPT_PATH: Path = REPO_ROOT / ".ci" / "notebook_code.py"


def load_script() -> ModuleType:
    """Loads ``.ci/notebook_code.py`` as a module."""
    spec = importlib.util.spec_from_file_location("notebook_code", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script: ModuleType = load_script()


def write_notebook(directory: Path, name: str, cells: list[dict]) -> Path:
    """Writes a minimal notebook with the given cells and returns its path."""
    path: Path = directory / name
    path.write_text(
        json.dumps(
            {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        ),
        encoding="utf-8",
    )
    return path


class CellCodeTest(unittest.TestCase):
    """Checks how the lines of a code cell are turned into Python that Semgrep can parse."""

    def test_python_is_kept_unchanged(self) -> None:
        """Python lines stay as they are, including continuation lines that start with an operator."""
        code: str = "if (a\n    != b):\n    x = c \\\n        % n"
        self.assertEqual(code, script.cell_code(code))

    def test_line_magic_keeps_its_python_code(self) -> None:
        """A line magic becomes the Python code at its end, after the magic's options."""
        self.assertEqual("eval(x)  # %time", script.cell_code("%time eval(x)"))
        self.assertEqual(
            "eval(x)  # %timeit", script.cell_code("%timeit -n 10 eval(x)")
        )
        self.assertEqual("y = f()  # %time", script.cell_code("y = %time f()"))

    def test_line_magic_without_python_becomes_pass(self) -> None:
        """A line magic without Python code becomes ``pass``, which keeps an indented block valid."""
        self.assertEqual(
            "if ok:\n    pass  # %cd /tmp",
            script.cell_code("if ok:\n    %cd /tmp"),
        )

    def test_shell_command_keeps_the_python_in_braces(self) -> None:
        """A shell command becomes ``pass`` followed by the Python expressions in braces."""
        self.assertEqual(
            "for f in files:\n    pass  # !rm {f}\n    (f)",
            script.cell_code("for f in files:\n    !rm {f}"),
        )
        self.assertEqual(
            "pass  # x = !echo {eval(y)}\n(eval(y))",
            script.cell_code("x = !echo {eval(y)}"),
        )

    def test_shell_command_keeps_braces_that_are_not_python_in_the_comment_only(
        self,
    ) -> None:
        """Text in braces that is not a Python expression (an awk program) stays in the comment only."""
        self.assertEqual(
            "pass  # !awk '{print $1}' data.txt",
            script.cell_code("!awk '{print $1}' data.txt"),
        )
        self.assertEqual(
            "pass  # !awk -F, '{s += $2} END {print s}' data.csv",
            script.cell_code("!awk -F, '{s += $2} END {print s}' data.csv"),
        )
        self.assertEqual(
            "pass  # !echo {x} '{print $1}'\n(x)",
            script.cell_code("!echo {x} '{print $1}'"),
        )

    def test_json_in_braces_is_read_like_an_f_string_field(self) -> None:
        """JSON in braces yields the part before ``:``, as IPython reads it like a field of an f-string."""
        self.assertEqual(
            'pass  # !curl -d \'{"a": 1}\' url\n("a")',
            script.cell_code("!curl -d '{\"a\": 1}' url"),
        )

    def test_shell_command_drops_conversion_and_format_in_braces(self) -> None:
        """A conversion (``!r``) or format (``:>10``) in braces is dropped, but ``!=`` in the expression is kept."""
        self.assertEqual(
            "pass  # !echo {eval(x)!r} {y:>10} {f(a)!=b!r}\n(eval(x))\n(y)\n(f(a)!=b)",
            script.cell_code("!echo {eval(x)!r} {y:>10} {f(a)!=b!r}"),
        )

    def test_line_magic_keeps_the_python_in_braces(self) -> None:
        """The expressions in braces in a line magic's arguments follow the magic's line, with its indentation."""
        self.assertEqual(
            "pass  # %cd /data/{eval(input())}\n(eval(input()))",
            script.cell_code("%cd /data/{eval(input())}"),
        )
        self.assertEqual(
            "for d in dirs:\n    pass  # %cd /data/{d!s}\n    (d)",
            script.cell_code("for d in dirs:\n    %cd /data/{d!s}"),
        )

    def test_line_magic_without_braces_adds_no_lines(self) -> None:
        """A line magic without braces stays one line, its Python code."""
        self.assertEqual("eval(x)  # %time", script.cell_code("%time eval(x)"))

    def test_help_request_becomes_pass(self) -> None:
        """A help request (obj?, ??obj) becomes ``pass`` with the request as a comment, keeping the indentation."""
        self.assertEqual("pass  # obj?", script.cell_code("obj?"))
        self.assertEqual("pass  # ??np.sum", script.cell_code("??np.sum"))
        self.assertEqual(
            "if ok:\n    pass  # np.sum??", script.cell_code("if ok:\n    np.sum??")
        )

    def test_question_mark_in_a_comment_is_not_a_help_request(self) -> None:
        """A Python line whose comment ends with ``?`` stays unchanged."""
        self.assertEqual("x = 1  # ok?", script.cell_code("x = 1  # ok?"))

    def test_cell_magic_with_python_body_keeps_the_body(self) -> None:
        """The line of a cell magic becomes a comment; a Python body (e.g. %%time) is kept."""
        self.assertEqual(
            "# %%time\nx = eval(y)", script.cell_code(["%%time\n", "x = eval(y)"])
        )
        self.assertEqual(
            "# \n# %%capture out\nprint(1)",
            script.cell_code("\n%%capture out\nprint(1)"),
        )

    def test_python_cell_magic_body_is_converted_line_by_line(self) -> None:
        """The body of a Python cell magic is never commented out as a whole; its IPython lines are converted."""
        self.assertEqual(
            "# %%time\npass  # obj?\neval(input())",
            script.cell_code("%%time\nobj?\neval(input())"),
        )
        self.assertEqual(
            "# %%capture\nf(x)  # %time\npass  # !ls {d}\n(d)",
            script.cell_code("%%capture\n%time f(x)\n!ls {d}"),
        )

    def test_timeit_cell_magic_keeps_its_setup_code(self) -> None:
        """The setup code on the line of %%timeit is kept as Python, after the magic's options."""
        self.assertEqual(
            "x = eval(input())  # %%timeit\nf(x)",
            script.cell_code("%%timeit x = eval(input())\nf(x)"),
        )
        self.assertEqual(
            "x = 1  # %%timeit\nf(x)", script.cell_code("%%timeit -n 10 x = 1\nf(x)")
        )

    def test_time_cell_magic_line_stays_a_comment(self) -> None:
        """For cell magics other than %%timeit, the magic's line stays a comment even if it looks like Python."""
        self.assertEqual("# %%time f()\ng()", script.cell_code("%%time f()\ng()"))

    def test_cell_magic_with_other_body_becomes_comments(self) -> None:
        """The body of a cell magic that is not Python (e.g. %%bash) becomes comments."""
        self.assertEqual(
            "# %%bash\n# cd /tmp && ls", script.cell_code("%%bash\ncd /tmp && ls")
        )

    def test_unknown_cell_magic_becomes_comments(self) -> None:
        """A cell magic that is not known to run Python (e.g. %%sql) turns the whole cell into comments."""
        self.assertEqual("# %%sql\n# SELECT 1", script.cell_code("%%sql\nSELECT 1"))


class BracedExpressionsTest(unittest.TestCase):
    """Checks which texts in braces are taken as the Python expressions that IPython evaluates."""

    def test_expressions_in_order_without_conversion_or_format(self) -> None:
        """Each text in braces yields its longest start that is Python, cut at ``!`` or ``:``."""
        self.assertEqual(
            ["eval(x)", "y", "f(a)!=b", "d[1:2]"],
            script.braced_expressions("{eval(x)!r} {y:>10} {f(a)!=b!r} {d[1:2]}"),
        )

    def test_expressions_in_a_nested_format_are_taken_too(self) -> None:
        """An expression in braces inside a format (``{x:{eval(y)}}``) is taken after the outer one."""
        self.assertEqual(['d["k"]', "w"], script.braced_expressions('{d["k"]:>{w}}'))
        self.assertEqual(["x", "eval(y)"], script.braced_expressions("{x:{eval(y)}}"))
        self.assertEqual(["a", "b", "c"], script.braced_expressions("{a} {b!r:{c}}"))
        self.assertEqual(
            "pass  # !printf {x:{eval(y)}}\n(x)\n(eval(y))",
            script.cell_code("!printf {x:{eval(y)}}"),
        )

    def test_text_that_is_not_python_yields_nothing(self) -> None:
        """Text without braces, empty braces, and braces around non-Python text yield no expression."""
        self.assertEqual([], script.braced_expressions(""))
        self.assertEqual([], script.braced_expressions("ls -l /tmp"))
        self.assertEqual([], script.braced_expressions("echo {} {print $1} {!r}"))


class NotebookCodeTest(unittest.TestCase):
    """Checks that the code cells of a notebook become one Python file."""

    def test_code_cells_in_order_other_cells_left_out(self) -> None:
        """Code cells appear in their order, separated by a blank line; markdown and raw cells are left out."""
        with tempfile.TemporaryDirectory() as directory:
            path: Path = write_notebook(
                Path(directory),
                "example.ipynb",
                [
                    {"cell_type": "code", "source": ["x = 1\n", "y = 2"]},
                    {"cell_type": "markdown", "source": ["# A heading"]},
                    {"cell_type": "raw", "source": ["eval(input())"]},
                    {"cell_type": "code", "source": "print(x + y)"},
                ],
            )
            self.assertEqual(
                "x = 1\ny = 2\n\nprint(x + y)\n", script.notebook_code(path)
            )

    def test_nosemgrep_comment_stays_before_its_line(self) -> None:
        """A ``# nosemgrep`` comment stays directly before the code it refers to."""
        with tempfile.TemporaryDirectory() as directory:
            path: Path = write_notebook(
                Path(directory),
                "annotated.ipynb",
                [
                    {
                        "cell_type": "code",
                        "source": [
                            "# nosemgrep: python.lang.security.audit.eval-detected.eval-detected\n",
                            "eval('1 + 1')",
                        ],
                    }
                ],
            )
            self.assertEqual(
                "# nosemgrep: python.lang.security.audit.eval-detected.eval-detected\n"
                "eval('1 + 1')\n",
                script.notebook_code(path),
            )

    def test_invalid_notebook_fails(self) -> None:
        """A file that is not a notebook raises an error instead of being skipped."""
        with tempfile.TemporaryDirectory() as directory:
            broken: Path = Path(directory) / "broken.ipynb"
            broken.write_text("not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                script.notebook_code(broken)

    def test_all_notebooks_of_the_repository_parse(self) -> None:
        """The code of every notebook in the repository is valid Python after the conversion."""
        paths: list[Path] = sorted(
            path
            for path in REPO_ROOT.glob("notebooks/**/*.ipynb")
            if ".ipynb_checkpoints" not in path.parts
        )
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(notebook=str(path.relative_to(REPO_ROOT))):
                ast.parse(script.notebook_code(path))


class MainTest(unittest.TestCase):
    """Checks the command line of the script."""

    def test_writes_one_file_per_notebook_keeping_folders(self) -> None:
        """Every notebook is written to its own path below the output directory, so equal names do not clash."""
        with tempfile.TemporaryDirectory() as directory:
            root: Path = Path(directory)
            (root / "a").mkdir()
            (root / "b").mkdir()
            first: Path = write_notebook(
                root / "a", "same.ipynb", [{"cell_type": "code", "source": "a = 1"}]
            )
            second: Path = write_notebook(
                root / "b", "same.ipynb", [{"cell_type": "code", "source": "b = 2"}]
            )
            output: Path = root / "out"
            with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()):
                status: int = script.main(
                    [
                        str(output),
                        str(first.relative_to(root)),
                        str(second.relative_to(root)),
                    ]
                )
            self.assertEqual(0, status)
            self.assertEqual(
                "a = 1\n", (output / "a" / "same.py").read_text(encoding="utf-8")
            )
            self.assertEqual(
                "b = 2\n", (output / "b" / "same.py").read_text(encoding="utf-8")
            )

    def test_two_notebooks_for_the_same_file_fail(self) -> None:
        """If two notebooks would be written to the same file, the script stops with exit status 1."""
        with tempfile.TemporaryDirectory() as directory:
            notebook: Path = write_notebook(
                Path(directory),
                "same.ipynb",
                [{"cell_type": "code", "source": "a = 1"}],
            )
            errors: io.StringIO = io.StringIO()
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(errors),
            ):
                status: int = script.main(
                    [str(Path(directory) / "out"), str(notebook), str(notebook)]
                )
            self.assertEqual(1, status)
            self.assertIn("exists already", errors.getvalue())

    def test_code_that_is_not_valid_python_fails(self) -> None:
        """If a notebook's code is not valid Python after the conversion, the script fails and writes no file."""
        with tempfile.TemporaryDirectory() as directory:
            notebook: Path = write_notebook(
                Path(directory),
                "continued.ipynb",
                [{"cell_type": "code", "source": ["%time f(x, \\\n", "y)"]}],
            )
            output: Path = Path(directory) / "out"
            errors: io.StringIO = io.StringIO()
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(errors),
            ):
                status: int = script.main([str(output), str(notebook)])
            self.assertEqual(1, status)
            self.assertIn("not valid Python", errors.getvalue())
            self.assertIn("line 2 of the converted code", errors.getvalue())
            self.assertFalse((output / "continued.py").exists())

    def test_without_notebooks_writes_nothing(self) -> None:
        """With an output directory but no notebooks, the script writes nothing and succeeds."""
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(0, script.main([str(Path(directory) / "out")]))
            self.assertFalse((Path(directory) / "out").exists())

    def test_without_arguments_fails(self) -> None:
        """Without an output directory the script prints its usage and exits with status 2."""
        usage: io.StringIO = io.StringIO()
        with contextlib.redirect_stderr(usage):
            self.assertEqual(2, script.main([]))
        self.assertIn("OUTPUT_DIR", usage.getvalue())


if __name__ == "__main__":
    unittest.main()
