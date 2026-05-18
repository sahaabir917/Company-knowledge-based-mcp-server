import os
from datetime import datetime
from pathlib import Path
from typing import Any
import asyncpg
from dotenv import load_dotenv
from fastmcp import FastMCP

# LLMs often pass numbers as strings ("1" instead of 1).
# int | str / float | str makes the JSON schema accept both.
# _Conn casts numeric strings to int/float before every asyncpg call.
CoercedInt   = int   | str
CoercedFloat = float | str

# Load .env from the same folder as main.py
ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH, override=True)

mcp = FastMCP("PostgresMemberServer")


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")

    print("ENV PATH:", ENV_PATH)
    print("DEBUG DATABASE_URL:", database_url)
    print("DEBUG POSTGRES_HOST:", os.getenv("POSTGRES_HOST"))

    if database_url:
        return database_url.strip()

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("POSTGRES_HOST")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB")

    if not all([user, password, host, database]):
        raise ValueError("Missing database environment variables.")

    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


class _Conn:
    """Wraps asyncpg.Connection — coerces numeric string args to int/float."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _cast(args: tuple) -> list:
        out = []
        for a in args:
            if isinstance(a, str):
                s = a.lstrip("-")
                if s.isdigit():
                    out.append(int(a))
                else:
                    try:
                        out.append(float(a))
                    except ValueError:
                        out.append(a)
            else:
                out.append(a)
        return out

    async def execute(self, q: str, *args):
        return await self._conn.execute(q, *self._cast(args))

    async def fetch(self, q: str, *args):
        return await self._conn.fetch(q, *self._cast(args))

    async def fetchrow(self, q: str, *args):
        return await self._conn.fetchrow(q, *self._cast(args))

    async def fetchval(self, q: str, *args):
        return await self._conn.fetchval(q, *self._cast(args))

    async def close(self):
        await self._conn.close()


async def get_connection() -> _Conn:
    conn = await asyncpg.connect(get_database_url(), ssl="require")
    return _Conn(conn)


def serialize_row(row: asyncpg.Record) -> dict[str, Any]:
    data = dict(row)
    created_at = data.get("created_at")
    if isinstance(created_at, datetime):
        data["created_at"] = created_at.isoformat()
    updated_at = data.get("updated_at")
    if isinstance(updated_at, datetime):
        data["updated_at"] = updated_at.isoformat()
    return data


# backward-compat alias
serialize_member = serialize_row


@mcp.tool()
async def test_connection() -> dict[str, Any]:
    conn = await get_connection()
    try:
        version = await conn.fetchval("SELECT version();")
        return {
            "status": "success",
            "message": "Connected successfully",
            "version": version,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    finally:
        await conn.close()


@mcp.tool()
async def create_member_table() -> dict[str, str]:
    sql = """
        CREATE TABLE IF NOT EXISTS public.member (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """

    conn = await get_connection()
    try:
        await conn.execute(sql)
        return {
            "status": "success",
            "message": "public.member table is ready",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    finally:
        await conn.close()


@mcp.tool()
async def add_member(name: str, email: str, phone: str = "") -> dict[str, Any]:
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.member (name, email, phone)
            VALUES ($1, $2, $3)
            RETURNING id, name, email, phone, created_at;
            """,
            name,
            email,
            phone,
        )

        return {
            "status": "success",
            "member": serialize_member(row),
        }

    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    finally:
        await conn.close()


@mcp.tool()
async def list_members() -> list[dict[str, Any]] | dict[str, str]:
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, email, phone, created_at
            FROM public.member
            ORDER BY id;
            """
        )

        return [serialize_member(row) for row in rows]

    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    finally:
        await conn.close()


@mcp.tool()
async def get_member_by_email(email: str) -> dict[str, Any]:
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, name, email, phone, created_at
            FROM public.member
            WHERE email = $1;
            """,
            email,
        )

        if not row:
            return {
                "status": "error",
                "message": "Member not found",
            }

        return {
            "status": "success",
            "member": serialize_member(row),
        }

    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    finally:
        await conn.close()


@mcp.tool()
async def delete_member(email: str) -> dict[str, str]:
    conn = await get_connection()
    try:
        result = await conn.execute(
            """
            DELETE FROM public.member
            WHERE email = $1;
            """,
            email,
        )

        deleted_count = int(result.split()[-1])

        if deleted_count == 0:
            return {
                "status": "error",
                "message": "Member not found",
            }

        return {
            "status": "success",
            "message": f"Member with email {email} deleted successfully",
        }

    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    finally:
        await conn.close()


# =============================================================================
# DEPARTMENT TOOLS
# =============================================================================

@mcp.tool()
async def add_department(name: str, description: str = "") -> dict[str, Any]:
    """Add a new department."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.department (name, description)
            VALUES ($1, $2)
            RETURNING id, name, description, created_at;
            """,
            name,
            description,
        )
        return {"status": "success", "department": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_departments() -> list[dict[str, Any]] | dict[str, str]:
    """List all departments."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, description, created_at
            FROM public.department
            ORDER BY id;
            """
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_department(department_id: CoercedInt) -> dict[str, Any]:
    """Get a department by its ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, name, description, created_at
            FROM public.department
            WHERE id = $1;
            """,
            department_id,
        )
        if not row:
            return {"status": "error", "message": "Department not found"}
        return {"status": "success", "department": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def update_department(department_id: CoercedInt, name: str, description: str = "") -> dict[str, Any]:
    """Update a department's name and description."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.department
            SET name = $2, description = $3
            WHERE id = $1
            RETURNING id, name, description, created_at;
            """,
            department_id,
            name,
            description,
        )
        if not row:
            return {"status": "error", "message": "Department not found"}
        return {"status": "success", "department": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_department(department_id: CoercedInt) -> dict[str, str]:
    """Delete a department by its ID."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.department WHERE id = $1;",
            department_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Department not found"}
        return {"status": "success", "message": f"Department {department_id} deleted successfully"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# TEAM TOOLS  (id is INT — caller must supply the ID)
# =============================================================================

@mcp.tool()
async def add_team(
    id: CoercedInt,
    name: str,
    department_id: CoercedInt,
    description: str = "",
) -> dict[str, Any]:
    """Add a new team. id and department_id must be integers (department_id is a FK to department)."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.team (id, name, department_id, description)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, department_id, description, created_at;
            """,
            id, name, department_id, description,
        )
        return {"status": "success", "team": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_teams() -> list[dict[str, Any]] | dict[str, str]:
    """List all teams with their department name."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT t.id, t.name, t.description, t.created_at,
                   t.department_id, d.name AS department_name
            FROM public.team t
            JOIN public.department d ON d.id = t.department_id
            ORDER BY t.id;
            """
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_teams_by_department(department_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all teams in a specific department."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT t.id, t.name, t.description, t.created_at,
                   t.department_id, d.name AS department_name
            FROM public.team t
            JOIN public.department d ON d.id = t.department_id
            WHERE t.department_id = $1
            ORDER BY t.id;
            """,
            department_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_team(team_id: CoercedInt) -> dict[str, Any]:
    """Get a team by its ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT t.id, t.name, t.description, t.created_at,
                   t.department_id, d.name AS department_name
            FROM public.team t
            JOIN public.department d ON d.id = t.department_id
            WHERE t.id = $1;
            """,
            team_id,
        )
        if not row:
            return {"status": "error", "message": "Team not found"}
        return {"status": "success", "team": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def update_team(
    team_id: CoercedInt,
    name: str,
    description: str = "",
) -> dict[str, Any]:
    """Update a team's name and description."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.team
            SET name = $2, description = $3
            WHERE id = $1
            RETURNING id, name, department_id, description, created_at;
            """,
            team_id, name, description,
        )
        if not row:
            return {"status": "error", "message": "Team not found"}
        return {"status": "success", "team": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_team(team_id: CoercedInt) -> dict[str, str]:
    """Delete a team by its ID."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.team WHERE id = $1;",
            team_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Team not found"}
        return {"status": "success", "message": f"Team {team_id} deleted successfully"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


if __name__ == "__main__":
    print("Starting MCP server...")
    print("Loaded .env from:", ENV_PATH)
    print("Loaded DATABASE_URL:", os.getenv("DATABASE_URL"))
    print("Loaded POSTGRES_HOST:", os.getenv("POSTGRES_HOST"))

    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8080,
    )