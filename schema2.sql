-- =============================================================================
-- Company Knowledge Base - Full Schema
-- Run this entire file in Neon SQL Editor to set up the database.
-- Safe to re-run: all objects use IF NOT EXISTS / OR REPLACE / ON CONFLICT.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. TABLES (FK-safe creation order)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.department (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.team (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    department_id INT  NOT NULL REFERENCES public.department(id) ON DELETE CASCADE,
    description   TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, department_id)
);

CREATE TABLE IF NOT EXISTS public.member (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE,
    phone      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.member
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS public.team_member (
    team_id   INT NOT NULL REFERENCES public.team(id)   ON DELETE CASCADE,
    member_id INT NOT NULL REFERENCES public.member(id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, member_id)
);

CREATE TABLE IF NOT EXISTS public.project (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    team_id     INT  NOT NULL REFERENCES public.team(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'on_hold', 'completed', 'cancelled')),
    start_date  DATE,
    end_date    DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.task (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    project_id  INT  NOT NULL REFERENCES public.project(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'todo'
                    CHECK (status IN ('todo', 'in_progress', 'review', 'done', 'cancelled')),
    priority    TEXT NOT NULL DEFAULT 'medium'
                    CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    due_date    DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.task_assignee (
    task_id     INT NOT NULL REFERENCES public.task(id)   ON DELETE CASCADE,
    member_id   INT NOT NULL REFERENCES public.member(id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_id, member_id)
);

CREATE TABLE IF NOT EXISTS public.project_budget (
    id           SERIAL PRIMARY KEY,
    project_id   INT            NOT NULL UNIQUE REFERENCES public.project(id) ON DELETE CASCADE,
    total_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    currency     TEXT           NOT NULL DEFAULT 'USD',
    approved_by  TEXT           NOT NULL DEFAULT '',
    approved_at  TIMESTAMPTZ,
    notes        TEXT           NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.project_expense (
    id                    SERIAL PRIMARY KEY,
    project_id            INT            NOT NULL REFERENCES public.project(id) ON DELETE CASCADE,
    title                 TEXT           NOT NULL,
    amount                NUMERIC(14, 2) NOT NULL,
    category              TEXT           NOT NULL DEFAULT '',
    incurred_at           DATE           NOT NULL DEFAULT CURRENT_DATE,
    recorded_by_member_id INT            REFERENCES public.member(id) ON DELETE SET NULL,
    notes                 TEXT           NOT NULL DEFAULT '',
    created_at            TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.project_knowledge (
    id               SERIAL PRIMARY KEY,
    project_id       INT         NOT NULL REFERENCES public.project(id) ON DELETE CASCADE,
    title            TEXT        NOT NULL,
    content          TEXT        NOT NULL,
    tags             TEXT[]      NOT NULL DEFAULT '{}',
    author_member_id INT         REFERENCES public.member(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- 2. INDEXES
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_team_department        ON public.team(department_id);
CREATE INDEX IF NOT EXISTS idx_team_member_member     ON public.team_member(member_id);
CREATE INDEX IF NOT EXISTS idx_project_team           ON public.project(team_id);
CREATE INDEX IF NOT EXISTS idx_project_status         ON public.project(status);
CREATE INDEX IF NOT EXISTS idx_task_project           ON public.task(project_id);
CREATE INDEX IF NOT EXISTS idx_task_status            ON public.task(status);
CREATE INDEX IF NOT EXISTS idx_task_priority          ON public.task(priority);
CREATE INDEX IF NOT EXISTS idx_task_assignee_member   ON public.task_assignee(member_id);
CREATE INDEX IF NOT EXISTS idx_expense_project        ON public.project_expense(project_id);
CREATE INDEX IF NOT EXISTS idx_expense_incurred_at    ON public.project_expense(incurred_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_project      ON public.project_knowledge(project_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_tags         ON public.project_knowledge USING GIN (tags);


-- -----------------------------------------------------------------------------
-- 3. VIEWS
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.v_project_budget_summary AS
SELECT
    p.id                              AS project_id,
    p.name                            AS project_name,
    t.name                            AS team_name,
    d.name                            AS department_name,
    p.status,
    COALESCE(pb.currency, 'USD')       AS currency,
    COALESCE(pb.total_amount, 0)       AS budget,
    COALESCE(SUM(pe.amount), 0)        AS total_spent,
    COALESCE(pb.total_amount, 0)
        - COALESCE(SUM(pe.amount), 0)  AS remaining,
    COALESCE(pb.approved_by, '')       AS approved_by,
    pb.approved_at
FROM      public.project         p
JOIN      public.team            t  ON t.id = p.team_id
JOIN      public.department      d  ON d.id = t.department_id
LEFT JOIN public.project_budget  pb ON pb.project_id = p.id
LEFT JOIN public.project_expense pe ON pe.project_id = p.id
GROUP BY  p.id, p.name, t.name, d.name, p.status,
          pb.currency, pb.total_amount, pb.approved_by, pb.approved_at;

CREATE OR REPLACE VIEW public.v_member_teams AS
SELECT
    m.id         AS member_id,
    m.name       AS member_name,
    m.email,
    m.role,
    t.id         AS team_id,
    t.name       AS team_name,
    d.id         AS department_id,
    d.name       AS department_name,
    tm.joined_at
FROM      public.member      m
JOIN      public.team_member tm ON tm.member_id = m.id
JOIN      public.team        t  ON t.id = tm.team_id
JOIN      public.department  d  ON d.id = t.department_id
ORDER BY  m.name, d.name, t.name;

CREATE OR REPLACE VIEW public.v_task_workload AS
SELECT
    m.id                                             AS member_id,
    m.name                                           AS member_name,
    m.role,
    COUNT(tk.id) FILTER (WHERE tk.status = 'todo')        AS todo,
    COUNT(tk.id) FILTER (WHERE tk.status = 'in_progress') AS in_progress,
    COUNT(tk.id) FILTER (WHERE tk.status = 'review')      AS in_review,
    COUNT(tk.id) FILTER (WHERE tk.status NOT IN ('done','cancelled')) AS total_open
FROM      public.member        m
LEFT JOIN public.task_assignee ta ON ta.member_id = m.id
LEFT JOIN public.task          tk ON tk.id = ta.task_id
GROUP BY  m.id, m.name, m.role
ORDER BY  total_open DESC, m.name;


-- -----------------------------------------------------------------------------
-- 4. SEED DATA
-- -----------------------------------------------------------------------------

INSERT INTO public.department (name, description) VALUES
    ('Engineering',  'Builds and maintains all software products'),
    ('Marketing',    'Handles brand, growth, and customer acquisition'),
    ('Operations',   'Runs internal processes, HR, and finance')
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO public.team (name, department_id, description)
SELECT 'Backend',  id, 'API and database development' FROM public.department WHERE name = 'Engineering'
ON CONFLICT (name, department_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO public.team (name, department_id, description)
SELECT 'Frontend', id, 'Web and mobile UI development' FROM public.department WHERE name = 'Engineering'
ON CONFLICT (name, department_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO public.team (name, department_id, description)
SELECT 'Growth', id, 'SEO, paid ads, and analytics' FROM public.department WHERE name = 'Marketing'
ON CONFLICT (name, department_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO public.team (name, department_id, description)
SELECT 'HR', id, 'Hiring and people operations' FROM public.department WHERE name = 'Operations'
ON CONFLICT (name, department_id) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO public.member (name, email, phone, role) VALUES
    ('Alice Rahman',   'alice@company.com',   '+8801700000001', 'Backend Engineer'),
    ('Bob Hossain',    'bob@company.com',     '+8801700000002', 'Frontend Engineer'),
    ('Carol Ahmed',    'carol@company.com',   '+8801700000003', 'Full-Stack Engineer'),
    ('David Islam',    'david@company.com',   '+8801700000004', 'Growth Marketer'),
    ('Eva Chowdhury',  'eva@company.com',     '+8801700000005', 'Engineering Manager'),
    ('Farhan Karim',   'farhan@company.com',  '+8801700000006', 'HR Manager')
ON CONFLICT (email) DO UPDATE
SET name = EXCLUDED.name, phone = EXCLUDED.phone, role = EXCLUDED.role;

INSERT INTO public.team_member (team_id, member_id)
SELECT t.id, m.id
FROM public.team t, public.member m
WHERE (t.name = 'Backend'  AND m.email IN ('alice@company.com', 'carol@company.com', 'eva@company.com'))
   OR (t.name = 'Frontend' AND m.email IN ('bob@company.com', 'carol@company.com', 'eva@company.com'))
   OR (t.name = 'Growth'   AND m.email IN ('david@company.com'))
   OR (t.name = 'HR'       AND m.email IN ('farhan@company.com'))
ON CONFLICT DO NOTHING;

INSERT INTO public.project (name, team_id, description, status, start_date, end_date)
SELECT 'MCP Knowledge Server', t.id, 'Build the company-wide MCP server backed by Neon Postgres',
       'active', '2026-01-01', '2026-06-30'
FROM public.team t WHERE t.name = 'Backend'
AND NOT EXISTS (
    SELECT 1 FROM public.project p WHERE p.name = 'MCP Knowledge Server' AND p.team_id = t.id
);

INSERT INTO public.project (name, team_id, description, status, start_date, end_date)
SELECT 'Company Dashboard', t.id, 'Internal React dashboard for ops and finance metrics',
       'active', '2026-02-01', '2026-07-31'
FROM public.team t WHERE t.name = 'Frontend'
AND NOT EXISTS (
    SELECT 1 FROM public.project p WHERE p.name = 'Company Dashboard' AND p.team_id = t.id
);

INSERT INTO public.project (name, team_id, description, status, start_date, end_date)
SELECT 'Q2 Growth Campaign', t.id, 'Paid ads + SEO push for Q2 user acquisition targets',
       'active', '2026-04-01', '2026-06-30'
FROM public.team t WHERE t.name = 'Growth'
AND NOT EXISTS (
    SELECT 1 FROM public.project p WHERE p.name = 'Q2 Growth Campaign' AND p.team_id = t.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Design full DB schema', p.id, 'All tables, indexes, views, and seed data', 'done', 'critical', '2026-05-20'
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Design full DB schema' AND tk.project_id = p.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Implement MCP tools', p.id, 'CRUD tools for all new tables in main.py', 'in_progress', 'high', '2026-05-30'
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Implement MCP tools' AND tk.project_id = p.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Write integration tests', p.id, 'Test every tool against Neon staging', 'todo', 'medium', '2026-06-10'
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Write integration tests' AND tk.project_id = p.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Set up React project', p.id, 'Vite + TypeScript + Tailwind scaffold', 'done', 'high', '2026-02-15'
FROM public.project p WHERE p.name = 'Company Dashboard'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Set up React project' AND tk.project_id = p.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Budget summary widget', p.id, 'Pull from v_project_budget_summary view', 'in_progress', 'high', '2026-05-25'
FROM public.project p WHERE p.name = 'Company Dashboard'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Budget summary widget' AND tk.project_id = p.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Launch Google Ads', p.id, 'Set up and fund the Q2 ad campaigns', 'in_progress', 'critical', '2026-04-10'
FROM public.project p WHERE p.name = 'Q2 Growth Campaign'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Launch Google Ads' AND tk.project_id = p.id
);

INSERT INTO public.task (title, project_id, description, status, priority, due_date)
SELECT 'Keyword research', p.id, 'Target 50 high-intent keywords for SEO', 'done', 'medium', '2026-04-05'
FROM public.project p WHERE p.name = 'Q2 Growth Campaign'
AND NOT EXISTS (
    SELECT 1 FROM public.task tk WHERE tk.title = 'Keyword research' AND tk.project_id = p.id
);

INSERT INTO public.task_assignee (task_id, member_id)
SELECT tk.id, m.id
FROM   public.task tk, public.member m
WHERE  tk.title = 'Design full DB schema' AND m.email = 'alice@company.com'
ON CONFLICT DO NOTHING;

INSERT INTO public.task_assignee (task_id, member_id)
SELECT tk.id, m.id
FROM   public.task tk, public.member m
WHERE  tk.title = 'Implement MCP tools' AND m.email IN ('alice@company.com', 'carol@company.com')
ON CONFLICT DO NOTHING;

INSERT INTO public.task_assignee (task_id, member_id)
SELECT tk.id, m.id
FROM   public.task tk, public.member m
WHERE  tk.title = 'Budget summary widget' AND m.email IN ('bob@company.com', 'carol@company.com')
ON CONFLICT DO NOTHING;

INSERT INTO public.task_assignee (task_id, member_id)
SELECT tk.id, m.id
FROM   public.task tk, public.member m
WHERE  tk.title = 'Launch Google Ads' AND m.email = 'david@company.com'
ON CONFLICT DO NOTHING;

INSERT INTO public.task_assignee (task_id, member_id)
SELECT tk.id, m.id
FROM   public.task tk, public.member m
WHERE  tk.title = 'Keyword research' AND m.email = 'david@company.com'
ON CONFLICT DO NOTHING;

INSERT INTO public.project_budget (project_id, total_amount, currency, approved_by, approved_at, notes)
SELECT p.id, 25000.00, 'USD', 'eva@company.com', '2026-01-05 09:00+00', 'Approved for H1 2026'
FROM   public.project p WHERE p.name = 'MCP Knowledge Server'
ON CONFLICT (project_id) DO UPDATE
SET total_amount = EXCLUDED.total_amount, currency = EXCLUDED.currency,
    approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at, notes = EXCLUDED.notes;

INSERT INTO public.project_budget (project_id, total_amount, currency, approved_by, approved_at, notes)
SELECT p.id, 18000.00, 'USD', 'eva@company.com', '2026-02-03 09:00+00', 'Dashboard Q1-Q2 budget'
FROM   public.project p WHERE p.name = 'Company Dashboard'
ON CONFLICT (project_id) DO UPDATE
SET total_amount = EXCLUDED.total_amount, currency = EXCLUDED.currency,
    approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at, notes = EXCLUDED.notes;

INSERT INTO public.project_budget (project_id, total_amount, currency, approved_by, approved_at, notes)
SELECT p.id, 40000.00, 'USD', 'farhan@company.com', '2026-03-28 09:00+00', 'Q2 paid + organic budget'
FROM   public.project p WHERE p.name = 'Q2 Growth Campaign'
ON CONFLICT (project_id) DO UPDATE
SET total_amount = EXCLUDED.total_amount, currency = EXCLUDED.currency,
    approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at, notes = EXCLUDED.notes;

INSERT INTO public.project_expense (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
SELECT p.id, 'Neon Postgres Pro Plan', 19.00, 'software', '2026-01-10',
       (SELECT id FROM public.member WHERE email = 'alice@company.com'), 'Monthly DB hosting'
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.project_expense pe
    WHERE pe.project_id = p.id AND pe.title = 'Neon Postgres Pro Plan'
      AND pe.incurred_at = '2026-01-10' AND pe.amount = 19.00
);

INSERT INTO public.project_expense (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
SELECT p.id, 'Neon Postgres Pro Plan', 19.00, 'software', '2026-02-10',
       (SELECT id FROM public.member WHERE email = 'alice@company.com'), ''
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.project_expense pe
    WHERE pe.project_id = p.id AND pe.title = 'Neon Postgres Pro Plan'
      AND pe.incurred_at = '2026-02-10' AND pe.amount = 19.00
);

INSERT INTO public.project_expense (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
SELECT p.id, 'Figma Team Seat', 45.00, 'software', '2026-02-05',
       (SELECT id FROM public.member WHERE email = 'bob@company.com'), 'Design tool for dashboard mockups'
FROM public.project p WHERE p.name = 'Company Dashboard'
AND NOT EXISTS (
    SELECT 1 FROM public.project_expense pe
    WHERE pe.project_id = p.id AND pe.title = 'Figma Team Seat'
      AND pe.incurred_at = '2026-02-05' AND pe.amount = 45.00
);

INSERT INTO public.project_expense (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
SELECT p.id, 'Google Ads - April', 8500.00, 'advertising', '2026-04-30',
       (SELECT id FROM public.member WHERE email = 'david@company.com'), 'Q2 first month spend'
FROM public.project p WHERE p.name = 'Q2 Growth Campaign'
AND NOT EXISTS (
    SELECT 1 FROM public.project_expense pe
    WHERE pe.project_id = p.id AND pe.title = 'Google Ads - April'
      AND pe.incurred_at = '2026-04-30' AND pe.amount = 8500.00
);

INSERT INTO public.project_expense (project_id, title, amount, category, incurred_at, recorded_by_member_id, notes)
SELECT p.id, 'SEO Tool (Ahrefs)', 199.00, 'software', '2026-04-01',
       (SELECT id FROM public.member WHERE email = 'david@company.com'), ''
FROM public.project p WHERE p.name = 'Q2 Growth Campaign'
AND NOT EXISTS (
    SELECT 1 FROM public.project_expense pe
    WHERE pe.project_id = p.id AND pe.title = 'SEO Tool (Ahrefs)'
      AND pe.incurred_at = '2026-04-01' AND pe.amount = 199.00
);

INSERT INTO public.project_knowledge (project_id, title, content, tags, author_member_id)
SELECT p.id, 'Architecture Overview',
       E'## MCP Server Architecture\n\nSingle-file FastMCP server connected to Neon Postgres via asyncpg.\n\n- Transport: Streamable HTTP on port 8080\n- Each tool opens and closes its own DB connection',
       ARRAY['architecture', 'fastmcp', 'neon'],
       (SELECT id FROM public.member WHERE email = 'alice@company.com')
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.project_knowledge pk
    WHERE pk.project_id = p.id AND pk.title = 'Architecture Overview'
);

INSERT INTO public.project_knowledge (project_id, title, content, tags, author_member_id)
SELECT p.id, 'Inspector Gotcha',
       E'When using MCP Inspector, clear the URL field and set it to http://localhost:8080/mcp before connecting if the browser reused an old URL.',
       ARRAY['inspector', 'debugging', 'tip'],
       (SELECT id FROM public.member WHERE email = 'carol@company.com')
FROM public.project p WHERE p.name = 'MCP Knowledge Server'
AND NOT EXISTS (
    SELECT 1 FROM public.project_knowledge pk
    WHERE pk.project_id = p.id AND pk.title = 'Inspector Gotcha'
);

INSERT INTO public.project_knowledge (project_id, title, content, tags, author_member_id)
SELECT p.id, 'Q2 Keyword Strategy',
       E'## Target Keyword Clusters\n\n1. Brand terms\n2. Product long-tail terms\n3. Competitor comparison terms',
       ARRAY['seo', 'keywords', 'q2'],
       (SELECT id FROM public.member WHERE email = 'david@company.com')
FROM public.project p WHERE p.name = 'Q2 Growth Campaign'
AND NOT EXISTS (
    SELECT 1 FROM public.project_knowledge pk
    WHERE pk.project_id = p.id AND pk.title = 'Q2 Keyword Strategy'
);


-- -----------------------------------------------------------------------------
-- 5. QUICK VERIFICATION QUERIES
-- -----------------------------------------------------------------------------

-- SELECT * FROM public.v_project_budget_summary;
-- SELECT * FROM public.v_member_teams;
-- SELECT * FROM public.v_task_workload;
