"""Editorial context for a target whose parent problem has several questions."""

ERDOS_12_SUMMARY = r"""Erdős problem 12 — all three questions

Let \(A\) be an infinite set of positive integers with no distinct
\(a,b,c\in A\) satisfying \(a\mid b+c\) and \(b,c>a\).

1. Is there such a set with
   \(\liminf_{N\to\infty}|A\cap\{1,\ldots,N\}|/\sqrt N>0\)?
   **Solved: yes.** DeepMind supplied a construction.
2. Is there an absolute \(c>0\) such that every such set has
   \(|A\cap\{1,\ldots,N\}|<N^{1-c}\) for infinitely many \(N\)?
   **Solved: no.** DeepMind supplied a counterexample construction.
3. Must \(\sum_{n\in A}1/n\) converge?
   **Open.** This is the remaining question and the reward target on this page.

A published claim for part (iii) assumes an additional growth axiom that is
false for a standard good set; it does not resolve the question. The two
previously solved parts are included for context, not as new reward targets.

Source: [Erdős Problems, #12](https://www.erdosproblems.com/12).
"""


def problem_summary(theorem: str, source_docstring: str | None) -> str | None:
    if theorem == "Erdos12.erdos_12.parts.iii":
        return ERDOS_12_SUMMARY
    return source_docstring
