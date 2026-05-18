import os
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv
from fastmcp import FastMCP

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


async def get_connection() -> asyncpg.Connection:
    return await asyncpg.connect(
        get_database_url(),
        ssl="require",
    )


def serialize_member(row: asyncpg.Record) -> dict[str, Any]:
    member = dict(row)

    created_at = member.get("created_at")
    if isinstance(created_at, datetime):
        member["created_at"] = created_at.isoformat()

    return member


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