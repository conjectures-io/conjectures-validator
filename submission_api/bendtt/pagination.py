"""Filter-bound high-water keysets; no OFFSET and no unbounded response histories."""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from submission_api.errors import BadRequest
from submission_api.pagination import decode_parts, encode_parts


@dataclass(frozen=True, slots=True)
class Window:
    rows: list[dict[str, object]]
    next_cursor: str | None


async def page(
    session: AsyncSession,
    secret: str,
    scope: str,
    query: str,
    parameters: dict[str, object],
    *,
    key: str,
    limit: int,
    cursor: str | None,
    ascending: bool = False,
    after: int | None = None,
    textual: bool = False,
) -> Window:
    # query and key are operator constants, never caller-provided SQL identifiers.
    binding = hashlib.sha256(
        json.dumps([scope, parameters, ascending], sort_keys=True).encode()
    ).hexdigest()[:24]
    params = dict(parameters)
    if cursor is not None and after is not None:
        raise BadRequest("cursor and after_id are mutually exclusive", reason_code="INVALID_CURSOR")
    last = None
    if cursor:
        claimed, water, last = decode_parts(secret, cursor, version="btt1", count=3)
        if claimed != binding:
            raise BadRequest("cursor does not match this query", reason_code="INVALID_CURSOR")
        if not textual:
            try:
                water, last = int(water), int(last)
                if not 0 <= last <= water < 2**63:
                    raise ValueError("range")
            except ValueError as exc:
                raise BadRequest("invalid cursor", reason_code="INVALID_CURSOR") from exc
    else:
        water = await session.scalar(
            text(f"SELECT {key} FROM ({query}) AS feed ORDER BY {key} DESC LIMIT 1"), params
        )
    if water is None:
        return Window([], None)
    clauses = [f"{key}<=:_water"]
    params["_water"] = water
    if last is not None:
        clauses.append(f"{key}{'>' if ascending else '<'}:_last")
        params["_last"] = last
    if after is not None:
        clauses.append(f"{key}>:_after")
        params["_after"] = after
    params["_limit"] = limit + 1
    result = await session.execute(
        text(
            f"SELECT * FROM ({query}) AS feed WHERE {' AND '.join(clauses)} ORDER BY {key} {'ASC' if ascending else 'DESC'} LIMIT :_limit"
        ),
        params,
    )
    rows = [dict(row) for row in result.mappings()]
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_parts(secret, version="btt1", parts=(binding, str(water), str(rows[-1][key])))
        if more
        else None
    )
    return Window(rows, next_cursor)
