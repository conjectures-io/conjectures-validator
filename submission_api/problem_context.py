"""Editorial context for a target whose parent problem has several questions."""

ERDOS_12_SUMMARY = r"""Erdős problem 12(iii)

Let \(A\) be an infinite set of positive integers with no distinct
\(a,b,c\in A\) satisfying \(a\mid b+c\) and \(b,c>a\).

Must \(\sum_{n\in A}1/n\) converge?

**Open.**

Source: [Erdős Problems, #12](https://www.erdosproblems.com/12).
"""


def problem_summary(theorem: str, source_docstring: str | None) -> str | None:
    if theorem == "Erdos12.erdos_12.parts.iii":
        return ERDOS_12_SUMMARY
    return source_docstring
