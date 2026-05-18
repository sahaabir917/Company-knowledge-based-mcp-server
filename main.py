import os
from datetime import datetime
from typing import Any

import asyncpg
import uvicorn
from fastmcp import FastMCP
from dotenv import load_dotenv
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware


load_dotenv()

mcp = FastMCP("PostgresMemberServer")


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "123456")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "ARIA2")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


async def get_connection() -> asyncpg.Connection:
    return await asyncpg.connect(get_database_url())


def serialize_member(row: asyncpg.Record) -> dict[str, Any]:
    member = dict(row)
    created_at = member.get("created_at")
    if isinstance(created_at, datetime):
        member["created_at"] = created_at.isoformat()
    return member


@mcp.tool()
async def create_member_table() -> dict[str, str]:
    """Create the public.member table in Postgres if it does not exist."""
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
        return {"status": "success", "message": "public.member table is ready"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def add_member(name: str, email: str, phone: str = "") -> dict[str, Any]:
    """Add a member to the public.member table."""
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
        return {"status": "success", "member": serialize_member(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_members() -> list[dict[str, Any]] | dict[str, str]:
    """List all members from the public.member table."""
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
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


if __name__ == "__main__":
    app = mcp.http_app(
        transport="http",
        stateless_http=True,
        middleware=[
            Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
        ],
    )
    uvicorn.run(app, host="0.0.0.0", port=8080)