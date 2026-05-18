# PostgreSQL Member MCP Server

A FastMCP server that connects to a PostgreSQL database and exposes tools to manage a `member` table. Works with MCP Inspector and any MCP-compatible AI client.

---

## Requirements

- Python 3.11+
- PostgreSQL 17
- `uv` package manager

---

## Setup

### 1. Install `uv` (if not installed)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart terminal after installing.

### 2. Install dependencies

```powershell
uv sync
```

### 3. Create `.env` file

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your database credentials:

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=YOUR_DATABASE_NAME
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
```

### 4. Create the database in pgAdmin

```sql
CREATE DATABASE "YOUR_DATABASE_NAME";
```

---

## Running the Server

```powershell
.\.venv\Scripts\python.exe main.py
```

Server starts at: `http://localhost:8080/mcp`

> If port 8080 is busy, run:
>
> ```powershell
> Get-NetTCPConnection -LocalPort 8080 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
> ```

---

## Connecting with MCP Inspector

| Field           | Value                       |
| --------------- | --------------------------- |
| Transport Type  | `Streamable HTTP`           |
| URL             | `http://localhost:8080/mcp` |
| Connection Type | `Direct`                    |

Click **Connect**.

---

## Available Tools

| Tool                  | Description                                 | Parameters                          |
| --------------------- | ------------------------------------------- | ----------------------------------- |
| `create_member_table` | Creates `public.member` table if not exists | None                                |
| `add_member`          | Adds a new member                           | `name`, `email`, `phone` (optional) |
| `list_members`        | Lists all members                           | None                                |

### Member Table Schema

```sql
CREATE TABLE public.member (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT UNIQUE NOT NULL,
    phone      TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## Important Notes

| Problem                                   | Fix Applied                                 |
| ----------------------------------------- | ------------------------------------------- |
| Browser blocks cross-origin requests      | `CORSMiddleware` with `allow_origins=["*"]` |
| Inspector sends no session ID             | `stateless_http=True`                       |
| Port 8000 taken by Splunk on this machine | Server runs on port `8080`                  |
| `mcp.run()` does not support middleware   | Use `mcp.http_app()` + `uvicorn.run()`      |

---

## Project Structure

```
.
├── main.py          # MCP server with all tools
├── .env             # Database credentials (never commit this)
├── .env.example     # Template for .env
├── pyproject.toml   # Dependencies
└── README.md        # This file
```

---

## Prompt to Give Claude Next Time

> "This is a FastMCP 3.x MCP server connected to PostgreSQL. Run it with `.\.venv\Scripts\python.exe main.py`. It uses `mcp.http_app()` with `stateless_http=True` and `CORSMiddleware` on port 8080. Connect MCP Inspector with Transport Type: Streamable HTTP, URL: http://localhost:8080/mcp, Connection Type: Direct. Database credentials are in `.env`."
