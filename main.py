import json
import os
import re
from datetime import date, datetime, timedelta
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


class ConnectionErrorProxy:
    """Raises a captured connection error from normal query methods."""

    def __init__(self, exc: Exception):
        self.exc = exc

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        raise self.exc

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        raise self.exc

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        raise self.exc

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise self.exc

    def transaction(self) -> "ConnectionErrorProxy":
        return self

    async def __aenter__(self) -> "ConnectionErrorProxy":
        raise self.exc

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def close(self) -> None:
        return None


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


async def get_connection() -> asyncpg.Connection | ConnectionErrorProxy:
    try:
        return await asyncpg.connect(get_database_url(), ssl="require")
    except Exception as exc:
        return ConnectionErrorProxy(exc)


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


def tool_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str)


def ok(**payload: Any) -> str:
    return tool_json({"status": "success", **payload})


def error(message: str) -> str:
    return tool_json({"status": "error", "message": message})


def rows_payload(name: str, rows: list[asyncpg.Record]) -> str:
    return ok(count=len(rows), **{name: [serialize_row(row) for row in rows]})


def tags_from_csv(tags_csv: str) -> list[str]:
    values = re.split(r"\s*(?:,|;|/|&|\band\b|\bthen\b|->)\s*", tags_csv, flags=re.IGNORECASE)
    return [tag.strip() for tag in values if tag.strip()]


def ints_from_csv(values_csv: str) -> list[int]:
    return [int(value.strip()) for value in values_csv.split(",") if value.strip()]


def int_filter(value: int | str | None) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def required_int(value: int | str) -> int:
    return int(value)


def number_value(value: float | int | str | None, default: float = 0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if not match:
            return default
        return float(match.group(0))
    return float(value)


def required_float(value: float | int | str) -> float:
    return number_value(value)


def optional_float(value: float | int | str | None) -> float | None:
    if value in (None, ""):
        return None
    return number_value(value)


def normalize_date_string(value: str) -> str:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value


def end_date_from_start(start_date: str, days_requested: float) -> str:
    whole_days = max(int(days_requested), 1)
    return (datetime.strptime(start_date, "%Y-%m-%d").date() + timedelta(days=whole_days - 1)).isoformat()


async def execute_status(sql: str, *args: Any) -> str:
    conn = await get_connection()
    try:
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


async def refresh_leave_request_status(conn: asyncpg.Connection, leave_request_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        WITH approval_summary AS (
            SELECT
                COUNT(*) AS total_steps,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending_steps,
                COUNT(*) FILTER (WHERE status = 'rejected') AS rejected_steps
            FROM public.leave_approval
            WHERE leave_request_id = $1
        )
        UPDATE public.leave_request lr
        SET status = CASE
                WHEN approval_summary.total_steps = 0 THEN 'pending'
                WHEN approval_summary.rejected_steps > 0 THEN 'rejected'
                WHEN approval_summary.pending_steps > 0 THEN 'pending'
                ELSE 'approved'
            END,
            final_decision_at = CASE
                WHEN approval_summary.total_steps > 0
                     AND (approval_summary.rejected_steps > 0 OR approval_summary.pending_steps = 0) THEN NOW()
                ELSE NULL
            END
        FROM approval_summary
        WHERE lr.id = $1
        RETURNING lr.id, lr.member_id, lr.project_id, lr.leave_type, lr.start_date, lr.end_date,
                  lr.days_requested, lr.reason, lr.status, lr.requested_at, lr.final_decision_at;
        """,
        leave_request_id,
    )


def extract_leave_day_limit(rule_text: str) -> float | None:
    text = rule_text.lower()
    if "leave" not in text or "day" not in text:
        return None
    patterns = [
        r"(?:more than|over|exceed|exceeds|maximum|max|up to|at most|cannot take more than)\s+(\d+(?:\.\d+)?)\s+days?",
        r"(\d+(?:\.\d+)?)\s+days?\s+(?:at a time|per request|maximum|max)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


async def get_leave_day_policy_limit(
    conn: asyncpg.Connection,
    leave_type: str,
    project_id: int,
    start_date: str,
) -> tuple[float | None, dict[str, Any] | None]:
    rows = await conn.fetch(
        """
        SELECT id, name, category, rule_text, max_leave_days_per_request
        FROM public.policy_rule
        WHERE status = 'active'
          AND category = 'leave'
          AND ($1::int = 0 OR applies_to_project_id IS NULL OR applies_to_project_id = $1)
          AND (effective_from IS NULL OR effective_from <= $2::text::date)
          AND (effective_to IS NULL OR effective_to >= $2::text::date)
        ORDER BY applies_to_project_id NULLS LAST, updated_at DESC, id DESC;
        """,
        project_id,
        start_date,
    )
    for row in rows:
        limit = serialize_value(row["max_leave_days_per_request"])
        if limit is None:
            limit = extract_leave_day_limit(row["rule_text"])
        if limit is not None:
            policy = serialize_row(row)
            policy["matched_leave_type"] = leave_type
            return float(limit), policy
    return None, None


def normalize_approver_role(role: str) -> str:
    normalized = role.strip()
    key = normalized.lower().replace("_", " ").replace("-", " ")
    role_aliases = {
        "hr": "HR Manager",
        "human resources": "HR Manager",
        "project lead": "Project Lead",
        "lead": "Project Lead",
        "manager": "Engineering Manager",
        "ceo": "CEO",
        "cto": "CTO",
        "ceo/cto": "CEO/CTO",
        "executive": "CEO/CTO",
    }
    return role_aliases.get(key, normalized)


async def get_default_leave_approval_roles(conn: asyncpg.Connection, project_id: int, start_date: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT rule_text
        FROM public.policy_rule
        WHERE status = 'active'
          AND category = 'leave'
          AND ($1::int = 0 OR applies_to_project_id IS NULL OR applies_to_project_id = $1)
          AND (effective_from IS NULL OR effective_from <= $2::text::date)
          AND (effective_to IS NULL OR effective_to >= $2::text::date)
        ORDER BY applies_to_project_id NULLS LAST, updated_at DESC, id DESC;
        """,
        project_id,
        start_date,
    )
    for row in rows:
        text = row["rule_text"].lower()
        roles: list[str] = []
        if "project lead" in text:
            roles.append("Project Lead")
        elif "manager" in text:
            roles.append("Engineering Manager")
        if "hr" in text:
            roles.append("HR Manager")
        if "ceo" in text and "cto" in text:
            roles.append("CEO/CTO")
        elif "ceo" in text:
            roles.append("CEO")
        elif "cto" in text:
            roles.append("CTO")
        if roles:
            return roles
    return ["HR Manager"]


async def insert_leave_approval_steps(
    conn: asyncpg.Connection,
    leave_request_id: int,
    start_date: str,
    project_id: int,
    approver_roles_csv: str,
    approver_member_ids_csv: str,
) -> list[asyncpg.Record]:
    approval_order = 1
    approval_rows: list[asyncpg.Record] = []
    roles = [normalize_approver_role(role) for role in tags_from_csv(approver_roles_csv)]
    if not roles and not tags_from_csv(approver_member_ids_csv):
        roles = await get_default_leave_approval_roles(conn, project_id, start_date)

    for role in roles:
        row = await conn.fetchrow(
            """
            INSERT INTO public.leave_approval (leave_request_id, approval_order, approver_role)
            VALUES ($1, $2, $3)
            RETURNING id, leave_request_id, approval_order, approver_role, approver_member_id,
                      status, decision_by_member_id, decision_at, comments, created_at;
            """,
            leave_request_id,
            approval_order,
            role,
        )
        approval_rows.append(row)
        approval_order += 1

    for approver_member_id in ints_from_csv(approver_member_ids_csv):
        row = await conn.fetchrow(
            """
            INSERT INTO public.leave_approval (leave_request_id, approval_order, approver_member_id)
            VALUES ($1, $2, $3)
            RETURNING id, leave_request_id, approval_order, approver_role, approver_member_id,
                      status, decision_by_member_id, decision_at, comments, created_at;
            """,
            leave_request_id,
            approval_order,
            approver_member_id,
        )
        approval_rows.append(row)
        approval_order += 1
    return approval_rows


@mcp.tool(output_schema=None)
async def test_connection() -> str:
    """Test the database connection."""
    conn = await get_connection()
    try:
        version = await conn.fetchval("SELECT version();")
        return ok(message="Connected successfully", version=version)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def create_company_schema() -> str:
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


@mcp.tool(output_schema=None)
async def create_member_table() -> str:
    """Backward-compatible helper that now creates the full schema."""
    return await create_company_schema()


# =============================================================================
# MEMBER TOOLS
# =============================================================================


@mcp.tool(output_schema=None)
async def add_member(name: str, email: str, phone: str = "", role: str = "") -> str:
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


@mcp.tool(output_schema=None)
async def list_members() -> str:
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


@mcp.tool(output_schema=None)
async def get_member_by_email(email: str) -> str:
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


@mcp.tool(output_schema=None)
async def update_member(email: str, name: str, phone: str = "", role: str = "") -> str:
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


@mcp.tool(output_schema=None)
async def delete_member(email: str) -> str:
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


@mcp.tool(output_schema=None)
async def add_department(name: str, description: str = "") -> str:
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


@mcp.tool(output_schema=None)
async def list_departments() -> str:
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


@mcp.tool(output_schema=None)
async def get_department(department_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def update_department(department_id: int, name: str, description: str = "") -> str:
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


@mcp.tool(output_schema=None)
async def delete_department(department_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def add_team(name: str, department_id: int, description: str = "") -> str:
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


@mcp.tool(output_schema=None)
async def list_teams(department_id: int | str = 0) -> str:
    """List teams. Pass department_id to filter, or 0 for all teams."""
    conn = await get_connection()
    try:
        department_id = int_filter(department_id)
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


@mcp.tool(output_schema=None)
async def get_team(team_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def update_team(team_id: int, name: str, department_id: int, description: str = "") -> str:
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


@mcp.tool(output_schema=None)
async def delete_team(team_id: int) -> str:
    """Delete a team by ID."""
    try:
        result = await execute_status("DELETE FROM public.team WHERE id = $1;", team_id)
        if int(result.split()[-1]) == 0:
            return error("Team not found")
        return ok(message=f"Team {team_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool(output_schema=None)
async def add_member_to_team(team_id: int, member_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def list_team_members(team_id: int | str = 0, member_id: int | str = 0) -> str:
    """List team memberships. Pass team_id or member_id to filter, or 0 for all."""
    conn = await get_connection()
    try:
        team_id = int_filter(team_id)
        member_id = int_filter(member_id)
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


@mcp.tool(output_schema=None)
async def remove_member_from_team(team_id: int, member_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def add_project(
    name: str,
    team_id: int,
    description: str = "",
    status: str = "active",
    start_date: str = "",
    end_date: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def list_projects(team_id: int | str = 0, status: str = "") -> str:
    """List projects. Pass team_id or status to filter, or leave default for all."""
    conn = await get_connection()
    try:
        team_id = int_filter(team_id)
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


@mcp.tool(output_schema=None)
async def get_project(project_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def update_project(
    project_id: int,
    name: str,
    team_id: int,
    description: str = "",
    status: str = "active",
    start_date: str = "",
    end_date: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def delete_project(project_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def add_task(
    title: str,
    project_id: int,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    due_date: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def list_tasks(project_id: int | str = 0, status: str = "", priority: str = "") -> str:
    """List tasks. Pass project_id, status, or priority to filter."""
    conn = await get_connection()
    try:
        project_id = int_filter(project_id)
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


@mcp.tool(output_schema=None)
async def get_task(task_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def update_task(
    task_id: int,
    title: str,
    project_id: int,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    due_date: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def delete_task(task_id: int) -> str:
    """Delete a task by ID."""
    try:
        result = await execute_status("DELETE FROM public.task WHERE id = $1;", task_id)
        if int(result.split()[-1]) == 0:
            return error("Task not found")
        return ok(message=f"Task {task_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool(output_schema=None)
async def assign_task(task_id: int, member_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def list_task_assignees(task_id: int | str = 0, member_id: int | str = 0) -> str:
    """List task assignments. Pass task_id or member_id to filter, or 0 for all."""
    conn = await get_connection()
    try:
        task_id = int_filter(task_id)
        member_id = int_filter(member_id)
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


@mcp.tool(output_schema=None)
async def unassign_task(task_id: int, member_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def upsert_project_budget(
    project_id: int,
    total_amount: float,
    currency: str = "USD",
    approved_by: str = "",
    approved_at: str = "",
    notes: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def list_project_budgets(project_id: int | str = 0) -> str:
    """List project budgets. Pass project_id to filter, or 0 for all."""
    conn = await get_connection()
    try:
        project_id = int_filter(project_id)
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


@mcp.tool(output_schema=None)
async def get_project_budget(project_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def delete_project_budget(project_id: int) -> str:
    """Delete a project's budget."""
    try:
        result = await execute_status("DELETE FROM public.project_budget WHERE project_id = $1;", project_id)
        if int(result.split()[-1]) == 0:
            return error("Project budget not found")
        return ok(message=f"Budget for project {project_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool(output_schema=None)
async def add_project_expense(
    project_id: int,
    title: str,
    amount: float,
    category: str = "",
    incurred_at: str = "",
    recorded_by_member_id: int = 0,
    notes: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def list_project_expenses(project_id: int | str = 0, category: str = "") -> str:
    """List project expenses. Pass project_id or category to filter."""
    conn = await get_connection()
    try:
        project_id = int_filter(project_id)
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


@mcp.tool(output_schema=None)
async def get_project_expense(expense_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def update_project_expense(
    expense_id: int,
    project_id: int,
    title: str,
    amount: float,
    category: str = "",
    incurred_at: str = "",
    recorded_by_member_id: int = 0,
    notes: str = "",
) -> str:
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


@mcp.tool(output_schema=None)
async def delete_project_expense(expense_id: int) -> str:
    """Delete a project expense by ID."""
    try:
        result = await execute_status("DELETE FROM public.project_expense WHERE id = $1;", expense_id)
        if int(result.split()[-1]) == 0:
            return error("Project expense not found")
        return ok(message=f"Project expense {expense_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool(output_schema=None)
async def add_project_knowledge(
    project_id: int,
    title: str,
    content: str,
    tags_csv: str = "",
    author_member_id: int = 0,
) -> str:
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


@mcp.tool(output_schema=None)
async def list_project_knowledge(project_id: int | str = 0, tag: str = "") -> str:
    """List project knowledge entries. Pass project_id or tag to filter."""
    conn = await get_connection()
    try:
        project_id = int_filter(project_id)
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


@mcp.tool(output_schema=None)
async def get_project_knowledge(knowledge_id: int) -> str:
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


@mcp.tool(output_schema=None)
async def update_project_knowledge(
    knowledge_id: int,
    title: str,
    content: str,
    tags_csv: str = "",
    author_member_id: int = 0,
) -> str:
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


@mcp.tool(output_schema=None)
async def delete_project_knowledge(knowledge_id: int) -> str:
    """Delete a project knowledge entry by ID."""
    try:
        result = await execute_status("DELETE FROM public.project_knowledge WHERE id = $1;", knowledge_id)
        if int(result.split()[-1]) == 0:
            return error("Project knowledge entry not found")
        return ok(message=f"Project knowledge entry {knowledge_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


# =============================================================================
# POLICY, BENEFIT, AND LEAVE TOOLS
# =============================================================================


@mcp.tool(output_schema=None)
async def add_policy_rule(
    name: str,
    rule_text: str,
    category: str = "",
    description: str = "",
    max_leave_days_per_request: float | int | str = "",
    applies_to_project_id: int = 0,
    effective_from: str = "",
    effective_to: str = "",
    status: str = "active",
    created_by_member_id: int = 0,
) -> str:
    """Create a policy rule such as project revenue, delivery, hiring, or leave."""
    conn = await get_connection()
    try:
        max_leave_days_per_request = optional_float(max_leave_days_per_request)
        row = await conn.fetchrow(
            """
            INSERT INTO public.policy_rule
                (name, category, description, rule_text, max_leave_days_per_request, applies_to_project_id,
                 effective_from, effective_to, status, created_by_member_id)
            VALUES ($1, $2, $3, $4, $5, NULLIF($6, 0), NULLIF($7, '')::date,
                    NULLIF($8, '')::date, $9, NULLIF($10, 0))
            RETURNING id, name, category, description, rule_text, max_leave_days_per_request, applies_to_project_id,
                      effective_from, effective_to, status, created_by_member_id, created_at, updated_at;
            """,
            name,
            category,
            description,
            rule_text,
            max_leave_days_per_request,
            applies_to_project_id,
            effective_from,
            effective_to,
            status,
            created_by_member_id,
        )
        return ok(policy_rule=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def list_policy_rules(category: str = "", status: str = "", project_id: int | str = 0) -> str:
    """List policy rules. Filter by category, status, or project_id."""
    conn = await get_connection()
    try:
        project_id = int_filter(project_id)
        rows = await conn.fetch(
            """
            SELECT pr.id, pr.name, pr.category, pr.description, pr.rule_text,
                   pr.max_leave_days_per_request,
                   pr.applies_to_project_id, p.name AS applies_to_project_name,
                   pr.effective_from, pr.effective_to, pr.status,
                   pr.created_by_member_id, m.name AS created_by_member_name,
                   pr.created_at, pr.updated_at
            FROM public.policy_rule pr
            LEFT JOIN public.project p ON p.id = pr.applies_to_project_id
            LEFT JOIN public.member m ON m.id = pr.created_by_member_id
            WHERE ($1::text = '' OR pr.category = $1)
              AND ($2::text = '' OR pr.status = $2)
              AND ($3::int = 0 OR pr.applies_to_project_id = $3)
            ORDER BY pr.category, pr.name;
            """,
            category,
            status,
            project_id,
        )
        return rows_payload("policy_rules", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def get_policy_rule(policy_rule_id: int) -> str:
    """Get one policy rule by ID."""
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT pr.id, pr.name, pr.category, pr.description, pr.rule_text,
                   pr.max_leave_days_per_request,
                   pr.applies_to_project_id, p.name AS applies_to_project_name,
                   pr.effective_from, pr.effective_to, pr.status,
                   pr.created_by_member_id, m.name AS created_by_member_name,
                   pr.created_at, pr.updated_at
            FROM public.policy_rule pr
            LEFT JOIN public.project p ON p.id = pr.applies_to_project_id
            LEFT JOIN public.member m ON m.id = pr.created_by_member_id
            WHERE pr.id = $1;
            """,
            policy_rule_id,
        )
        if not row:
            return error("Policy rule not found")
        return ok(policy_rule=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def update_policy_rule(
    policy_rule_id: int,
    name: str,
    rule_text: str,
    category: str = "",
    description: str = "",
    max_leave_days_per_request: float | int | str = "",
    applies_to_project_id: int = 0,
    effective_from: str = "",
    effective_to: str = "",
    status: str = "active",
    created_by_member_id: int = 0,
) -> str:
    """Update a policy rule."""
    conn = await get_connection()
    try:
        max_leave_days_per_request = optional_float(max_leave_days_per_request)
        row = await conn.fetchrow(
            """
            UPDATE public.policy_rule
            SET name = $2,
                category = $3,
                description = $4,
                rule_text = $5,
                max_leave_days_per_request = $6,
                applies_to_project_id = NULLIF($7, 0),
                effective_from = NULLIF($8, '')::date,
                effective_to = NULLIF($9, '')::date,
                status = $10,
                created_by_member_id = NULLIF($11, 0),
                updated_at = NOW()
            WHERE id = $1
            RETURNING id, name, category, description, rule_text, max_leave_days_per_request, applies_to_project_id,
                      effective_from, effective_to, status, created_by_member_id, created_at, updated_at;
            """,
            policy_rule_id,
            name,
            category,
            description,
            rule_text,
            max_leave_days_per_request,
            applies_to_project_id,
            effective_from,
            effective_to,
            status,
            created_by_member_id,
        )
        if not row:
            return error("Policy rule not found")
        return ok(policy_rule=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def delete_policy_rule(policy_rule_id: int) -> str:
    """Delete a policy rule by ID."""
    try:
        result = await execute_status("DELETE FROM public.policy_rule WHERE id = $1;", policy_rule_id)
        if int(result.split()[-1]) == 0:
            return error("Policy rule not found")
        return ok(message=f"Policy rule {policy_rule_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool(output_schema=None)
async def add_employee_benefit(
    member_id: int | str,
    benefit_type: str,
    title: str,
    amount: float | int | str = 0,
    currency: str = "USD",
    balance_days: float | int | str = 0,
    effective_from: str = "",
    effective_to: str = "",
    status: str = "active",
    notes: str = "",
) -> str:
    """Add salary, leave balance, or another employee benefit."""
    conn = await get_connection()
    try:
        member_id = required_int(member_id)
        amount = number_value(amount)
        balance_days = number_value(balance_days)
        row = await conn.fetchrow(
            """
            INSERT INTO public.employee_benefit
                (member_id, benefit_type, title, amount, currency, balance_days,
                 effective_from, effective_to, status, notes)
            VALUES ($1, $2, $3, NULLIF($4, 0), $5, NULLIF($6, 0),
                    NULLIF($7, '')::date, NULLIF($8, '')::date, $9, $10)
            RETURNING id, member_id, benefit_type, title, amount, currency, balance_days,
                      effective_from, effective_to, status, notes, created_at, updated_at;
            """,
            member_id,
            benefit_type,
            title,
            amount,
            currency,
            balance_days,
            effective_from,
            effective_to,
            status,
            notes,
        )
        return ok(employee_benefit=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def list_employee_benefits(member_id: int | str = 0, benefit_type: str = "", status: str = "") -> str:
    """List employee benefits. Filter by member_id, benefit_type, or status."""
    conn = await get_connection()
    try:
        member_id = int_filter(member_id)
        rows = await conn.fetch(
            """
            SELECT eb.id, eb.member_id, m.name AS member_name, m.email, m.role,
                   eb.benefit_type, eb.title, eb.amount, eb.currency, eb.balance_days,
                   eb.effective_from, eb.effective_to, eb.status, eb.notes,
                   eb.created_at, eb.updated_at
            FROM public.employee_benefit eb
            JOIN public.member m ON m.id = eb.member_id
            WHERE ($1::int = 0 OR eb.member_id = $1)
              AND ($2::text = '' OR eb.benefit_type = $2)
              AND ($3::text = '' OR eb.status = $3)
            ORDER BY m.name, eb.benefit_type, eb.effective_from DESC NULLS LAST;
            """,
            member_id,
            benefit_type,
            status,
        )
        return rows_payload("employee_benefits", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def update_employee_benefit(
    benefit_id: int | str,
    benefit_type: str,
    title: str,
    amount: float | int | str = 0,
    currency: str = "USD",
    balance_days: float | int | str = 0,
    effective_from: str = "",
    effective_to: str = "",
    status: str = "active",
    notes: str = "",
) -> str:
    """Update an employee benefit."""
    conn = await get_connection()
    try:
        benefit_id = required_int(benefit_id)
        amount = number_value(amount)
        balance_days = number_value(balance_days)
        row = await conn.fetchrow(
            """
            UPDATE public.employee_benefit
            SET benefit_type = $2,
                title = $3,
                amount = NULLIF($4, 0),
                currency = $5,
                balance_days = NULLIF($6, 0),
                effective_from = NULLIF($7, '')::date,
                effective_to = NULLIF($8, '')::date,
                status = $9,
                notes = $10,
                updated_at = NOW()
            WHERE id = $1
            RETURNING id, member_id, benefit_type, title, amount, currency, balance_days,
                      effective_from, effective_to, status, notes, created_at, updated_at;
            """,
            benefit_id,
            benefit_type,
            title,
            amount,
            currency,
            balance_days,
            effective_from,
            effective_to,
            status,
            notes,
        )
        if not row:
            return error("Employee benefit not found")
        return ok(employee_benefit=serialize_row(row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def delete_employee_benefit(benefit_id: int | str) -> str:
    """Delete an employee benefit by ID."""
    try:
        benefit_id = required_int(benefit_id)
        result = await execute_status("DELETE FROM public.employee_benefit WHERE id = $1;", benefit_id)
        if int(result.split()[-1]) == 0:
            return error("Employee benefit not found")
        return ok(message=f"Employee benefit {benefit_id} deleted successfully")
    except Exception as exc:
        return error(str(exc))


@mcp.tool(output_schema=None)
async def create_leave_request(
    member_id: int | str,
    start_date: str,
    days_requested: float | int | str,
    end_date: str = "",
    leave_type: str = "annual",
    reason: str = "",
    project_id: int | str = 0,
    approver_roles_csv: str = "",
    approver_member_ids_csv: str = "",
) -> str:
    """Create a leave request and optional ordered approval chain by role/member IDs."""
    conn = await get_connection()
    try:
        member_id = required_int(member_id)
        days_requested = required_float(days_requested)
        project_id = int_filter(project_id)
        start_date = normalize_date_string(start_date)
        end_date = end_date_from_start(start_date, days_requested) if not end_date else normalize_date_string(end_date)
        async with conn.transaction():
            limit, policy = await get_leave_day_policy_limit(conn, leave_type, project_id, start_date)
            if limit is not None and days_requested > limit:
                return error(
                    "Leave request violates active policy "
                    f"'{policy['name']}': maximum {limit:g} days per request, requested {days_requested:g} days."
                )
            request_row = await conn.fetchrow(
                """
                INSERT INTO public.leave_request
                    (member_id, project_id, leave_type, start_date, end_date, days_requested, reason)
                VALUES ($1, NULLIF($2, 0), $3, $4::text::date, $5::text::date, $6, $7)
                RETURNING id, member_id, project_id, leave_type, start_date, end_date,
                          days_requested, reason, status, requested_at, final_decision_at;
                """,
                member_id,
                project_id,
                leave_type,
                start_date,
                end_date,
                days_requested,
                reason,
            )
            approval_rows = await insert_leave_approval_steps(
                conn,
                request_row["id"],
                start_date,
                project_id,
                approver_roles_csv,
                approver_member_ids_csv,
            )
            request_row = await refresh_leave_request_status(conn, request_row["id"])
        return ok(
            leave_request=serialize_row(request_row),
            approvals=[serialize_row(row) for row in approval_rows],
        )
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def list_leave_requests(member_id: int | str = 0, status: str = "", project_id: int | str = 0) -> str:
    """List leave requests with approval summary."""
    conn = await get_connection()
    try:
        member_id = int_filter(member_id)
        project_id = int_filter(project_id)
        rows = await conn.fetch(
            """
            SELECT *
            FROM public.v_leave_request_status
            WHERE ($1::int = 0 OR member_id = $1)
              AND ($2::text = '' OR status = $2)
              AND ($3::int = 0 OR project_id = $3)
            ORDER BY requested_at DESC, leave_request_id DESC;
            """,
            member_id,
            status,
            project_id,
        )
        return rows_payload("leave_requests", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def get_leave_request(leave_request_id: int | str) -> str:
    """Get a leave request and its ordered approval steps."""
    conn = await get_connection()
    try:
        leave_request_id = required_int(leave_request_id)
        request_row = await conn.fetchrow(
            "SELECT * FROM public.v_leave_request_status WHERE leave_request_id = $1;",
            leave_request_id,
        )
        if not request_row:
            return error("Leave request not found")
        approval_rows = await conn.fetch(
            """
            SELECT la.id, la.leave_request_id, la.approval_order, la.approver_role,
                   la.approver_member_id, approver.name AS approver_member_name,
                   approver.email AS approver_member_email, approver.role AS approver_member_role,
                   la.status, la.decision_by_member_id, decision_by.name AS decision_by_member_name,
                   la.decision_at, la.comments, la.created_at
            FROM public.leave_approval la
            LEFT JOIN public.member approver ON approver.id = la.approver_member_id
            LEFT JOIN public.member decision_by ON decision_by.id = la.decision_by_member_id
            WHERE la.leave_request_id = $1
            ORDER BY la.approval_order;
            """,
            leave_request_id,
        )
        return ok(
            leave_request=serialize_row(request_row),
            approvals=[serialize_row(row) for row in approval_rows],
        )
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def cancel_leave_request(
    leave_request_id: int | str,
    cancelled_by_member_id: int | str = 0,
    comments: str = "",
) -> str:
    """Cancel a leave request and skip any pending approval steps."""
    conn = await get_connection()
    try:
        leave_request_id = required_int(leave_request_id)
        cancelled_by_member_id = int_filter(cancelled_by_member_id)
        async with conn.transaction():
            request_row = await conn.fetchrow(
                """
                UPDATE public.leave_request
                SET status = 'cancelled',
                    final_decision_at = NOW()
                WHERE id = $1
                  AND status IN ('pending', 'approved')
                RETURNING id, member_id, project_id, leave_type, start_date, end_date,
                          days_requested, reason, status, requested_at, final_decision_at;
                """,
                leave_request_id,
            )
            if not request_row:
                return error("Leave request not found or cannot be cancelled")
            approval_rows = await conn.fetch(
                """
                UPDATE public.leave_approval
                SET status = 'skipped',
                    decision_by_member_id = NULLIF($2, 0),
                    decision_at = NOW(),
                    comments = CASE
                        WHEN $3::text = '' THEN 'Cancelled by requester or administrator'
                        ELSE $3
                    END
                WHERE leave_request_id = $1
                  AND status = 'pending'
                RETURNING id, leave_request_id, approval_order, approver_role, approver_member_id,
                          status, decision_by_member_id, decision_at, comments, created_at;
                """,
                leave_request_id,
                cancelled_by_member_id,
                comments,
            )
        return ok(
            leave_request=serialize_row(request_row),
            skipped_approvals=[serialize_row(row) for row in approval_rows],
        )
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def add_leave_approval_step(
    leave_request_id: int | str,
    approval_order: int | str,
    approver_role: str = "",
    approver_member_id: int | str = 0,
) -> str:
    """Add an approval step to a leave request by role or member ID."""
    conn = await get_connection()
    try:
        leave_request_id = required_int(leave_request_id)
        approval_order = required_int(approval_order)
        approver_member_id = int_filter(approver_member_id)
        approver_role = normalize_approver_role(approver_role) if approver_role else ""
        row = await conn.fetchrow(
            """
            INSERT INTO public.leave_approval
                (leave_request_id, approval_order, approver_role, approver_member_id)
            VALUES ($1, $2, $3, NULLIF($4, 0))
            RETURNING id, leave_request_id, approval_order, approver_role, approver_member_id,
                      status, decision_by_member_id, decision_at, comments, created_at;
            """,
            leave_request_id,
            approval_order,
            approver_role,
            approver_member_id,
        )
        request_row = await refresh_leave_request_status(conn, leave_request_id)
        return ok(leave_approval=serialize_row(row), leave_request=serialize_row(request_row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def review_leave_approval_step(
    approval_id: int | str,
    decision_by_member_id: int | str,
    status: str,
    comments: str = "",
) -> str:
    """Approve or reject one leave approval step."""
    conn = await get_connection()
    try:
        approval_id = required_int(approval_id)
        decision_by_member_id = required_int(decision_by_member_id)
        async with conn.transaction():
            if status == "approved":
                request_to_review = await conn.fetchrow(
                    """
                    SELECT lr.id, lr.project_id, lr.leave_type, lr.start_date, lr.days_requested
                    FROM public.leave_approval la
                    JOIN public.leave_request lr ON lr.id = la.leave_request_id
                    WHERE la.id = $1;
                    """,
                    approval_id,
                )
                if not request_to_review:
                    return error("Leave approval step not found")
                limit, policy = await get_leave_day_policy_limit(
                    conn,
                    request_to_review["leave_type"],
                    request_to_review["project_id"] or 0,
                    request_to_review["start_date"].isoformat(),
                )
                if limit is not None and float(request_to_review["days_requested"]) > limit:
                    return error(
                        "Cannot approve leave request because it violates active policy "
                        f"'{policy['name']}': maximum {limit:g} days per request, "
                        f"requested {float(request_to_review['days_requested']):g} days."
                    )
            row = await conn.fetchrow(
                """
                UPDATE public.leave_approval
                SET status = $2,
                    decision_by_member_id = $3,
                    decision_at = NOW(),
                    comments = $4
                WHERE id = $1
                RETURNING id, leave_request_id, approval_order, approver_role, approver_member_id,
                          status, decision_by_member_id, decision_at, comments, created_at;
                """,
                approval_id,
                status,
                decision_by_member_id,
                comments,
            )
            if not row:
                return error("Leave approval step not found")
            request_row = await refresh_leave_request_status(conn, row["leave_request_id"])
        return ok(leave_approval=serialize_row(row), leave_request=serialize_row(request_row))
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def list_leave_approvals(
    leave_request_id: int | str = 0,
    approver_role: str = "",
    approver_member_id: int | str = 0,
    status: str = "",
) -> str:
    """List leave approval steps."""
    conn = await get_connection()
    try:
        leave_request_id = int_filter(leave_request_id)
        approver_member_id = int_filter(approver_member_id)
        rows = await conn.fetch(
            """
            SELECT la.id, la.leave_request_id, la.approval_order, la.approver_role,
                   la.approver_member_id, approver.name AS approver_member_name,
                   approver.email AS approver_member_email, approver.role AS approver_member_role,
                   la.status, la.decision_by_member_id, decision_by.name AS decision_by_member_name,
                   la.decision_at, la.comments, la.created_at
            FROM public.leave_approval la
            LEFT JOIN public.member approver ON approver.id = la.approver_member_id
            LEFT JOIN public.member decision_by ON decision_by.id = la.decision_by_member_id
            WHERE ($1::int = 0 OR la.leave_request_id = $1)
              AND ($2::text = '' OR la.approver_role = $2)
              AND ($3::int = 0 OR la.approver_member_id = $3)
              AND ($4::text = '' OR la.status = $4)
            ORDER BY la.leave_request_id DESC, la.approval_order;
            """,
            leave_request_id,
            approver_role,
            approver_member_id,
            status,
        )
        return rows_payload("leave_approvals", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


# =============================================================================
# VIEW TOOLS
# =============================================================================


@mcp.tool(output_schema=None)
async def list_project_budget_summary() -> str:
    """List budget versus actual spend per project."""
    conn = await get_connection()
    try:
        rows = await conn.fetch("SELECT * FROM public.v_project_budget_summary ORDER BY project_id;")
        return rows_payload("project_budget_summary", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def list_member_teams() -> str:
    """List all teams that each member belongs to."""
    conn = await get_connection()
    try:
        rows = await conn.fetch("SELECT * FROM public.v_member_teams;")
        return rows_payload("member_teams", rows)
    except Exception as exc:
        return error(str(exc))
    finally:
        await conn.close()


@mcp.tool(output_schema=None)
async def list_task_workload() -> str:
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
