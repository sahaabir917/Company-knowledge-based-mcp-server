# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Server

```powershell
.\.venv\Scripts\python.exe main.py
```

Server starts at `http://localhost:8080/mcp`. If port 8080 is busy:

```powershell
Get-NetTCPConnection -LocalPort 8080 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## MCP Inspector (dev/testing)

```powershell
$env:DANGEROUSLY_OMIT_AUTH="true"; $env:MCP_PROXY_AUTH_TOKEN="dev-token"
uv run fastmcp dev inspector main.py
```

Inspector UI opens at `http://localhost:6274`. **Important:** In the inspector URL field, enter `http://localhost:8080/mcp` — the browser may have a stale URL from a previous session in localStorage.

## Dependencies

```powershell
uv sync
```

## Architecture

Single-file server (`main.py`) built on **FastMCP 3.x** + **asyncpg**. Every tool opens its own DB connection and closes it in a `finally` block — there is no connection pool.

### Environment / DB connection

`load_dotenv` is called with an **absolute path** (`Path(__file__).parent / ".env"`) and `override=True`. This is intentional: `fastmcp dev inspector` imports `main.py` via `importlib` from an arbitrary CWD, and without the absolute path + override the `.env` values are silently ignored and the server falls back to `localhost:5432`.

`get_database_url()` prefers `DATABASE_URL` (full connection string) over individual `POSTGRES_*` vars. The `get_connection()` helper always passes `ssl="require"` explicitly — do not rely on the connection string's `?sslmode=require` alone, as asyncpg may ignore query-string SSL flags.

### MCP tools exposed

| Tool | Purpose |
|---|---|
| `test_connection` | Smoke-test DB connectivity, returns Postgres version |
| `create_member_table` | `CREATE TABLE IF NOT EXISTS public.member` |
| `add_member` | INSERT, returns the created row |
| `list_members` | SELECT all members ordered by id |
| `get_member_by_email` | SELECT single member |
| `delete_member` | DELETE by email, parses `asyncpg` result string to get count |

### Neon (cloud Postgres)

The production `.env` points to a Neon pooler endpoint. The `DATABASE_URL` must include `?sslmode=require&channel_binding=require`. When connecting, always pass `ssl="require"` to `asyncpg.connect()`.
