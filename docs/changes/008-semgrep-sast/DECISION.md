# 008 — Static analysis in CI with Semgrep
**Date:** 2026-10
**MR:** https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/merge_requests/141
---
## Context

Nothing in the CI checks merge requests for insecure code patterns such as `eval` on input or `pickle.load` on
untrusted files. The sibling project mastersystem gates merge requests with a diff-aware Semgrep job
(`semgrep-sast` in its `pipelines/base.yml`); this feature takes it over, after the secret detection of feature
007. A full scan of this repository (Semgrep 1.173.0, rulesets `p/security-audit` and `p/python`, default
exclusions) covered 110 files and found 4 findings, all intentional teaching code: `pickle` in
`system/query_processing/operators.py` (reading the operator's own storage file) and in
`system/queues/external_queues.py` (the queue spilling its own objects to a file and reading them back), and
`eval` of a predicate from the query plan in `operators.py`.

## Decision

- A job `semgrep_sast` runs in merge-request pipelines only, in the pinned official image
  (`semgrep/semgrep:1.173.0`, by tag and digest), with the rulesets `p/security-audit` and `p/python`. It fails
  only on findings that are new compared with the merge base with the (explicitly fetched) target branch, so older
  code never blocks a merge request.
- Findings are printed to the job log; a copy in GitLab's SAST format is kept as an artifact for a week (GitLab
  shows it in the merge request only on the Ultimate tier).
- `--metrics=off`: no usage data goes to Semgrep's servers. One retry on a script failure, as the job fetches from
  GitLab and downloads the rulesets. It is skipped when a merge request only changes Markdown.
- No `.semgrepignore`: Semgrep's default exclusions apply (e.g. `tests/` and the files in `.gitignore`).
- The code cells of the notebooks a merge request changes are converted to Python and scanned in full (see the
  Scope-shift section below).
- The intentional findings (4 in `system/`, 7 in notebooks) get a `nosemgrep` comment naming the rule, with a
  comment saying why the code is safe, so that a full scan is clean and readers see why `eval` and `pickle` are fine
  there. CONTRIBUTING.md explains this for new findings; reviewers check every new `nosemgrep` comment.

## Consequences
**Positive:** A merge request that adds an insecure pattern (e.g. `eval` of input, unsafe deserialization) fails,
with the finding in the job log. Older code never blocks a merge request.
**Negative:** The rulesets are downloaded at scan time and not pinned, so a changed rule can flag new code without
a change in this repository. Notebooks are scanned only when a merge request changes them, and then in full.
The teaching code now carries 11 `nosemgrep` comments. The job needs network access to Semgrep's registry.

## Out of scope & deferred
- A full (not diff-aware) scan in the nightly pipeline, together with a full Gitleaks scan.
- Pinning the rulesets (e.g. a vendored copy of the rules).
- A local script for the scan (mastersystem has one).

## Alternatives rejected
- GitLab's built-in SAST template: blocking the merge on findings needs the Ultimate tier.
- Switching the rules `avoid-pickle` and `eval-detected` off in the job: new uses of `pickle` and `eval` would then
  never be flagged.
- A `.semgrepignore` as in mastersystem: it would replace Semgrep's default exclusions without adding anything this
  repository needs.
- Bandit: Python only, no diff-aware gating, and it would duplicate what Semgrep's rules cover.

## Scope-shift 2026-10-10

Notebooks are scanned as well. Converted to Python, the 16 notebooks had 7 findings, all intentional: `pickle` of
the notebooks' own sample data in `Bitmaps-and-Bloom-Filters`, `CodeGen` and `External-Merge-Sort`, and `eval` of
a predicate written in `Result-DB`. They get the same `nosemgrep` comments with a reason as the code in `system/`.
The job writes the code cells of the notebooks that a merge request changes to Python files (a small standard-library
script, `.ci/notebook_code.py`, as the Semgrep image has no Jupyter; IPython magics and shell commands become
comments, keeping the Python code that IPython runs) and scans them in full, since Semgrep's comparison with the
merge base only covers files in git. A finding anywhere in a changed notebook thus fails the job; unchanged
notebooks are not scanned, so a new rule in the unpinned rulesets cannot block an unrelated merge request.

<!-- notes below: skill never overwrites past this marker -->

**Note 2026-10-10 (stage):** like the Gitleaks job, the job runs in the stage `prepare` instead of `test`, so that
a finding stops the expensive test stage. As `detect-docs-only` runs in the same stage, the job lists it under
`needs` to receive the `DOCS_ONLY` variable.

**Note 2026-10-10 (verified in CI):** the runner pulls the Semgrep image, and the image has `python3` for
`.ci/notebook_code.py`.
- Python files: green on the merge request (https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547417,
  0 new findings; the existing `eval` and `pickle` lines did not fail it), red on a throwaway commit adding
  `print(eval(input()))` in a new file outside `tests/`
  (https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547436, `eval-detected`, 1 blocking).
- Notebooks: green with the four annotated notebooks changed
  (https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547527, 4 notebooks converted, 0 findings), red
  on a throwaway commit adding a cell with `print(eval(input()))` to `Z-Order-Curve.ipynb`
  (https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547549, `eval-detected` in the converted file,
  1 blocking). Both throwaway commits were dropped again.

**Note 2026-10-10 (review):**
- The Scope-shift section above replaces what the Context, Decision, Consequences and Out-of-scope sections say about
  notebooks: notebooks a merge request changes are scanned, and there are 11 `nosemgrep` comments (4 in `system/`,
  7 in notebooks).
- The notebook conversion keeps the Python code that IPython runs: the code after a line magic (e.g. `%time`), the
  body of a cell magic if it is Python (e.g. `%%time`, `%%capture`), and the expressions in `{...}` of a shell
  command. Commenting all of these out had hidden an `eval` from the scan. Other magics become `pass`, so an
  indented block stays valid; continuation lines that start with `!=` or `%` stay Python. A notebook keeps its
  folders below the output directory; two notebooks for the same output file stop the script.
- Suppressions: the job lists every added line in the changed Python files and notebooks (outside `tests/`) that
  mentions a Semgrep suppression, and fails unless each is a comment line of its own naming a rule; a bare
  suppression or one hidden in a string, both of which Semgrep honours, fail the job. Adding or changing a
  `.semgrepignore` fails it as well.
- The SAST report artifact covers the Python files only; findings in notebooks appear only in the job log.
- Notebook names are passed NUL-separated (`git diff -z`), so names with spaces or non-ASCII characters work.
- The suppression reasons in `operators.py` are worded as rules for the callers, as `Scan` and `Filter` take their
  file and predicate from the caller.

**Note 2026-10-10 (verified in CI after the review):**
- Green at fcf265b: https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547588 (11 suppressions listed
  for review, 4 notebooks converted with their folders, 0 findings).
- Red on a throwaway file with a bare suppression comment above `print(eval(input()))`:
  https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547610 (the suppression guard).
- Red on a throwaway notebook cell `%%time` with `print(eval(input()))`:
  https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/jobs/547623 (`eval-detected` in the converted file).
- Both throwaway commits were dropped again.

**Note 2026-10-12 (second review):**
- The sections above the Scope-shift section now describe the current state (notebooks scanned, 11 suppressions).
- Notebook conversion: text in `{...}` of a shell command is kept as code only if it is a Python expression (not
  e.g. `awk '{print $1}'`); the body of a cell magic is kept for the magics whose body IPython runs as Python
  (`%%time`, `%%timeit`, `%%capture`, ...) and commented out for all others, instead of commenting out any body that
  does not parse; the setup code on the first line of `%%timeit` is kept; help requests (`obj?`) become `pass`.
  If a notebook's code is still not valid Python, the script fails, since Semgrep would skip that code with only a
  warning and the job would stay green.
- Suppressions in Python files (`.py`, `.pyi`) are read only from the diff's hunks, so a file path containing
  "nosem" no longer counts. Suppressions in notebooks are checked on the converted code instead of the notebook's
  JSON, so the way Jupyter stores a cell no longer matters. When suppressions are found, Semgrep runs once more
  with `--disable-nosem` and lists the findings they hide, for the reviewers, without failing the job.
- A changed file that matches a `.gitignore` fails the job, as Semgrep may skip it although git tracks it.
- The fetch of the target branch and the merge base are shared with the Gitleaks job (`.merge_base`, used via
  `!reference`); the Semgrep options are one job variable, `SCAN_OPTIONS`.
- CONTRIBUTING.md: the local check clears `/tmp/notebook_code` first and runs the script with `uv run python`
  (the project's Python 3.12; an older Python cannot read some notebooks' f-strings).
- The expressions in `{...}` are also taken from line magics (`%cd {path}`), and with a conversion or format
  (`{x!r}`, `{x:>10}`), as IPython evaluates them there, too.
- `GIT_ATTR_SOURCE` as in the Gitleaks job, so that a `.gitattributes` entry such as `*.py -diff` in the merge
  request cannot hide its added suppressions from `git diff`. `--max-target-bytes=0`, so that Semgrep does not skip
  files over 1 MB.
- Known gaps, documented in CONTRIBUTING.md: the code after `%timeit -s`, the bodies of `%%writefile` and
  `%%script python3`, and suppressions in files other than Python files and notebooks are not checked.
