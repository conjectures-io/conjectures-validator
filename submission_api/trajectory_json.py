"""Frame artifact-index entries without loading the entire JSON array into memory.

The supplied decoder still checks each value's complete JSON syntax, duplicate keys and
numbers. This scanner only finds array boundaries outside strings and nested containers.
There is no file or entry byte limit; the isolated validator's resource budgets apply.
"""

MAX_JSON_DEPTH = 24
READ_BYTES = 64 * 1024
WHITESPACE = b" \t\r\n"


def array_items(source, decode):
    started, finished = False, False
    expecting, allow_empty = True, True
    quoted, escaped, depth = False, False, 1
    item = bytearray()
    while chunk := source.read(READ_BYTES):
        for ch in chunk:
            if not started:
                if ch in WHITESPACE:
                    continue
                if ch != 91:  # [
                    raise ValueError("artifact-index.json must be a JSON array")
                started = True
                continue
            if finished:
                if ch not in WHITESPACE:
                    raise ValueError("invalid JSON")
                continue
            if expecting:
                if ch in WHITESPACE:
                    continue
                if ch == 93 and allow_empty:  # Empty array.
                    finished = True
                    continue
                if ch in (44, 93):  # Missing value, including a trailing comma.
                    raise ValueError("invalid JSON")
                expecting = False
            if not quoted and depth == 1 and ch in (44, 93):
                value = decode(bytes(item))
                item.clear()
                yield value
                del value
                expecting, allow_empty = True, False
                finished = ch == 93
                continue
            item.append(ch)
            if quoted:
                if escaped:
                    escaped = False
                elif ch == 92:
                    escaped = True
                elif ch == 34:
                    quoted = False
            elif ch == 34:
                quoted = True
            elif ch in (91, 123):
                depth += 1
                if depth > MAX_JSON_DEPTH:
                    raise ValueError(f"JSON nesting exceeds {MAX_JSON_DEPTH} levels")
            elif ch in (93, 125):
                depth -= 1
                if depth < 1:
                    raise ValueError("invalid JSON")
    if not finished:
        raise ValueError("invalid JSON")
