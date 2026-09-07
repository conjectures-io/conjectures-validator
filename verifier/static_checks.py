from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import re
import unicodedata

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
        "native_decide",
        "set_option",
        "include_str",
        "include_bytes",
        "implemented_by",
        "run_tac",
        "run_cmd",
        "attribute",
        "deriving",
        "compile_inductive",
        "init_quot",
        "docs_to_verso",
        "add_decl_doc",
        "register_tactic_tag",
        "tactic_extension",
        "recommended_spelling",
        "gen_injective_theorems",
        "notation",
        "infix",
        "infixl",
        "infixr",
        "prefix",
        "postfix",
        "mixfix",
        "meta",
        "partial",
        "coinductive",
        "instance",
        "unsafe",
        "extern",
        "foreign",
    }
)

MAX_TOKENS = 200_000
MAX_DELIMITER_DEPTH = 4096
MAX_LINE_LENGTH = 100_000
MAX_LITERAL_DIGITS = 4096


def _lean_name_parts(value: str) -> tuple[str, ...]:
    rooted = value.removeprefix("_root_.")
    segments = re.findall(r"«[^»]*»|[^.]+", rooted)
    return tuple(part.removeprefix("«").removesuffix("»") for part in segments)


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
        # Keep one overflow token so callers can reject without allocating the
        # rest of a hostile file's tokens. Never validate this truncated prefix.
        if len(tokens) > MAX_TOKENS:
            break
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
            if char == '"' and index > 0 and text[index - 1] == "!":
                tokens.append(Token("__interpolated_string__", line, column, depth, line_start))
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
    declaration_keywords = {
        "def",
        "abbrev",
        "opaque",
        "theorem",
        "lemma",
        "instance",
        "inductive",
        "coinductive",
        "structure",
        "class",
    }
    definitions = tuple(
        (index, tokens[index].value)
        for index in range(len(tokens) - 1)
        if tokens[index].value in declaration_keywords
        and _lean_name_parts(tokens[index + 1].value)[-1:] == ("submittedAnswer",)
    )
    if len(definitions) != 1 or definitions[0][1] != "def":
        return None, "exactly one regular def submittedAnswer declaration is required"
    try:
        start = definitions[0][0]
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
        if len(body[0]) > MAX_LITERAL_DIGITS:
            return None, f"submittedAnswer exceeds the {MAX_LITERAL_DIGITS}-digit limit"
    elif syntax == "int_literal":
        valid = (len(body) == 1 and body[0].isascii() and body[0].isdigit()) or (
            len(body) == 2
            and body[0] == "-"
            and body[1].isascii()
            and body[1].isdigit()
        )
        if not valid:
            return None, "submittedAnswer must be a closed signed integer literal"
        if len(body[-1]) > MAX_LITERAL_DIGITS:
            return None, f"submittedAnswer exceeds the {MAX_LITERAL_DIGITS}-digit limit"
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
    if len(tokens) > MAX_TOKENS:
        return StaticCheckResult(
            False, (f"submission exceeds the {MAX_TOKENS}-token policy limit",), None
        )
    if tokens and max(token.depth for token in tokens) > MAX_DELIMITER_DEPTH:
        violations.append(f"submission exceeds the {MAX_DELIMITER_DEPTH}-delimiter nesting limit")
    if any(len(line) > MAX_LINE_LENGTH for line in text.splitlines()):
        violations.append(f"submission exceeds the {MAX_LINE_LENGTH}-character line limit")
    forbidden_unicode = next(
        (
            (index, char)
            for index, char in enumerate(text)
            if unicodedata.category(char) in {"Cf", "Cs", "Co", "Cn"}
        ),
        None,
    )
    if forbidden_unicode is not None:
        index, char = forbidden_unicode
        violations.append(
            f"invisible, private-use, or unassigned Unicode is prohibited at character {index}: U+{ord(char):04X}"
        )
    for index, token in enumerate(tokens):
        if token.value == "__interpolated_string__":
            violations.append(
                f"interpolated strings are prohibited at {token.line}:{token.column}"
            )
            continue
        name_parts = _lean_name_parts(token.value)
        prohibited = next((name for name in name_parts if name in ANYWHERE_PROHIBITED), None)
        if prohibited is not None:
            violations.append(f"{prohibited} is prohibited at {token.line}:{token.column}")
        if token.depth == 0 and token.line_start and token.value in TOP_LEVEL_PROHIBITED:
            violations.append(f"top-level command {token.value} is prohibited at {token.line}:{token.column}")
        next_value = tokens[index + 1].value if index + 1 < len(tokens) else ""
        if token.value == "@" and next_value == "[":
            violations.append(f"declaration attributes are prohibited at {token.line}:{token.column}")
        if token.depth == 0 and token.value == "#" and next_value != "[":
            violations.append(f"top-level hash command is prohibited at {token.line}:{token.column}")
    target_type_ranges = []
    for target_theorem in manifest.theorem_names:
        target_short = target_theorem.rsplit(".", 1)[-1]
        target_start = next(
            (
                index
                for index in range(len(tokens) - 1)
                if tokens[index].value in {"theorem", "lemma"}
                and tokens[index + 1].value == target_short
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
        if target_start >= 0 and target_assign >= 0:
            target_type_ranges.append((target_start, target_assign))
    for dependency in manifest.forbidden_dependencies:
        dependency_parts = _lean_name_parts(dependency)
        short = dependency_parts[-1]
        references = tuple(
            index
            for index, token in enumerate(tokens)
            if _lean_name_parts(token.value) == dependency_parts
            or short in _lean_name_parts(token.value)
        )
        if any(
            not any(start <= index < assign for start, assign in target_type_ranges)
            for index in references
        ):
            violations.append(f"source declaration dependency is prohibited: {dependency}")
    submitted_answer, answer_error = _literal_answer(tokens, manifest.answer_policy)
    if answer_error:
        violations.append(answer_error)
    return StaticCheckResult(not violations, tuple(dict.fromkeys(violations)), submitted_answer)
