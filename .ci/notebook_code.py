#!/usr/bin/env python3
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

"""Write the code cells of notebooks as Python files, so that Semgrep can scan them.

Semgrep does not read notebooks (``.ipynb`` files are JSON). The CI job ``semgrep_sast``
(see ``.gitlab-ci.yml``) therefore calls this script with an output directory and the
notebooks a merge request changes::

    python3 .ci/notebook_code.py OUTPUT_DIR NOTEBOOK.ipynb [NOTEBOOK.ipynb ...]

For every notebook it writes ``OUTPUT_DIR/<path of the notebook>.py`` with the notebook's
code cells in order, separated by a blank line. IPython syntax that is not Python is turned
into Python that Semgrep can parse, keeping the Python code that IPython runs:

- a line magic (``%name ...``) becomes the Python code at its end if there is any, e.g.
  ``%time eval(x)`` becomes ``eval(x)  # %time``, otherwise ``pass`` with the magic as a
  comment (``pass`` keeps an indented block valid), followed by the Python expressions in
  ``{...}`` as for a shell command;
- a request for help (``obj?``, ``?obj``, ``obj??``) becomes ``pass`` with the request as a
  comment;
- a shell command (``!...`` or ``x = !...``) becomes ``pass`` with the command as a comment,
  followed by the Python expressions in ``{...}``, which IPython evaluates before running it,
  also with a conversion or format as in an f-string (``{x!r}``, ``{x:>10}``); text in braces
  that is not a Python expression, e.g. ``awk '{print $1}'``, stays in the comment only, as
  IPython leaves it unchanged, too;
- a cell magic (``%%name`` in the first non-empty line) becomes a comment. The rest of the cell is kept
  for the magics whose body is Python (``PYTHON_CELL_MAGICS``, e.g. ``%%time``, ``%%capture``),
  with the setup code of ``%%timeit`` from the first line; for all other magics (e.g.
  ``%%bash``) the whole cell becomes comments.

Comments in the cells, such as Semgrep's suppression comments, are kept on the line before the code
they refer to. Raw and markdown cells are left out, as Jupyter does not run them.

If the result is still not valid Python (e.g. a magic that continues on the next line with a
trailing backslash), Semgrep would skip the code it cannot parse with only a warning. The script
therefore stops with an error instead, so that no code drops out of the scan unnoticed.

Not scanned: the setup code of ``%timeit -s "..."``, and the bodies of cell magics that write
or run Python in another way (e.g. ``%%writefile``, ``%%script python3``), which become
comments like ``%%bash``.

Only the standard library is used, as the script runs in the Semgrep image.
"""

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

# a line magic: indentation, an optional assignment (x = %time ...), the magic's name, the rest:
LINE_MAGIC: re.Pattern[str] = re.compile(
    r"^(\s*)([\w.]+\s*=\s*)?%(?!%)([A-Za-z_]\w*)(.*)$"
)
# a shell command: indentation, an optional assignment (x = !ls), then ! (but not !=):
SHELL_COMMAND: re.Pattern[str] = re.compile(r"^(\s*)([\w.]+\s*=\s*)?!(?!=)(.*)$")
# a request for help: indentation, then a name with ? or ?? before or after it (obj?, ??obj):
HELP: re.Pattern[str] = re.compile(
    r"^(\s*)(\?{1,2}[\w.*\[\]]+|%{0,2}[\w.*\[\]]+\?{1,2})\s*$"
)
# a cell magic in the first non-empty line of a cell: the magic's name, the rest:
CELL_MAGIC: re.Pattern[str] = re.compile(r"^\s*%%([A-Za-z_]\w*)(.*)$")
# the cell magics whose body IPython runs as Python code:
PYTHON_CELL_MAGICS: frozenset[str] = frozenset(
    {"time", "timeit", "capture", "prun", "debug", "python", "python3"}
)
# a Python expression in braces in a shell command, e.g. {path}, also with braces nested once
# in its format, e.g. {x:>{width}}:
BRACED_EXPRESSION: re.Pattern[str] = re.compile(r"\{((?:[^{}]|\{[^{}]*\})+)\}")


def parses(code: str) -> bool:
    """Returns True if code is valid Python."""
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def magic_code(rest: str) -> str | None:
    """Returns the Python code at the end of a line magic, or None if there is none.

    The magic's options come first (e.g. ``-n 10`` of ``%timeit``), so leading words are
    dropped one by one until the remainder is valid Python.

    @param rest: the text after the magic's name
    @return: the Python code, or None
    """
    words: list[str] = rest.split()
    for start in range(len(words)):
        candidate: str = " ".join(words[start:])
        if parses(candidate):
            return candidate
    return None


def braced_expressions(text: str) -> list[str]:
    """Returns the Python expressions in braces that IPython evaluates in a command's text.

    IPython fills in ``{expression}`` in the text of a shell command or line magic, also with a
    conversion or format as in an f-string (``{x!r}``, ``{x:>10}``), and leaves text in braces
    that is not a Python expression unchanged. Of each text in braces, the longest start that is
    a Python expression is taken, which drops a conversion or format; expressions in braces in
    the format (``{x:>{width}}``) are taken as well.

    @param text: the text of the command after ``!`` or after the magic's name
    @return: the expressions, in order
    """
    expressions: list[str] = []
    for inner in BRACED_EXPRESSION.findall(text):
        ends: list[int] = [len(inner)] + [
            end for end in range(len(inner) - 1, 0, -1) if inner[end] in "!:"
        ]
        for end in ends:
            if parses(f"({inner[:end]})"):
                expressions.append(inner[:end])
                expressions += braced_expressions(inner[end:])
                break
    return expressions


def convert_line(line: str) -> str:
    """Returns the Python for one line of a code cell (several lines for expressions in braces).

    @param line: a line of the cell, without its newline
    @return: the line itself if it is Python, otherwise its replacement
    """
    magic: re.Match[str] | None = LINE_MAGIC.match(line)
    if magic:
        indent, assignment, name, rest = magic.groups()
        code: str | None = magic_code(rest)
        first: str = (
            f"{indent}pass  # {line.strip()}"
            if code is None
            else f"{indent}{assignment or ''}{code}  # %{name}"
        )
        return "\n".join(
            [first]
            + [f"{indent}({expression})" for expression in braced_expressions(rest)]
        )
    help_request: re.Match[str] | None = HELP.match(line)
    if help_request:
        return f"{help_request.group(1)}pass  # {line.strip()}"
    shell: re.Match[str] | None = SHELL_COMMAND.match(line)
    if shell:
        indent = shell.group(1)
        # IPython evaluates the expressions in braces as Python before running the command:
        return "\n".join(
            [f"{indent}pass  # {line.strip()}"]
            + [
                f"{indent}({expression})"
                for expression in braced_expressions(shell.group(3))
            ]
        )
    return line


def cell_code(source: list[str] | str) -> str:
    """Returns the Python code of a code cell, with IPython-only syntax converted.

    @param source: the cell's ``source`` as stored in the notebook, a list of lines or one string
    @return: the cell's code, with the same lines as the cell (a shell command can add lines)
    """
    text: str = "".join(source) if isinstance(source, list) else source
    lines: list[str] = text.split("\n")
    first: int = next((i for i, line in enumerate(lines) if line.strip()), 0)
    cell_magic: re.Match[str] | None = CELL_MAGIC.match(lines[first])
    if cell_magic is None:
        return "\n".join(convert_line(line) for line in lines)
    name, rest = cell_magic.groups()
    if name not in PYTHON_CELL_MAGICS:
        return "\n".join("# " + line for line in lines)
    # the cell magic's line becomes a comment, except for the setup code that %%timeit runs:
    setup: str | None = magic_code(rest) if name == "timeit" else None
    magic_line: str = (
        f"{setup}  # %%{name}" if setup is not None else "# " + lines[first]
    )
    return "\n".join(
        ["# " + line for line in lines[:first]]
        + [magic_line]
        + [convert_line(line) for line in lines[first + 1 :]]
    )


def notebook_code(notebook_path: Path) -> str:
    """Returns the code cells of a notebook as one Python file.

    @param notebook_path: the path of the ``.ipynb`` file
    @return: the code of all code cells, in order, separated by a blank line, with a final newline
    """
    notebook: dict[str, Any] = json.loads(notebook_path.read_text(encoding="utf-8"))
    return (
        "\n\n".join(
            cell_code(cell["source"])
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
        )
        + "\n"
    )


def main(arguments: list[str]) -> int:
    """Writes the code of every given notebook to the output directory.

    A relative notebook path keeps its folders below the output directory, so notebooks with the
    same name in different folders do not overwrite each other; if two notebooks would still be
    written to the same file, the script stops with an error. It also stops with an error if the
    code of a notebook is not valid Python after the conversion, as Semgrep would then skip part
    of it without failing.

    @param arguments: the output directory, then the notebook paths
    @return: the exit status: 0 on success, 1 if two notebooks map to the same file or a
    notebook's code is not valid Python, 2 on missing arguments
    """
    if len(arguments) < 1:
        print(__doc__, file=sys.stderr)
        return 2
    output_dir: Path = Path(arguments[0])
    for name in arguments[1:]:
        notebook_path: Path = Path(name)
        relative: Path = (
            Path(notebook_path.name) if notebook_path.is_absolute() else notebook_path
        )
        target: Path = output_dir / relative.with_suffix(".py")
        if target.exists():
            print(f"{notebook_path}: {target} exists already", file=sys.stderr)
            return 1
        code: str = notebook_code(notebook_path)
        try:
            ast.parse(code)
        except SyntaxError as error:
            print(
                f"{notebook_path}: the code is not valid Python after the conversion, so "
                f"Semgrep could not scan all of it ({error.msg} in line {error.lineno} of the "
                f"converted code: {(error.text or '').strip()})",
                file=sys.stderr,
            )
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        print(f"{notebook_path} -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
