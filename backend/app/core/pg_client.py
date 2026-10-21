"""
Postgres client with supabase-py's query interface (21/10/2026)
================================================================

VidGrab moves its data from Supabase to the Postgres Vibe Host gives the
backend (docs/MIGRATION-DB-VIBEHOST.md). The code calls the database through
supabase-py in ~576 places, with a small set of builder calls:

    db.table("x").select("a, b", count="exact").eq("k", v).order("t", desc=True)
      .range(0, 9).execute()            -> .data (list of dicts), .count
    .insert(row|rows) .update(values) .upsert(rows, on_conflict=, ignore_duplicates=)
    .delete()  .single() .maybe_single()  db.rpc("fn", {...}).execute()
    filters: eq neq gt gte lt lte like ilike is_ in_ contains or_ not_.<filter> filter

PgClient speaks that interface over plain SQL (psycopg2, parameterised), so
those call sites stay as they are: database.get_*_client() returns a PgClient
when DB_BACKEND=postgres. Results are shaped like PostgREST's JSON (ISO
strings for timestamps, str for uuid, float for numeric). Errors raise
postgrest's APIError with the Postgres SQLSTATE as code, as PostgREST did.

`.auth` / `.storage` are not data: they go to the real Supabase client until
sign-in moves too (phase 2).

Identifiers are checked against the table's real columns (catalog, cached per
process) before they reach SQL; values only ever travel as parameters.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from postgrest.exceptions import APIError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EMBED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$")
_OPS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
        "like": "LIKE", "ilike": "ILIKE"}


class Response:
    """APIResponse look-alike: .data and .count."""

    def __init__(self, data: Any, count: Optional[int] = None):
        self.data = data
        self.count = count

    def __repr__(self) -> str:  # pragma: no cover
        return f"Response(data={self.data!r}, count={self.count!r})"


def _api_error(exc: Exception) -> APIError:
    code = getattr(exc, "pgcode", None) or "PGRST000"
    diag = getattr(exc, "diag", None)
    return APIError({
        "message": (getattr(diag, "message_primary", None) or str(exc).strip())[:500],
        "code": code,
        "details": getattr(diag, "message_detail", None),
        "hint": getattr(diag, "message_hint", None),
    })


def _out(v: Any) -> Any:
    """A column value as PostgREST's JSON would give it."""
    if isinstance(v, _dt.datetime):
        return v.isoformat()
    if isinstance(v, (_dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2 ** 53 else float(v)
    if isinstance(v, (bytes, memoryview)):
        return "\\x" + bytes(v).hex()
    if isinstance(v, list):
        return [_out(x) for x in v]
    if isinstance(v, dict):
        return {k: _out(x) for k, x in v.items()}
    return v


# ── connection pool + catalog, per process (Celery forks) ───────────────────

class _Pool:
    def __init__(self, dsn: str, maxconn: int):
        self.dsn, self.maxconn = dsn, maxconn
        self._pid = None
        self._pool = None
        self._lock = threading.Lock()
        self.columns: Dict[str, Dict[str, str]] = {}      # table -> {col: sql type}
        self.pkeys: Dict[str, List[str]] = {}
        self.fkeys: Dict[Tuple[str, str], Tuple[str, str, str]] = {}  # (a, b) -> (holder, col, refcol)
        self.funcs: Dict[str, Tuple[bool, str, Dict[str, str]]] = {}

    def _get(self):
        from psycopg2 import pool as pgpool  # noqa: PLC0415
        if self._pool is None or self._pid != os.getpid():
            with self._lock:
                if self._pool is None or self._pid != os.getpid():
                    self._pool = pgpool.ThreadedConnectionPool(
                        1, self.maxconn, self.dsn, connect_timeout=10,
                        options="-c timezone=UTC -c statement_timeout=30000")
                    self._pid = os.getpid()
                    self.columns.clear()
                    self.pkeys.clear()
                    self.fkeys.clear()
                    self.funcs.clear()
        return self._pool

    def run(self, sql: str, params: Sequence[Any] = (), fetch: bool = True):
        import psycopg2  # noqa: PLC0415
        import psycopg2.extras  # noqa: PLC0415
        pool = self._get()
        conn = pool.getconn()
        broken = False
        try:
            conn.autocommit = True
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()] if fetch and cur.description else []
        except psycopg2.Error as exc:
            broken = conn.closed != 0 or isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))
            raise _api_error(exc) from exc
        finally:
            pool.putconn(conn, close=broken)

    # catalog
    def table_columns(self, table: str) -> Dict[str, str]:
        if table not in self.columns:
            rows = self.run(
                """SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type
                     FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                     JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped""",
                (table,))
            if not rows:
                raise APIError({"message": f'relation "public.{table}" does not exist', "code": "42P01",
                                "details": None, "hint": None})
            self.columns[table] = {r["name"]: r["type"] for r in rows}
        return self.columns[table]

    def primary_key(self, table: str) -> List[str]:
        if table not in self.pkeys:
            rows = self.run(
                """SELECT a.attname AS name FROM pg_index i
                     JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = ('public.' || quote_ident(%s))::regclass AND i.indisprimary""",
                (table,))
            self.pkeys[table] = [r["name"] for r in rows]
        return self.pkeys[table]

    def foreign_key(self, base: str, other: str) -> Optional[Tuple[str, str, str]]:
        """(holder table, holder column, referenced column) linking base and other."""
        key = (base, other)
        if key not in self.fkeys:
            rows = self.run(
                """SELECT cl.relname AS holder, a.attname AS col, ra.attname AS refcol
                     FROM pg_constraint k
                     JOIN pg_class cl ON cl.oid = k.conrelid
                     JOIN pg_class rc ON rc.oid = k.confrelid
                     JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
                     JOIN pg_attribute ra ON ra.attrelid = k.confrelid AND ra.attnum = k.confkey[1]
                    WHERE k.contype = 'f' AND ((cl.relname = %s AND rc.relname = %s)
                                            OR (cl.relname = %s AND rc.relname = %s))
                    ORDER BY (cl.relname = %s) DESC LIMIT 1""",
                (base, other, other, base, base))
            self.fkeys[key] = (rows[0]["holder"], rows[0]["col"], rows[0]["refcol"]) if rows else None
        return self.fkeys[key]

    def function(self, name: str) -> Tuple[bool, str, Dict[str, str]]:
        """(returns set, 'composite'|'scalar'|'void', {arg: type})."""
        if name not in self.funcs:
            rows = self.run(
                """SELECT p.proretset AS retset, t.typtype AS typtype, t.typname AS typname,
                          p.proargnames AS argnames,
                          ARRAY(SELECT format_type(x, NULL) FROM unnest(p.proargtypes) x) AS argtypes
                     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                     JOIN pg_type t ON t.oid = p.prorettype
                    WHERE n.nspname = 'public' AND p.proname = %s LIMIT 1""",
                (name,))
            if not rows:
                raise APIError({"message": f"function public.{name} does not exist", "code": "PGRST202",
                                "details": None, "hint": None})
            r = rows[0]
            kind = "void" if r["typname"] == "void" else ("composite" if r["typtype"] == "c" else "scalar")
            names = list(r["argnames"] or [])
            self.funcs[name] = (bool(r["retset"]), kind, dict(zip(names, r["argtypes"])))
        return self.funcs[name]


def _param(value: Any, sql_type: str) -> Tuple[str, Any]:
    """Placeholder + value for a column of sql_type."""
    from psycopg2.extras import Json  # noqa: PLC0415
    if value is None:
        return f"%s::{sql_type}", None
    if sql_type in ("json", "jsonb"):
        return f"%s::{sql_type}", Json(value)
    if sql_type.endswith("[]"):
        return f"%s::{sql_type}", list(value)
    if isinstance(value, (dict, list)):
        return "%s", Json(value)
    return f"%s::{sql_type}", value


def _qi(name: str) -> str:
    if not _IDENT.match(name):
        raise APIError({"message": f"bad identifier {name!r}", "code": "PGRST100", "details": None, "hint": None})
    return f'"{name}"'


# ── query builder ────────────────────────────────────────────────────────────

class _Not:
    def __init__(self, q: "Query"):
        self.q = q

    def __getattr__(self, name):
        method = getattr(self.q, name)

        def call(*a, **kw):
            self.q._negate = True
            return method(*a, **kw)
        return call


class Query:
    def __init__(self, pool: _Pool, table: str):
        self._pool = pool
        self._table = table
        self._action = "select"
        self._columns = "*"
        self._count: Optional[str] = None
        self._payload: Any = None
        self._on_conflict: Optional[str] = None
        self._ignore_dup = False
        self._default_to_null = True
        self._filters: List[Tuple[str, List[Any]]] = []
        self._order: List[str] = []
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._single: Optional[str] = None     # "single" | "maybe"
        self._negate = False
        self._returning = True

    # actions
    def select(self, *columns: str, count: Optional[str] = None, head: bool = False):
        self._action = "select"
        self._columns = ",".join(columns) if columns else "*"
        self._count = count
        self._head = head
        return self

    def insert(self, json_: Any, *, count=None, returning="representation", upsert=False,
               default_to_null=True, **_):
        self._action, self._payload = "insert", json_
        self._returning = returning != "minimal"
        self._count = count
        if upsert:
            self._action = "upsert"
        return self

    def upsert(self, json_: Any, *, count=None, returning="representation", ignore_duplicates=False,
               on_conflict: str = "", default_to_null=True, **_):
        self._action, self._payload = "upsert", json_
        self._returning = returning != "minimal"
        self._count = count
        self._ignore_dup = bool(ignore_duplicates)
        self._on_conflict = on_conflict or None
        return self

    def update(self, json_: Dict[str, Any], *, count=None, returning="representation", **_):
        self._action, self._payload = "update", json_
        self._returning = returning != "minimal"
        self._count = count
        return self

    def delete(self, *, count=None, returning="representation", **_):
        self._action = "delete"
        self._returning = returning != "minimal"
        self._count = count
        return self

    # filters
    @property
    def not_(self):
        return _Not(self)

    def _col(self, column: str) -> Tuple[str, str]:
        cols = self._pool.table_columns(self._table)
        if column not in cols:
            raise APIError({"message": f'column {self._table}.{column} does not exist', "code": "42703",
                            "details": None, "hint": None})
        return _qi(column), cols[column]

    def _add(self, sql: str, params: List[Any]):
        if self._negate:
            sql = f"NOT ({sql})"
            self._negate = False
        self._filters.append((sql, params))
        return self

    def _cmp(self, column: str, op: str, value: Any):
        col, typ = self._col(column)
        if op in ("LIKE", "ILIKE"):
            return self._add(f"{col}::text {op} %s", [str(value).replace("*", "%")])
        if isinstance(value, bool) or value is None:
            if value is None:
                return self._add(f"{col} {op} NULL", [])
        ph, v = _param(value, typ)
        return self._add(f"{col} {op} {ph}", [v])

    def eq(self, column, value):
        return self._cmp(column, "=", value)

    def neq(self, column, value):
        return self._cmp(column, "<>", value)

    def gt(self, column, value):
        return self._cmp(column, ">", value)

    def gte(self, column, value):
        return self._cmp(column, ">=", value)

    def lt(self, column, value):
        return self._cmp(column, "<", value)

    def lte(self, column, value):
        return self._cmp(column, "<=", value)

    def like(self, column, pattern):
        return self._cmp(column, "LIKE", pattern)

    def ilike(self, column, pattern):
        return self._cmp(column, "ILIKE", pattern)

    def is_(self, column, value):
        col, _ = self._col(column)
        v = {None: "NULL", "null": "NULL", True: "TRUE", "true": "TRUE", False: "FALSE", "false": "FALSE"}.get(
            value if not isinstance(value, str) else value.lower())
        if v is None:
            raise APIError({"message": f"is_ needs null/true/false, got {value!r}", "code": "PGRST100",
                            "details": None, "hint": None})
        return self._add(f"{col} IS {v}", [])

    def in_(self, column, values):
        col, typ = self._col(column)
        values = list(values)
        if not values:
            return self._add("FALSE", [])
        base = typ[:-2] if typ.endswith("[]") else typ
        return self._add(f"{col} = ANY(%s::{base}[])", [values])

    def contains(self, column, value):
        col, typ = self._col(column)
        ph, v = _param(value, typ)
        return self._add(f"{col} @> {ph}", [v])

    def filter(self, column, operator, criteria):
        op = operator.lower()
        if op == "in":
            vals = [x.strip().strip('"') for x in str(criteria).strip("()").split(",") if x.strip()]
            return self.in_(column, vals)
        if op == "is":
            return self.is_(column, criteria)
        if op in _OPS:
            return self._cmp(column, _OPS[op], criteria)
        raise APIError({"message": f"filter operator {operator!r} not supported", "code": "PGRST100",
                        "details": None, "hint": None})

    def or_(self, filters: str, reference_table: Optional[str] = None):
        """PostgREST "a.eq.1,b.is.null" (top-level commas only, simple ops)."""
        parts, depth, cur = [], 0, ""
        for ch in filters:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        if cur:
            parts.append(cur)
        sub = Query(self._pool, self._table)
        for p in parts:
            column, op, val = p.split(".", 2)
            if op == "not":
                op2, val2 = val.split(".", 1)
                sub._negate = True
                sub.filter(column, op2, val2)
            else:
                sub.filter(column, op, val)
        if not sub._filters:
            return self
        sql = " OR ".join(f"({s})" for s, _ in sub._filters)
        params = [x for _, ps in sub._filters for x in ps]
        return self._add(sql, params)

    def match(self, query: Dict[str, Any]):
        for k, v in query.items():
            self.eq(k, v)
        return self

    # modifiers
    def order(self, column: str, *, desc: bool = False, nullsfirst: Optional[bool] = None, foreign_table=None):
        col, _ = self._col(column)
        nulls = ""
        if nullsfirst is not None:
            nulls = " NULLS FIRST" if nullsfirst else " NULLS LAST"
        self._order.append(f"{col} {'DESC' if desc else 'ASC'}{nulls}")
        return self

    def limit(self, size: int, *, foreign_table=None):
        self._limit = int(size)
        return self

    def offset(self, size: int):
        self._offset = int(size)
        return self

    def range(self, start: int, end: int, foreign_table=None):
        self._offset = int(start)
        self._limit = int(end) - int(start) + 1
        return self

    def single(self):
        self._single = "single"
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    # SQL
    def _where(self) -> Tuple[str, List[Any]]:
        if not self._filters:
            return "", []
        return " WHERE " + " AND ".join(f"({s})" for s, _ in self._filters), [p for _, ps in self._filters for p in ps]

    def _select_list(self) -> str:
        cols = self._pool.table_columns(self._table)
        out = []
        for raw in _split_top(self._columns):
            item = raw.strip()
            if not item:
                continue
            if item == "*":
                out.append("t.*")
                continue
            m = _EMBED.match(item)
            if m:
                out.append(self._embed(m.group(1), m.group(2)))
                continue
            if item not in cols:
                raise APIError({"message": f'column {self._table}.{item} does not exist', "code": "42703",
                                "details": None, "hint": None})
            out.append(f"t.{_qi(item)}")
        return ", ".join(out) or "t.*"

    def _embed(self, other: str, inner: str) -> str:
        link = self._pool.foreign_key(self._table, other)
        if not link:
            raise APIError({"message": f"no relationship between {self._table} and {other}", "code": "PGRST200",
                            "details": None, "hint": None})
        holder, col, refcol = link
        sub_cols = "r.*" if inner.strip() in ("", "*") else ", ".join(
            f"r.{_qi(c.strip())}" for c in inner.split(","))
        if holder == self._table:     # many-to-one: an object
            return (f"(SELECT to_jsonb(x) FROM (SELECT {sub_cols} FROM public.{_qi(other)} r "
                    f"WHERE r.{_qi(refcol)} = t.{_qi(col)} LIMIT 1) x) AS {_qi(other)}")
        return (f"(SELECT coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) FROM (SELECT {sub_cols} "
                f"FROM public.{_qi(other)} r WHERE r.{_qi(col)} = t.{_qi(refcol)}) x) AS {_qi(other)}")

    def _rows_payload(self) -> List[Dict[str, Any]]:
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        return [r for r in rows if isinstance(r, dict)]

    def _returning_sql(self) -> str:
        return " RETURNING *" if self._returning else ""

    def execute(self):
        if self._action == "select":
            return self._exec_select()
        if self._action in ("insert", "upsert"):
            return self._exec_insert()
        if self._action == "update":
            return self._exec_update()
        return self._exec_delete()

    def _count_rows(self, where: str, params: List[Any]) -> int:
        rows = self._pool.run(f"SELECT count(*) AS n FROM public.{_qi(self._table)} t{where}", params)
        return int(rows[0]["n"])

    def _finish(self, rows: List[Dict[str, Any]], count: Optional[int]):
        data = [{k: _out(v) for k, v in r.items()} for r in rows]
        if self._single:
            if len(data) != 1:
                if self._single == "maybe" and not data:
                    return None
                raise APIError({"message": "JSON object requested, multiple (or no) rows returned",
                                "code": "PGRST116", "details": f"The result contains {len(data)} rows",
                                "hint": None})
            return Response(data[0], count)
        return Response(data, count)

    def _exec_select(self):
        where, params = self._where()
        count = self._count_rows(where, params) if self._count else None
        if getattr(self, "_head", False):
            return Response([], count)
        sql = f"SELECT {self._select_list()} FROM public.{_qi(self._table)} t{where}"
        if self._order:
            sql += " ORDER BY " + ", ".join(f"t.{o}" for o in self._order)
        if self._limit is not None:
            sql += f" LIMIT {int(self._limit)}"
        elif self._single == "single":
            sql += " LIMIT 2"
        if self._offset:
            sql += f" OFFSET {int(self._offset)}"
        return self._finish(self._pool.run(sql, params), count)

    def _exec_insert(self):
        rows = self._rows_payload()
        if not rows:
            return Response([], 0 if self._count else None)
        cols_t = self._pool.table_columns(self._table)
        names: List[str] = []
        for r in rows:
            for k in r:
                if k not in names:
                    if k not in cols_t:
                        raise APIError({"message": f"Could not find the '{k}' column of '{self._table}'",
                                        "code": "PGRST204", "details": None, "hint": None})
                    names.append(k)
        values_sql, params = [], []
        for r in rows:
            phs = []
            for k in names:
                if k in r:
                    ph, v = _param(r[k], cols_t[k])
                    phs.append(ph)
                    params.append(v)
                else:
                    phs.append("DEFAULT")
            values_sql.append("(" + ", ".join(phs) + ")")
        sql = (f"INSERT INTO public.{_qi(self._table)} AS t ({', '.join(_qi(n) for n in names)}) "
               f"VALUES {', '.join(values_sql)}")
        if self._action == "upsert":
            target = [c.strip() for c in self._on_conflict.split(",")] if self._on_conflict \
                else self._pool.primary_key(self._table)
            if not target:
                raise APIError({"message": f"upsert on {self._table} needs on_conflict", "code": "42P10",
                                "details": None, "hint": None})
            sql += f" ON CONFLICT ({', '.join(_qi(c) for c in target)})"
            upd = [n for n in names if n not in target]
            if self._ignore_dup or not upd:
                sql += " DO NOTHING"
            else:
                sql += " DO UPDATE SET " + ", ".join(f"{_qi(n)} = EXCLUDED.{_qi(n)}" for n in upd)
        sql += self._returning_sql()
        out = self._pool.run(sql, params, fetch=self._returning)
        return Response([{k: _out(v) for k, v in r.items()} for r in out], len(out) if self._count else None)

    def _exec_update(self):
        cols_t = self._pool.table_columns(self._table)
        sets, params = [], []
        for k, v in (self._payload or {}).items():
            if k not in cols_t:
                raise APIError({"message": f"Could not find the '{k}' column of '{self._table}'",
                                "code": "PGRST204", "details": None, "hint": None})
            ph, val = _param(v, cols_t[k])
            sets.append(f"{_qi(k)} = {ph}")
            params.append(val)
        if not sets:
            return Response([], None)
        where, wparams = self._where()
        sql = f"UPDATE public.{_qi(self._table)} AS t SET {', '.join(sets)}{where}{self._returning_sql()}"
        out = self._pool.run(sql, params + wparams, fetch=self._returning)
        return self._finish(out, len(out) if self._count else None)

    def _exec_delete(self):
        where, params = self._where()
        sql = f"DELETE FROM public.{_qi(self._table)} AS t{where}{self._returning_sql()}"
        out = self._pool.run(sql, params, fetch=self._returning)
        return self._finish(out, len(out) if self._count else None)


def _split_top(s: str) -> List[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


class _Rpc:
    def __init__(self, pool: _Pool, name: str, params: Dict[str, Any]):
        self._pool, self._name, self._params = pool, name, params or {}
        self._single = None

    def single(self):
        self._single = "single"
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    def execute(self):
        retset, kind, argtypes = self._pool.function(self._name)
        args, params = [], []
        for k, v in self._params.items():
            if not _IDENT.match(k):
                raise APIError({"message": f"bad argument {k!r}", "code": "PGRST100", "details": None, "hint": None})
            ph, val = _param(v, argtypes.get(k, "text") if k in argtypes else "text")
            args.append(f"{_qi(k)} => {ph}")
            params.append(val)
        call = f"public.{_qi(self._name)}({', '.join(args)})"
        if retset or kind == "composite":
            rows = self._pool.run(f"SELECT * FROM {call}", params)
            data: Any = [{k: _out(v) for k, v in r.items()} for r in rows]
            if kind == "composite" and not retset:
                data = data[0] if data else None
        else:
            rows = self._pool.run(f"SELECT {call} AS r", params)
            data = _out(rows[0]["r"]) if rows and kind != "void" else None
        return Response(data)


class PgClient:
    """What database.get_*_client() hands out when DB_BACKEND=postgres."""

    def __init__(self, dsn: str, *, maxconn: int = 8, supabase_factory=None):
        self._pool = _Pool(dsn, maxconn)
        self._supabase_factory = supabase_factory
        self._supabase = None

    def table(self, name: str) -> Query:
        if not _IDENT.match(name):
            raise APIError({"message": f"bad table name {name!r}", "code": "PGRST100", "details": None, "hint": None})
        return Query(self._pool, name)

    from_ = table

    def rpc(self, fn: str, params: Optional[Dict[str, Any]] = None) -> _Rpc:
        if not _IDENT.match(fn):
            raise APIError({"message": f"bad function name {fn!r}", "code": "PGRST100", "details": None, "hint": None})
        return _Rpc(self._pool, fn, params or {})

    def _sb(self):
        if self._supabase is None:
            if not self._supabase_factory:
                raise RuntimeError("no Supabase client configured for auth/storage")
            self._supabase = self._supabase_factory()
        return self._supabase

    @property
    def auth(self):
        return self._sb().auth

    @property
    def storage(self):
        return self._sb().storage

    def run_sql(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        """Raw SQL for migration/maintenance scripts (not for request code)."""
        return [{k: _out(v) for k, v in r.items()} for r in self._pool.run(sql, params)]
