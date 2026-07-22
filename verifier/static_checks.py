from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from verifier.models import StaticCheckResult, TaskManifest


@dataclass(frozen=True)
class Token:
    value: str
    line: int
    column: int
    depth: int
    line_start: bool


TOP_LEVEL_PROHIBITED = frozenset(
    {
        "import",
        "prelude",
        "module",
        "axiom",
        "axioms",
        "unsafe",
        "extern",
        "foreign",
        "initialize",
        "elab",
        "syntax",
        "macro",
        "set_option",
        "run_tac",
    }
)
ANYWHERE_PROHIBITED = frozenset(
    {
        "import",
        "prelude",
        "module",
        "axiom",
        "axioms",
        "constant",
        "constants",
        "sorry",
        "admit",
        "sorryAx",
        "initialize",
        "builtin_initialize",
        "elab",
        "elab_rules",
        "syntax",
        "syntax_rules",
        "declare_syntax_cat",
        "macro",
        "macro_rules",
        "set_option",
        "include_str",
        "include_bytes",
        "implemented_by",
        "run_tac",
        "run_cmd",
        "unsafe",
        "extern",
        "foreign",
    }
)


def lean_tokens(text: str) -> tuple[Token, ...]:
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    depth = 0
    line_start = True
    block_depth = 0
    length = len(text)
    while index < length:
        char = text[index]
        pair = text[index : index + 2]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                index += 2
                column += 2
            elif pair == "-/":
                block_depth -= 1
                index += 2
                column += 2
            elif char == "\n":
                index += 1
                line += 1
                column = 1
                line_start = True
            else:
                index += 1
                column += 1
            continue
        if pair == "/-":
            block_depth = 1
            index += 2
            column += 2
            continue
        if pair == "--":
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline
            continue
        if char in {'"', '\''}:
            quote = char
            index += 1
            column += 1
            while index < length:
                current = text[index]
                if current == "\\":
                    index += 2
                    column += 2
                elif current == quote:
                    index += 1
                    column += 1
                    break
                elif current == "\n":
                    index += 1
                    line += 1
                    column = 1
                else:
                    index += 1
                    column += 1
            continue
        if char == "\n":
            index += 1
            line += 1
            column = 1
            line_start = True
            continue
        if char.isspace():
            index += 1
            column += 1
            continue
        start_column = column
        start_line = line
        at_start = line_start
        if char.isalpha() or char == "_" or ord(char) > 127:
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] in "_'." or ord(text[end]) > 127):
                end += 1
            value = text[index:end]
            tokens.append(Token(value, start_line, start_column, depth, at_start))
            column += end - index
            index = end
            line_start = False
            continue
        if char in "([{":
            tokens.append(Token(char, start_line, start_column, depth, at_start))
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
            tokens.append(Token(char, start_line, start_column, depth, at_start))
        else:
            tokens.append(Token(char, start_line, start_column, depth, at_start))
        index += 1
        column += 1
        line_start = False
    return tuple(tokens)


def _literal_answer(tokens: tuple[Token, ...], policy: Mapping[str, object]) -> tuple[str | None, str | None]:
    syntax = policy.get("syntax")
    if not syntax:
        return None, None
    try:
        start = next(
            index
            for index in range(len(tokens) - 1)
            if tokens[index].value == "def" and tokens[index + 1].value == "submittedAnswer"
        )
        name = tokens[start + 1].value
        assign = next(index for index in range(start + 2, len(tokens)) if tokens[index].value == ":" and tokens[index + 1].value == "=")
        declaration_starts = {"theorem", "lemma", "def", "example", "namespace", "section", "end"}
        end = next(
            (
                index
                for index in range(assign + 2, len(tokens))
                if tokens[index].depth == 0
                and tokens[index].line_start
                and tokens[index].value in declaration_starts
            ),
            len(tokens),
        )
        body = tuple(token.value for token in tokens[assign + 2 : end] if token.depth == 0)
    except (StopIteration, IndexError):
        return None, "required submittedAnswer definition was not found"
    if name != "submittedAnswer":
        return None, "answer definition must be named submittedAnswer"
    if syntax == "nat_literal":
        if len(body) != 1 or not body[0].isascii() or not body[0].isdigit():
            return None, "submittedAnswer must be a closed natural-number numeral"
    elif syntax == "int_literal":
        valid = (len(body) == 1 and body[0].isascii() and body[0].isdigit()) or (
            len(body) == 2
            and body[0] == "-"
            and body[1].isascii()
            and body[1].isdigit()
        )
        if not valid:
            return None, "submittedAnswer must be a closed signed integer literal"
    elif syntax == "bool_literal":
        if body not in {("true",), ("false",)}:
            return None, "submittedAnswer must be exactly true or false"
    elif syntax == "finite_constructor":
        allowed = tuple(str(x) for x in policy.get("allowed_constructors", ()))
        if len(body) != 1 or body[0] not in allowed:
            return None, f"submittedAnswer must be one allowed constructor: {allowed}"
    return "".join(body), None


def check_submission(text: str, manifest: TaskManifest) -> StaticCheckResult:
    tokens = lean_tokens(text)
    violations: list[str] = []
    for index, token in enumerate(tokens):
        name_parts = tuple(
            part.removeprefix("«").removesuffix("»")
            for part in token.value.removeprefix("_root_.").split(".")
        )
        prohibited = next((name for name in ANYWHERE_PROHIBITED if name in name_parts), None)
        if prohibited is not None:
            violations.append(f"{prohibited} is prohibited at {token.line}:{token.column}")
        if token.depth == 0 and token.line_start and token.value in TOP_LEVEL_PROHIBITED:
            violations.append(f"top-level command {token.value} is prohibited at {token.line}:{token.column}")
        next_value = tokens[index + 1].value if index + 1 < len(tokens) else ""
        if token.depth == 0 and token.value == "#" and next_value != "[":
            violations.append(f"top-level hash command is prohibited at {token.line}:{token.column}")
    target_short = manifest.target_theorem.rsplit(".", 1)[-1]
    target_start = next(
        (
            index
            for index in range(len(tokens) - 1)
            if tokens[index].value in {"theorem", "lemma"} and tokens[index + 1].value == target_short
        ),
        -1,
    )
    target_assign = next(
        (
            index
            for index in range(max(target_start, 0), len(tokens) - 1)
            if tokens[index].value == ":" and tokens[index + 1].value == "="
        ),
        -1,
    )
    for dependency in manifest.forbidden_dependencies:
        short = dependency.rsplit(".", 1)[-1]
        references = tuple(
            index
            for index, token in enumerate(tokens)
            if token.value == dependency
            or token.value == short
            or short in token.value.removeprefix("_root_.").split(".")
        )
        if any(not (target_start <= index < target_assign) for index in references):
            violations.append(f"source declaration dependency is prohibited: {dependency}")
    submitted_answer, answer_error = _literal_answer(tokens, manifest.answer_policy)
    if answer_error:
        violations.append(answer_error)
    return StaticCheckResult(not violations, tuple(dict.fromkeys(violations)), submitted_answer)
