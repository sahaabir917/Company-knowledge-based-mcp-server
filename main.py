import os
from datetime import datetime
from pathlib import Path
from typing import Any
import asyncpg
from dotenv import load_dotenv
from fastmcp import FastMCP

# LLMs sometimes pass integer IDs as strings ("1" instead of 1).
# Using int | str in signatures makes the JSON schema accept both.
# _Conn transparently coerces any numeric string arg → int before
# every asyncpg call, so no individual tool needs to cast manually.
CoercedInt = int | str

# Load .env from the same folder as main.py
ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH, override=True)

mcp = FastMCP("PostgresMemberServer")


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
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
    """Thin asyncpg wrapper that coerces numeric string args to int."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _cast(args: tuple) -> list:
        out = []
        for a in args:
            if isinstance(a, str) and a.lstrip("-").isdigit():
                out.append(int(a))
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
# TEAM TOOLS
# =============================================================================

@mcp.tool()
async def add_team(name: str, department_id: CoercedInt, description: str = "") -> dict[str, Any]:
    """Add a new team under a department."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.team (name, department_id, description)
            VALUES ($1, $2, $3)
            RETURNING id, name, department_id, description, created_at;
            """,
            name,
            department_id,
            description,
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
async def update_team(team_id: CoercedInt, name: str, description: str = "") -> dict[str, Any]:
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
            team_id,
            name,
            description,
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


# =============================================================================
# TEAM MEMBER TOOLS  (junction: member ↔ team)
# =============================================================================

@mcp.tool()
async def add_team_member(team_id: CoercedInt, member_id: CoercedInt) -> dict[str, Any]:
    """Add a member to a team."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.team_member (team_id, member_id)
            VALUES ($1, $2)
            RETURNING team_id, member_id, joined_at;
            """,
            team_id,
            member_id,
        )
        return {"status": "success", "team_member": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def remove_team_member(team_id: CoercedInt, member_id: CoercedInt) -> dict[str, str]:
    """Remove a member from a team."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.team_member WHERE team_id = $1 AND member_id = $2;",
            team_id,
            member_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Team member not found"}
        return {"status": "success", "message": f"Member {member_id} removed from team {team_id}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_team_members(team_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all members in a team."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT m.id AS member_id, m.name, m.email, m.phone, m.role,
                   tm.team_id, t.name AS team_name, tm.joined_at
            FROM public.team_member tm
            JOIN public.member m ON m.id = tm.member_id
            JOIN public.team   t ON t.id = tm.team_id
            WHERE tm.team_id = $1
            ORDER BY m.name;
            """,
            team_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_member_teams(member_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all teams a member belongs to."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT t.id AS team_id, t.name AS team_name, t.description,
                   d.id AS department_id, d.name AS department_name,
                   tm.joined_at
            FROM public.team_member tm
            JOIN public.team       t ON t.id = tm.team_id
            JOIN public.department d ON d.id = t.department_id
            WHERE tm.member_id = $1
            ORDER BY d.name, t.name;
            """,
            member_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# PROJECT TOOLS
# =============================================================================

@mcp.tool()
async def add_project(
    name: str,
    team_id: CoercedInt,
    description: str = "",
    status: str = "active",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Add a new project to a team. status: active | on_hold | completed | cancelled. Dates as YYYY-MM-DD."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project (name, team_id, description, status, start_date, end_date)
            VALUES ($1, $2, $3, $4, $5::DATE, $6::DATE)
            RETURNING id, name, team_id, description, status, start_date, end_date, created_at;
            """,
            name,
            team_id,
            description,
            status,
            start_date or None,
            end_date or None,
        )
        return {"status": "success", "project": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_projects() -> list[dict[str, Any]] | dict[str, str]:
    """List all projects with their team and department name."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.description, p.status,
                   p.start_date, p.end_date, p.created_at,
                   p.team_id, t.name AS team_name,
                   d.id AS department_id, d.name AS department_name
            FROM public.project p
            JOIN public.team       t ON t.id = p.team_id
            JOIN public.department d ON d.id = t.department_id
            ORDER BY p.id;
            """
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_projects_by_team(team_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all projects belonging to a specific team."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.description, p.status,
                   p.start_date, p.end_date, p.created_at,
                   p.team_id, t.name AS team_name
            FROM public.project p
            JOIN public.team t ON t.id = p.team_id
            WHERE p.team_id = $1
            ORDER BY p.id;
            """,
            team_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_projects_by_status(status: str) -> list[dict[str, Any]] | dict[str, str]:
    """List all projects filtered by status: active | on_hold | completed | cancelled."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.description, p.status,
                   p.start_date, p.end_date, p.created_at,
                   p.team_id, t.name AS team_name,
                   d.name AS department_name
            FROM public.project p
            JOIN public.team       t ON t.id = p.team_id
            JOIN public.department d ON d.id = t.department_id
            WHERE p.status = $1
            ORDER BY p.id;
            """,
            status,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_project(project_id: CoercedInt) -> dict[str, Any]:
    """Get a project by its ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT p.id, p.name, p.description, p.status,
                   p.start_date, p.end_date, p.created_at,
                   p.team_id, t.name AS team_name,
                   d.id AS department_id, d.name AS department_name
            FROM public.project p
            JOIN public.team       t ON t.id = p.team_id
            JOIN public.department d ON d.id = t.department_id
            WHERE p.id = $1;
            """,
            project_id,
        )
        if not row:
            return {"status": "error", "message": "Project not found"}
        return {"status": "success", "project": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def update_project(
    project_id: CoercedInt,
    name: str,
    description: str = "",
    status: str = "active",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Update a project. status: active | on_hold | completed | cancelled. Dates as YYYY-MM-DD."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.project
            SET name = $2, description = $3, status = $4,
                start_date = $5::DATE, end_date = $6::DATE
            WHERE id = $1
            RETURNING id, name, team_id, description, status, start_date, end_date, created_at;
            """,
            project_id,
            name,
            description,
            status,
            start_date or None,
            end_date or None,
        )
        if not row:
            return {"status": "error", "message": "Project not found"}
        return {"status": "success", "project": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_project(project_id: CoercedInt) -> dict[str, str]:
    """Delete a project by its ID."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.project WHERE id = $1;",
            project_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Project not found"}
        return {"status": "success", "message": f"Project {project_id} deleted successfully"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# TASK TOOLS
# =============================================================================

@mcp.tool()
async def add_task(
    title: str,
    project_id: CoercedInt,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    due_date: str = "",
) -> dict[str, Any]:
    """Add a new task to a project. status: todo|in_progress|review|done|cancelled. priority: low|medium|high|critical. due_date: YYYY-MM-DD."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.task (title, project_id, description, status, priority, due_date)
            VALUES ($1, $2, $3, $4, $5, $6::DATE)
            RETURNING id, title, project_id, description, status, priority, due_date, created_at;
            """,
            title, project_id, description, status, priority, due_date or None,
        )
        return {"status": "success", "task": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_tasks() -> list[dict[str, Any]] | dict[str, str]:
    """List all tasks with their project and team name."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT tk.id, tk.title, tk.description, tk.status, tk.priority,
                   tk.due_date, tk.created_at,
                   tk.project_id, p.name AS project_name,
                   t.id AS team_id, t.name AS team_name
            FROM public.task tk
            JOIN public.project p ON p.id = tk.project_id
            JOIN public.team    t ON t.id = p.team_id
            ORDER BY tk.id;
            """
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_tasks_by_project(project_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all tasks in a specific project."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT id, title, description, status, priority, due_date, created_at, project_id
            FROM public.task
            WHERE project_id = $1
            ORDER BY priority DESC, due_date ASC NULLS LAST;
            """,
            project_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_tasks_by_status(status: str) -> list[dict[str, Any]] | dict[str, str]:
    """List all tasks by status: todo | in_progress | review | done | cancelled."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT tk.id, tk.title, tk.description, tk.status, tk.priority,
                   tk.due_date, tk.created_at,
                   tk.project_id, p.name AS project_name
            FROM public.task tk
            JOIN public.project p ON p.id = tk.project_id
            WHERE tk.status = $1
            ORDER BY tk.priority DESC, tk.due_date ASC NULLS LAST;
            """,
            status,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_tasks_by_priority(priority: str) -> list[dict[str, Any]] | dict[str, str]:
    """List all tasks by priority: low | medium | high | critical."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT tk.id, tk.title, tk.description, tk.status, tk.priority,
                   tk.due_date, tk.created_at,
                   tk.project_id, p.name AS project_name
            FROM public.task tk
            JOIN public.project p ON p.id = tk.project_id
            WHERE tk.priority = $1
            ORDER BY tk.due_date ASC NULLS LAST;
            """,
            priority,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_task(task_id: CoercedInt) -> dict[str, Any]:
    """Get a task by its ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT tk.id, tk.title, tk.description, tk.status, tk.priority,
                   tk.due_date, tk.created_at,
                   tk.project_id, p.name AS project_name,
                   t.id AS team_id, t.name AS team_name
            FROM public.task tk
            JOIN public.project p ON p.id = tk.project_id
            JOIN public.team    t ON t.id = p.team_id
            WHERE tk.id = $1;
            """,
            task_id,
        )
        if not row:
            return {"status": "error", "message": "Task not found"}
        return {"status": "success", "task": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def update_task(
    task_id: CoercedInt,
    title: str,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    due_date: str = "",
) -> dict[str, Any]:
    """Update a task. status: todo|in_progress|review|done|cancelled. priority: low|medium|high|critical. due_date: YYYY-MM-DD."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.task
            SET title = $2, description = $3, status = $4, priority = $5, due_date = $6::DATE
            WHERE id = $1
            RETURNING id, title, project_id, description, status, priority, due_date, created_at;
            """,
            task_id, title, description, status, priority, due_date or None,
        )
        if not row:
            return {"status": "error", "message": "Task not found"}
        return {"status": "success", "task": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_task(task_id: CoercedInt) -> dict[str, str]:
    """Delete a task by its ID."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.task WHERE id = $1;",
            task_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Task not found"}
        return {"status": "success", "message": f"Task {task_id} deleted successfully"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# TASK ASSIGNEE TOOLS  (junction: task ↔ member)
# =============================================================================

@mcp.tool()
async def assign_task(task_id: CoercedInt, member_id: CoercedInt) -> dict[str, Any]:
    """Assign a member to a task."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.task_assignee (task_id, member_id)
            VALUES ($1, $2)
            RETURNING task_id, member_id, assigned_at;
            """,
            task_id,
            member_id,
        )
        return {"status": "success", "assignment": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def unassign_task(task_id: CoercedInt, member_id: CoercedInt) -> dict[str, str]:
    """Remove a member's assignment from a task."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.task_assignee WHERE task_id = $1 AND member_id = $2;",
            task_id,
            member_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Assignment not found"}
        return {"status": "success", "message": f"Member {member_id} unassigned from task {task_id}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_task_assignees(task_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all members assigned to a task."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT m.id AS member_id, m.name, m.email, m.role,
                   ta.task_id, tk.title AS task_title, ta.assigned_at
            FROM public.task_assignee ta
            JOIN public.member m  ON m.id  = ta.member_id
            JOIN public.task   tk ON tk.id = ta.task_id
            WHERE ta.task_id = $1
            ORDER BY m.name;
            """,
            task_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_member_tasks(member_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all tasks assigned to a member."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT tk.id AS task_id, tk.title, tk.status, tk.priority, tk.due_date,
                   p.id AS project_id, p.name AS project_name,
                   t.name AS team_name,
                   ta.assigned_at
            FROM public.task_assignee ta
            JOIN public.task    tk ON tk.id = ta.task_id
            JOIN public.project p  ON p.id  = tk.project_id
            JOIN public.team    t  ON t.id  = p.team_id
            WHERE ta.member_id = $1
            ORDER BY tk.priority DESC, tk.due_date ASC NULLS LAST;
            """,
            member_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# PROJECT BUDGET TOOLS  (1 budget per project)
# =============================================================================

@mcp.tool()
async def set_project_budget(
    project_id: CoercedInt,
    total_amount: float,
    currency: str = "USD",
    approved_by: str = "",
    approved_at: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Create or replace the budget for a project. approved_at as YYYY-MM-DD or datetime string."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project_budget
                (project_id, total_amount, currency, approved_by, approved_at, notes)
            VALUES ($1, $2, $3, $4, $5::TIMESTAMPTZ, $6)
            ON CONFLICT (project_id) DO UPDATE
                SET total_amount = EXCLUDED.total_amount,
                    currency     = EXCLUDED.currency,
                    approved_by  = EXCLUDED.approved_by,
                    approved_at  = EXCLUDED.approved_at,
                    notes        = EXCLUDED.notes
            RETURNING id, project_id, total_amount, currency,
                      approved_by, approved_at, notes, created_at;
            """,
            project_id,
            total_amount,
            currency,
            approved_by,
            approved_at or None,
            notes,
        )
        return {"status": "success", "budget": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_project_budget(project_id: CoercedInt) -> dict[str, Any]:
    """Get the budget for a project, including total spent and remaining amount."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pb.id, pb.project_id, p.name AS project_name,
                   pb.total_amount, pb.currency,
                   pb.approved_by, pb.approved_at, pb.notes, pb.created_at,
                   COALESCE(SUM(pe.amount), 0)              AS total_spent,
                   pb.total_amount - COALESCE(SUM(pe.amount), 0) AS remaining
            FROM public.project_budget pb
            JOIN public.project        p  ON p.id  = pb.project_id
            LEFT JOIN public.project_expense pe ON pe.project_id = pb.project_id
            WHERE pb.project_id = $1
            GROUP BY pb.id, pb.project_id, p.name;
            """,
            project_id,
        )
        if not row:
            return {"status": "error", "message": "Budget not found for this project"}
        return {"status": "success", "budget": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_project_budgets() -> list[dict[str, Any]] | dict[str, str]:
    """List budgets for all projects with total spent and remaining."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pb.id, pb.project_id, p.name AS project_name,
                   t.name AS team_name, d.name AS department_name,
                   p.status AS project_status,
                   pb.total_amount, pb.currency,
                   pb.approved_by, pb.approved_at, pb.notes, pb.created_at,
                   COALESCE(SUM(pe.amount), 0)                   AS total_spent,
                   pb.total_amount - COALESCE(SUM(pe.amount), 0) AS remaining
            FROM public.project_budget  pb
            JOIN public.project         p  ON p.id  = pb.project_id
            JOIN public.team            t  ON t.id  = p.team_id
            JOIN public.department      d  ON d.id  = t.department_id
            LEFT JOIN public.project_expense pe ON pe.project_id = pb.project_id
            GROUP BY pb.id, pb.project_id, p.name, t.name, d.name, p.status
            ORDER BY pb.project_id;
            """
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_project_budget(project_id: CoercedInt) -> dict[str, str]:
    """Delete the budget record for a project."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.project_budget WHERE project_id = $1;",
            project_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Budget not found for this project"}
        return {"status": "success", "message": f"Budget for project {project_id} deleted"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# PROJECT EXPENSE TOOLS
# =============================================================================

@mcp.tool()
async def add_expense(
    project_id: CoercedInt,
    title: str,
    amount: float,
    category: str = "",
    incurred_at: str = "",
    recorded_by_member_id: CoercedInt = 0,
    notes: str = "",
) -> dict[str, Any]:
    """Add an expense to a project. category: software|hardware|travel|personnel|other. incurred_at: YYYY-MM-DD."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project_expense
                (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
            VALUES ($1, $2, $3, $4, $5::DATE, $6, $7)
            RETURNING id, project_id, title, amount, category,
                      incurred_at, recorded_by_member_id, notes, created_at;
            """,
            project_id,
            title,
            amount,
            category,
            incurred_at or None,
            recorded_by_member_id or None,
            notes,
        )
        return {"status": "success", "expense": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_expenses_by_project(project_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all expenses for a project."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pe.id, pe.title, pe.amount, pe.category,
                   pe.incurred_at, pe.notes, pe.created_at,
                   pe.project_id, p.name AS project_name,
                   pe.recorded_by_member_id, m.name AS recorded_by
            FROM public.project_expense pe
            JOIN public.project p ON p.id = pe.project_id
            LEFT JOIN public.member m ON m.id = pe.recorded_by_member_id
            WHERE pe.project_id = $1
            ORDER BY pe.incurred_at DESC;
            """,
            project_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_expenses_by_category(category: str) -> list[dict[str, Any]] | dict[str, str]:
    """List all expenses filtered by category: software|hardware|travel|personnel|other."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pe.id, pe.title, pe.amount, pe.category,
                   pe.incurred_at, pe.notes, pe.created_at,
                   pe.project_id, p.name AS project_name,
                   m.name AS recorded_by
            FROM public.project_expense pe
            JOIN public.project p ON p.id = pe.project_id
            LEFT JOIN public.member m ON m.id = pe.recorded_by_member_id
            WHERE pe.category = $1
            ORDER BY pe.incurred_at DESC;
            """,
            category,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_expense(expense_id: CoercedInt) -> dict[str, Any]:
    """Get a single expense by its ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pe.id, pe.title, pe.amount, pe.category,
                   pe.incurred_at, pe.notes, pe.created_at,
                   pe.project_id, p.name AS project_name,
                   pe.recorded_by_member_id, m.name AS recorded_by
            FROM public.project_expense pe
            JOIN public.project p ON p.id = pe.project_id
            LEFT JOIN public.member m ON m.id = pe.recorded_by_member_id
            WHERE pe.id = $1;
            """,
            expense_id,
        )
        if not row:
            return {"status": "error", "message": "Expense not found"}
        return {"status": "success", "expense": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def update_expense(
    expense_id: CoercedInt,
    title: str,
    amount: float,
    category: str = "",
    incurred_at: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Update an expense record. incurred_at: YYYY-MM-DD."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.project_expense
            SET title = $2, amount = $3, category = $4,
                incurred_at = $5::DATE, notes = $6
            WHERE id = $1
            RETURNING id, project_id, title, amount, category,
                      incurred_at, recorded_by_member_id, notes, created_at;
            """,
            expense_id, title, amount, category, incurred_at or None, notes,
        )
        if not row:
            return {"status": "error", "message": "Expense not found"}
        return {"status": "success", "expense": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_expense(expense_id: CoercedInt) -> dict[str, str]:
    """Delete an expense by its ID."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.project_expense WHERE id = $1;",
            expense_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Expense not found"}
        return {"status": "success", "message": f"Expense {expense_id} deleted successfully"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


# =============================================================================
# PROJECT KNOWLEDGE TOOLS
# =============================================================================

@mcp.tool()
async def add_knowledge(
    project_id: CoercedInt,
    title: str,
    content: str,
    tags: list[str] | None = None,
    author_member_id: CoercedInt = 0,
) -> dict[str, Any]:
    """Add a knowledge entry to a project. tags is a list of strings."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project_knowledge
                (project_id, title, content, tags, author_member_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, project_id, title, content, tags,
                      author_member_id, created_at, updated_at;
            """,
            project_id,
            title,
            content,
            tags or [],
            author_member_id or None,
        )
        return {"status": "success", "knowledge": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def list_knowledge_by_project(project_id: CoercedInt) -> list[dict[str, Any]] | dict[str, str]:
    """List all knowledge entries for a project."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pk.id, pk.title, pk.content, pk.tags,
                   pk.created_at, pk.updated_at,
                   pk.project_id, p.name AS project_name,
                   pk.author_member_id, m.name AS author_name
            FROM public.project_knowledge pk
            JOIN public.project p ON p.id = pk.project_id
            LEFT JOIN public.member m ON m.id = pk.author_member_id
            WHERE pk.project_id = $1
            ORDER BY pk.updated_at DESC;
            """,
            project_id,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def search_knowledge_by_tag(tag: str) -> list[dict[str, Any]] | dict[str, str]:
    """Search knowledge entries that contain a specific tag."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pk.id, pk.title, pk.content, pk.tags,
                   pk.created_at, pk.updated_at,
                   pk.project_id, p.name AS project_name,
                   pk.author_member_id, m.name AS author_name
            FROM public.project_knowledge pk
            JOIN public.project p ON p.id = pk.project_id
            LEFT JOIN public.member m ON m.id = pk.author_member_id
            WHERE $1 = ANY(pk.tags)
            ORDER BY pk.updated_at DESC;
            """,
            tag,
        )
        return [serialize_row(row) for row in rows]
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def get_knowledge(knowledge_id: CoercedInt) -> dict[str, Any]:
    """Get a knowledge entry by its ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pk.id, pk.title, pk.content, pk.tags,
                   pk.created_at, pk.updated_at,
                   pk.project_id, p.name AS project_name,
                   pk.author_member_id, m.name AS author_name
            FROM public.project_knowledge pk
            JOIN public.project p ON p.id = pk.project_id
            LEFT JOIN public.member m ON m.id = pk.author_member_id
            WHERE pk.id = $1;
            """,
            knowledge_id,
        )
        if not row:
            return {"status": "error", "message": "Knowledge entry not found"}
        return {"status": "success", "knowledge": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def update_knowledge(
    knowledge_id: CoercedInt,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update a knowledge entry's title, content, and tags. updated_at is set automatically."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.project_knowledge
            SET title = $2, content = $3, tags = $4, updated_at = NOW()
            WHERE id = $1
            RETURNING id, project_id, title, content, tags,
                      author_member_id, created_at, updated_at;
            """,
            knowledge_id,
            title,
            content,
            tags or [],
        )
        if not row:
            return {"status": "error", "message": "Knowledge entry not found"}
        return {"status": "success", "knowledge": serialize_row(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        await conn.close()


@mcp.tool()
async def delete_knowledge(knowledge_id: CoercedInt) -> dict[str, str]:
    """Delete a knowledge entry by its ID."""
    conn = await get_connection()
    try:
        result = await conn.execute(
            "DELETE FROM public.project_knowledge WHERE id = $1;",
            knowledge_id,
        )
        if int(result.split()[-1]) == 0:
            return {"status": "error", "message": "Knowledge entry not found"}
        return {"status": "success", "message": f"Knowledge entry {knowledge_id} deleted successfully"}
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