import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv
from fastmcp import FastMCP

# Load .env from the same folder as main.py
BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
SCHEMA_PATH = BASE_DIR / "schema2.sql"
load_dotenv(ENV_PATH, override=True)

mcp = FastMCP("CompanyKnowledgeBaseServer")


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


async def get_connection() -> asyncpg.Connection:
    return await asyncpg.connect(get_database_url(), ssl="require")


def serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    return value


def serialize_row(row: asyncpg.Record) -> dict[str, Any]:
    return {key: serialize_value(value) for key, value in dict(row).items()}


def ok(**payload: Any) -> dict[str, Any]:
    return {"status": "success", **payload}


def error(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}


def rows_payload(name: str, rows: list[asyncpg.Record]) -> dict[str, Any]:
    return ok(count=len(rows), **{name: [serialize_row(row) for row in rows]})


def tags_from_csv(tags_csv: str) -> list[str]:
    return [tag.strip() for tag in tags_csv.split(",") if tag.strip()]


async def execute_status(sql: str, *args: Any) -> str:
    conn = await get_connection()
    try:
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


@mcp.tool()
async def test_connection() -> dict[str, Any]:
    """Test the database connection."""
    conn = await get_connection()
    try:
        version = await conn.fetchval("SELECT version();")
        return ok(message="Connected successfully", version=version)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def create_company_schema() -> dict[str, Any]:
    """Create all company knowledge base tables, indexes, views, and seed data from schema2.sql."""
    if not SCHEMA_PATH.exists():
        return error(f"Schema file not found: {SCHEMA_PATH}")

    conn = await get_connection()
    try:
        await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        return ok(message="Company knowledge base schema is ready")
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def create_member_table() -> dict[str, Any]:
    """Backward-compatible helper that now creates the full schema."""
    return await create_company_schema()


# =============================================================================
# MEMBER TOOLS
# =============================================================================


@mcp.tool()
async def add_member(name: str, email: str, phone: str = "", role: str = "") -> dict[str, Any]:
    """Add a new member."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.member (name, email, phone, role)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, email, phone, role, created_at;
            """,
            name,
            email,
            phone,
            role,
        )
        return ok(member=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_members() -> dict[str, Any]:
    """List all members."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, email, phone, role, created_at
            FROM public.member
            ORDER BY id;
            """
        )
        return rows_payload("members", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_member_by_email(email: str) -> dict[str, Any]:
    """Get a member by email."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, name, email, phone, role, created_at
            FROM public.member
            WHERE email = $1;
            """,
            email,
        )
        if not row:
            return error("Member not found")
        return ok(member=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_member(email: str, name: str, phone: str = "", role: str = "") -> dict[str, Any]:
    """Update a member by email."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.member
            SET name = $2, phone = $3, role = $4
            WHERE email = $1
            RETURNING id, name, email, phone, role, created_at;
            """,
            email,
            name,
            phone,
            role,
        )
        if not row:
            return error("Member not found")
        return ok(member=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_member(email: str) -> dict[str, Any]:
    """Delete a member by email."""
    try:
        result = await execute_status("DELETE FROM public.member WHERE email = $1;", email)
        if int(result.split()[-1]) == 0:
            return error("Member not found")
        return ok(message=f"Member with email {email} deleted successfully")
    except Exception as exc:
        return error(str(exc))


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
        return ok(department=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_departments() -> dict[str, Any]:
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
        return rows_payload("departments", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_department(department_id: int) -> dict[str, Any]:
    """Get a department by ID."""
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
            return error("Department not found")
        return ok(department=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_department(department_id: int, name: str, description: str = "") -> dict[str, Any]:
    """Update a department."""
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
            return error("Department not found")
        return ok(department=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_department(department_id: int) -> dict[str, Any]:
    """Delete a department by ID."""
    try:
        result = await execute_status("DELETE FROM public.department WHERE id = $1;", department_id)
        if int(result.split()[-1]) == 0:
            return error("Department not found")
        return ok(message=f"Department {department_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


# =============================================================================
# TEAM TOOLS
# =============================================================================


@mcp.tool()
async def add_team(name: str, department_id: int, description: str = "") -> dict[str, Any]:
    """Add a team under a department."""
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
        return ok(team=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_teams(department_id: int = 0) -> dict[str, Any]:
    """List teams. Pass department_id to filter, or 0 for all teams."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT t.id, t.name, t.department_id, d.name AS department_name, t.description, t.created_at
            FROM public.team t
            JOIN public.department d ON d.id = t.department_id
            WHERE ($1::int = 0 OR t.department_id = $1)
            ORDER BY t.id;
            """,
            department_id,
        )
        return rows_payload("teams", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_team(team_id: int) -> dict[str, Any]:
    """Get a team by ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT t.id, t.name, t.department_id, d.name AS department_name, t.description, t.created_at
            FROM public.team t
            JOIN public.department d ON d.id = t.department_id
            WHERE t.id = $1;
            """,
            team_id,
        )
        if not row:
            return error("Team not found")
        return ok(team=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_team(team_id: int, name: str, department_id: int, description: str = "") -> dict[str, Any]:
    """Update a team."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.team
            SET name = $2, department_id = $3, description = $4
            WHERE id = $1
            RETURNING id, name, department_id, description, created_at;
            """,
            team_id,
            name,
            department_id,
            description,
        )
        if not row:
            return error("Team not found")
        return ok(team=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_team(team_id: int) -> dict[str, Any]:
    """Delete a team by ID."""
    try:
        result = await execute_status("DELETE FROM public.team WHERE id = $1;", team_id)
        if int(result.split()[-1]) == 0:
            return error("Team not found")
        return ok(message=f"Team {team_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool()
async def add_member_to_team(team_id: int, member_id: int) -> dict[str, Any]:
    """Add a member to a team."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.team_member (team_id, member_id)
            VALUES ($1, $2)
            ON CONFLICT (team_id, member_id) DO UPDATE SET joined_at = public.team_member.joined_at
            RETURNING team_id, member_id, joined_at;
            """,
            team_id,
            member_id,
        )
        return ok(team_member=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_team_members(team_id: int = 0, member_id: int = 0) -> dict[str, Any]:
    """List team memberships. Pass team_id or member_id to filter, or 0 for all."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT tm.team_id, t.name AS team_name, tm.member_id, m.name AS member_name, m.email, tm.joined_at
            FROM public.team_member tm
            JOIN public.team t ON t.id = tm.team_id
            JOIN public.member m ON m.id = tm.member_id
            WHERE ($1::int = 0 OR tm.team_id = $1)
              AND ($2::int = 0 OR tm.member_id = $2)
            ORDER BY t.name, m.name;
            """,
            team_id,
            member_id,
        )
        return rows_payload("team_members", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def remove_member_from_team(team_id: int, member_id: int) -> dict[str, Any]:
    """Remove a member from a team."""
    try:
        result = await execute_status(
            "DELETE FROM public.team_member WHERE team_id = $1 AND member_id = $2;",
            team_id,
            member_id,
        )
        if int(result.split()[-1]) == 0:
            return error("Team membership not found")
        return ok(message="Team membership deleted successfully")
    except Exception as exc:
        return error(str(exc))


# =============================================================================
# PROJECT TOOLS
# =============================================================================


@mcp.tool()
async def add_project(
    name: str,
    team_id: int,
    description: str = "",
    status: str = "active",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Add a project. Date values should be YYYY-MM-DD or empty."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project (name, team_id, description, status, start_date, end_date)
            VALUES ($1, $2, $3, $4, NULLIF($5, '')::date, NULLIF($6, '')::date)
            RETURNING id, name, team_id, description, status, start_date, end_date, created_at;
            """,
            name,
            team_id,
            description,
            status,
            start_date,
            end_date,
        )
        return ok(project=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_projects(team_id: int = 0, status: str = "") -> dict[str, Any]:
    """List projects. Pass team_id or status to filter, or leave default for all."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.team_id, t.name AS team_name, p.description, p.status,
                   p.start_date, p.end_date, p.created_at
            FROM public.project p
            JOIN public.team t ON t.id = p.team_id
            WHERE ($1::int = 0 OR p.team_id = $1)
              AND ($2::text = '' OR p.status = $2)
            ORDER BY p.id;
            """,
            team_id,
            status,
        )
        return rows_payload("projects", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_project(project_id: int) -> dict[str, Any]:
    """Get a project by ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT p.id, p.name, p.team_id, t.name AS team_name, p.description, p.status,
                   p.start_date, p.end_date, p.created_at
            FROM public.project p
            JOIN public.team t ON t.id = p.team_id
            WHERE p.id = $1;
            """,
            project_id,
        )
        if not row:
            return error("Project not found")
        return ok(project=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_project(
    project_id: int,
    name: str,
    team_id: int,
    description: str = "",
    status: str = "active",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Update a project."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.project
            SET name = $2,
                team_id = $3,
                description = $4,
                status = $5,
                start_date = NULLIF($6, '')::date,
                end_date = NULLIF($7, '')::date
            WHERE id = $1
            RETURNING id, name, team_id, description, status, start_date, end_date, created_at;
            """,
            project_id,
            name,
            team_id,
            description,
            status,
            start_date,
            end_date,
        )
        if not row:
            return error("Project not found")
        return ok(project=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_project(project_id: int) -> dict[str, Any]:
    """Delete a project by ID."""
    try:
        result = await execute_status("DELETE FROM public.project WHERE id = $1;", project_id)
        if int(result.split()[-1]) == 0:
            return error("Project not found")
        return ok(message=f"Project {project_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


# =============================================================================
# TASK TOOLS
# =============================================================================


@mcp.tool()
async def add_task(
    title: str,
    project_id: int,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    due_date: str = "",
) -> dict[str, Any]:
    """Add a task. due_date should be YYYY-MM-DD or empty."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.task (title, project_id, description, status, priority, due_date)
            VALUES ($1, $2, $3, $4, $5, NULLIF($6, '')::date)
            RETURNING id, title, project_id, description, status, priority, due_date, created_at;
            """,
            title,
            project_id,
            description,
            status,
            priority,
            due_date,
        )
        return ok(task=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_tasks(project_id: int = 0, status: str = "", priority: str = "") -> dict[str, Any]:
    """List tasks. Pass project_id, status, or priority to filter."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT tk.id, tk.title, tk.project_id, p.name AS project_name, tk.description,
                   tk.status, tk.priority, tk.due_date, tk.created_at
            FROM public.task tk
            JOIN public.project p ON p.id = tk.project_id
            WHERE ($1::int = 0 OR tk.project_id = $1)
              AND ($2::text = '' OR tk.status = $2)
              AND ($3::text = '' OR tk.priority = $3)
            ORDER BY tk.id;
            """,
            project_id,
            status,
            priority,
        )
        return rows_payload("tasks", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_task(task_id: int) -> dict[str, Any]:
    """Get a task by ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT tk.id, tk.title, tk.project_id, p.name AS project_name, tk.description,
                   tk.status, tk.priority, tk.due_date, tk.created_at
            FROM public.task tk
            JOIN public.project p ON p.id = tk.project_id
            WHERE tk.id = $1;
            """,
            task_id,
        )
        if not row:
            return error("Task not found")
        return ok(task=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_task(
    task_id: int,
    title: str,
    project_id: int,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    due_date: str = "",
) -> dict[str, Any]:
    """Update a task."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.task
            SET title = $2,
                project_id = $3,
                description = $4,
                status = $5,
                priority = $6,
                due_date = NULLIF($7, '')::date
            WHERE id = $1
            RETURNING id, title, project_id, description, status, priority, due_date, created_at;
            """,
            task_id,
            title,
            project_id,
            description,
            status,
            priority,
            due_date,
        )
        if not row:
            return error("Task not found")
        return ok(task=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_task(task_id: int) -> dict[str, Any]:
    """Delete a task by ID."""
    try:
        result = await execute_status("DELETE FROM public.task WHERE id = $1;", task_id)
        if int(result.split()[-1]) == 0:
            return error("Task not found")
        return ok(message=f"Task {task_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool()
async def assign_task(task_id: int, member_id: int) -> dict[str, Any]:
    """Assign a task to a member."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.task_assignee (task_id, member_id)
            VALUES ($1, $2)
            ON CONFLICT (task_id, member_id) DO UPDATE SET assigned_at = public.task_assignee.assigned_at
            RETURNING task_id, member_id, assigned_at;
            """,
            task_id,
            member_id,
        )
        return ok(task_assignee=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_task_assignees(task_id: int = 0, member_id: int = 0) -> dict[str, Any]:
    """List task assignments. Pass task_id or member_id to filter, or 0 for all."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT ta.task_id, tk.title AS task_title, ta.member_id, m.name AS member_name,
                   m.email, ta.assigned_at
            FROM public.task_assignee ta
            JOIN public.task tk ON tk.id = ta.task_id
            JOIN public.member m ON m.id = ta.member_id
            WHERE ($1::int = 0 OR ta.task_id = $1)
              AND ($2::int = 0 OR ta.member_id = $2)
            ORDER BY tk.title, m.name;
            """,
            task_id,
            member_id,
        )
        return rows_payload("task_assignees", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def unassign_task(task_id: int, member_id: int) -> dict[str, Any]:
    """Remove a task assignment."""
    try:
        result = await execute_status(
            "DELETE FROM public.task_assignee WHERE task_id = $1 AND member_id = $2;",
            task_id,
            member_id,
        )
        if int(result.split()[-1]) == 0:
            return error("Task assignment not found")
        return ok(message="Task assignment deleted successfully")
    except Exception as exc:
        return error(str(exc))


# =============================================================================
# BUDGET, EXPENSE, AND KNOWLEDGE TOOLS
# =============================================================================


@mcp.tool()
async def upsert_project_budget(
    project_id: int,
    total_amount: float,
    currency: str = "USD",
    approved_by: str = "",
    approved_at: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Create or update a project's budget. approved_at should be ISO timestamp or empty."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project_budget (project_id, total_amount, currency, approved_by, approved_at, notes)
            VALUES ($1, $2, $3, $4, NULLIF($5, '')::timestamptz, $6)
            ON CONFLICT (project_id) DO UPDATE
            SET total_amount = EXCLUDED.total_amount,
                currency = EXCLUDED.currency,
                approved_by = EXCLUDED.approved_by,
                approved_at = EXCLUDED.approved_at,
                notes = EXCLUDED.notes
            RETURNING id, project_id, total_amount, currency, approved_by, approved_at, notes, created_at;
            """,
            project_id,
            total_amount,
            currency,
            approved_by,
            approved_at,
            notes,
        )
        return ok(project_budget=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_project_budgets(project_id: int = 0) -> dict[str, Any]:
    """List project budgets. Pass project_id to filter, or 0 for all."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pb.id, pb.project_id, p.name AS project_name, pb.total_amount, pb.currency,
                   pb.approved_by, pb.approved_at, pb.notes, pb.created_at
            FROM public.project_budget pb
            JOIN public.project p ON p.id = pb.project_id
            WHERE ($1::int = 0 OR pb.project_id = $1)
            ORDER BY pb.id;
            """,
            project_id,
        )
        return rows_payload("project_budgets", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_project_budget(project_id: int) -> dict[str, Any]:
    """Get one project's budget."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pb.id, pb.project_id, p.name AS project_name, pb.total_amount, pb.currency,
                   pb.approved_by, pb.approved_at, pb.notes, pb.created_at
            FROM public.project_budget pb
            JOIN public.project p ON p.id = pb.project_id
            WHERE pb.project_id = $1;
            """,
            project_id,
        )
        if not row:
            return error("Project budget not found")
        return ok(project_budget=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_project_budget(project_id: int) -> dict[str, Any]:
    """Delete a project's budget."""
    try:
        result = await execute_status("DELETE FROM public.project_budget WHERE project_id = $1;", project_id)
        if int(result.split()[-1]) == 0:
            return error("Project budget not found")
        return ok(message=f"Budget for project {project_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool()
async def add_project_expense(
    project_id: int,
    title: str,
    amount: float,
    category: str = "",
    incurred_at: str = "",
    recorded_by_member_id: int = 0,
    notes: str = "",
) -> dict[str, Any]:
    """Add a project expense. incurred_at should be YYYY-MM-DD or empty for today."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project_expense
                (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
            VALUES ($1, $2, $3, $4, COALESCE(NULLIF($5, '')::date, CURRENT_DATE), NULLIF($6, 0), $7)
            RETURNING id, project_id, title, amount, category, incurred_at, recorded_by_member_id, notes, created_at;
            """,
            project_id,
            title,
            amount,
            category,
            incurred_at,
            recorded_by_member_id,
            notes,
        )
        return ok(project_expense=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_project_expenses(project_id: int = 0, category: str = "") -> dict[str, Any]:
    """List project expenses. Pass project_id or category to filter."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pe.id, pe.project_id, p.name AS project_name, pe.title, pe.amount, pe.category,
                   pe.incurred_at, pe.recorded_by_member_id, m.name AS recorded_by_member_name,
                   pe.notes, pe.created_at
            FROM public.project_expense pe
            JOIN public.project p ON p.id = pe.project_id
            LEFT JOIN public.member m ON m.id = pe.recorded_by_member_id
            WHERE ($1::int = 0 OR pe.project_id = $1)
              AND ($2::text = '' OR pe.category = $2)
            ORDER BY pe.incurred_at DESC, pe.id DESC;
            """,
            project_id,
            category,
        )
        return rows_payload("project_expenses", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_project_expense(expense_id: int) -> dict[str, Any]:
    """Get one project expense by ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pe.id, pe.project_id, p.name AS project_name, pe.title, pe.amount, pe.category,
                   pe.incurred_at, pe.recorded_by_member_id, m.name AS recorded_by_member_name,
                   pe.notes, pe.created_at
            FROM public.project_expense pe
            JOIN public.project p ON p.id = pe.project_id
            LEFT JOIN public.member m ON m.id = pe.recorded_by_member_id
            WHERE pe.id = $1;
            """,
            expense_id,
        )
        if not row:
            return error("Project expense not found")
        return ok(project_expense=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_project_expense(
    expense_id: int,
    project_id: int,
    title: str,
    amount: float,
    category: str = "",
    incurred_at: str = "",
    recorded_by_member_id: int = 0,
    notes: str = "",
) -> dict[str, Any]:
    """Update a project expense. incurred_at should be YYYY-MM-DD or empty for today."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.project_expense
            SET project_id = $2,
                title = $3,
                amount = $4,
                category = $5,
                incurred_at = COALESCE(NULLIF($6, '')::date, CURRENT_DATE),
                recorded_by_member_id = NULLIF($7, 0),
                notes = $8
            WHERE id = $1
            RETURNING id, project_id, title, amount, category, incurred_at,
                      recorded_by_member_id, notes, created_at;
            """,
            expense_id,
            project_id,
            title,
            amount,
            category,
            incurred_at,
            recorded_by_member_id,
            notes,
        )
        if not row:
            return error("Project expense not found")
        return ok(project_expense=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_project_expense(expense_id: int) -> dict[str, Any]:
    """Delete a project expense by ID."""
    try:
        result = await execute_status("DELETE FROM public.project_expense WHERE id = $1;", expense_id)
        if int(result.split()[-1]) == 0:
            return error("Project expense not found")
        return ok(message=f"Project expense {expense_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool()
async def add_project_knowledge(
    project_id: int,
    title: str,
    content: str,
    tags_csv: str = "",
    author_member_id: int = 0,
) -> dict[str, Any]:
    """Add a project knowledge entry. tags_csv should be comma-separated."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.project_knowledge (project_id, title, content, tags, author_member_id)
            VALUES ($1, $2, $3, $4::text[], NULLIF($5, 0))
            RETURNING id, project_id, title, content, tags, author_member_id, created_at, updated_at;
            """,
            project_id,
            title,
            content,
            tags_from_csv(tags_csv),
            author_member_id,
        )
        return ok(project_knowledge=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_project_knowledge(project_id: int = 0, tag: str = "") -> dict[str, Any]:
    """List project knowledge entries. Pass project_id or tag to filter."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT pk.id, pk.project_id, p.name AS project_name, pk.title, pk.content, pk.tags,
                   pk.author_member_id, m.name AS author_member_name, pk.created_at, pk.updated_at
            FROM public.project_knowledge pk
            JOIN public.project p ON p.id = pk.project_id
            LEFT JOIN public.member m ON m.id = pk.author_member_id
            WHERE ($1::int = 0 OR pk.project_id = $1)
              AND ($2::text = '' OR $2 = ANY(pk.tags))
            ORDER BY pk.updated_at DESC, pk.id DESC;
            """,
            project_id,
            tag,
        )
        return rows_payload("project_knowledge", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def get_project_knowledge(knowledge_id: int) -> dict[str, Any]:
    """Get one project knowledge entry by ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pk.id, pk.project_id, p.name AS project_name, pk.title, pk.content, pk.tags,
                   pk.author_member_id, m.name AS author_member_name, pk.created_at, pk.updated_at
            FROM public.project_knowledge pk
            JOIN public.project p ON p.id = pk.project_id
            LEFT JOIN public.member m ON m.id = pk.author_member_id
            WHERE pk.id = $1;
            """,
            knowledge_id,
        )
        if not row:
            return error("Project knowledge entry not found")
        return ok(project_knowledge=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def update_project_knowledge(
    knowledge_id: int,
    title: str,
    content: str,
    tags_csv: str = "",
    author_member_id: int = 0,
) -> dict[str, Any]:
    """Update a project knowledge entry."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            UPDATE public.project_knowledge
            SET title = $2,
                content = $3,
                tags = $4::text[],
                author_member_id = NULLIF($5, 0),
                updated_at = NOW()
            WHERE id = $1
            RETURNING id, project_id, title, content, tags, author_member_id, created_at, updated_at;
            """,
            knowledge_id,
            title,
            content,
            tags_from_csv(tags_csv),
            author_member_id,
        )
        if not row:
            return error("Project knowledge entry not found")
        return ok(project_knowledge=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def delete_project_knowledge(knowledge_id: int) -> dict[str, Any]:
    """Delete a project knowledge entry by ID."""
    try:
        result = await execute_status("DELETE FROM public.project_knowledge WHERE id = $1;", knowledge_id)
        if int(result.split()[-1]) == 0:
            return error("Project knowledge entry not found")
        return ok(message=f"Project knowledge entry {knowledge_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


# =============================================================================
# VIEW TOOLS
# =============================================================================


@mcp.tool()
async def list_project_budget_summary() -> dict[str, Any]:
    """List budget versus actual spend per project."""
    conn = await get_connection()
    try:
        rows = await conn.fetch("SELECT * FROM public.v_project_budget_summary ORDER BY project_id;")
        return rows_payload("project_budget_summary", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_member_teams() -> dict[str, Any]:
    """List all teams that each member belongs to."""
    conn = await get_connection()
    try:
        rows = await conn.fetch("SELECT * FROM public.v_member_teams;")
        return rows_payload("member_teams", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool()
async def list_task_workload() -> dict[str, Any]:
    """List open-task workload per member."""
    conn = await get_connection()
    try:
        rows = await conn.fetch("SELECT * FROM public.v_task_workload;")
        return rows_payload("task_workload", rows)
    except Exception as exc:
        return error(str(exc))
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
