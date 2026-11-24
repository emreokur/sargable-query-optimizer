# Sargable

Find out why a PostgreSQL query is slow, and what fixes it.

Paste a query and Sargable runs it, shows the real execution plan with the bottleneck marked, asks Claude for fixes, and measures them against the database: real before and after timings, not guesses.

- **Plan:** every node with its own time, rows and rows thrown away, and the slowest one highlighted.
- **Diagnosis:** Claude's explanation of the bottleneck and up to three fixes (indexes, query rewrites, or both), compared on one scale.
- **Fixes:** each one with its SQL, before and after, and the plan it produces.

It comes with a demo veterinary database (5 million rows, no indexes) and ten slow example queries.

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) and an [Anthropic API key](https://console.anthropic.com).

```bash
git clone https://github.com/emreokur/sargable-query-optimizer.git
cd sargable-query-optimizer
cp .env.example .env
```

Paste your key into `.env`, then:

```bash
docker compose up
```

Open http://localhost:8000. The first start builds the demo database, which takes about a minute.

## Use your own database

Press **Change connection**, fill in the form and press **Connect**. Nothing is written to a file: the password is kept in memory only, so you type it again after a restart.

1. Connect as a role that can only read. Sargable checks this, and refuses to run with a list of what to fix if the role can do more:

   ```sql
   CREATE ROLE sargable LOGIN PASSWORD 'choose-a-password';
   GRANT pg_read_all_data TO sargable;
   ```

2. For a database on the same computer, use `host.docker.internal` as the host (`localhost` would mean the Sargable container itself).
3. A remote database needs SSL mode `verify-full`, so the password only ever goes to the real server. Providers with their own certificate authority (Amazon RDS, Azure, Google Cloud SQL) need their CA certificate pasted into the form; others work with it left empty.
4. Optional: `CREATE EXTENSION hypopg;` in your database lets Sargable estimate index fixes without building them.

To try live mode on the demo data first: host `db`, database `vet`, user and password `demo_readonly`, SSL mode `disable`.

## Live and sandbox

Anything you connect is **live**: read-only, and Sargable never builds anything on it. Tick **Sandbox** only for a disposable copy.

| | Live (your databases) | Sandbox (the demo, or a copy you tick it for) |
| --- | --- | --- |
| Your query | One `SELECT`, run read-only | Any single statement, run and rolled back |
| Index fixes | Never built. Estimated by the planner with HypoPG, otherwise shown unmeasured | Built, timed, rolled back |
| Rewrites from Claude | Shown for review. Run only when you press **Test** | Timed automatically |

In sandbox mode Sargable really builds indexes (blocking writes to that table while it builds) and runs `UPDATE` or `DELETE` statements before rolling them back.

## How a live database is protected

Each layer is meant to hold on its own.

1. **The role.** Sargable refuses to run if its role, or any role it can switch to, can do more than read: superuser, role or database creation, any privilege beyond `SELECT` on a table, column or sequence, owning anything, creating objects, or membership in a predefined role that can write, signal other sessions or reach server files.
2. **Read-only transactions**, always rolled back, with a 15 second statement timeout, a 2 second lock timeout, and one statement at a time.
3. **PostgreSQL's own parser** (via [pglast](https://github.com/lelit/pglast)) accepts a single `SELECT` only: no `SELECT INTO`, no `FOR UPDATE`, no data-modifying CTEs.
4. **No index builds.** Index fixes are only planned, never executed.
5. **Claude is not trusted.** Nothing Claude wrote runs without your click, and a rewrite that calls a function your query does not is flagged and not offered for testing.
6. **Masking.** Literal values and comments are replaced before anything is sent to Claude, and only the tables in the plan are described. Row data is never sent.
7. **The connection.** Built from separate fields with the driver's own escaping, never from a pasted URL. A remote server must prove its identity (`verify-full`) before the password is sent. The password is never written to disk, stored by the browser, or echoed in a response. Switching connections waits for a running query to finish.
8. **The web page.** Sargable listens on `127.0.0.1` only, rejects requests for other host names (against DNS rebinding), accepts JSON bodies only, and allows no cross-origin requests.

### What it does not protect against

- **Load.** Your query, and any rewrite you test, really runs. The timeout limits time, not memory or temporary disk. Prefer a read replica, and consider `ALTER ROLE sargable SET work_mem = '16MB'`.
- **A rewrite you test is yours.** Read it first.
- **No login.** Anyone who can reach the port can use it and change its connection. It is meant for your own machine. To share it, put it behind your own authentication and HTTPS.
- **The password is in the app's memory** as plain text while it runs.
- **What still reaches Claude:** table, column and index names, aliases, row counts and costs.
- **Functions your own query calls.** The role's `EXECUTE` privileges are the boundary for those.
- **Result equivalence.** A rewrite is checked by row count, not row by row.

## Development

Run the database on its own, then the backend and the frontend with live reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/uvicorn main:app --app-dir backend --env-file .env --reload
npm install --prefix frontend && npm run dev --prefix frontend
```

Open http://localhost:5173. Tests (the backend suite needs the database running):

```bash
backend/.venv/bin/pytest backend
npm test --prefix frontend
```

| Path | What |
| --- | --- |
| `backend/` | FastAPI app: running plans (`db.py`), masking (`mask.py`), the Claude call (`llm.py`), routes (`main.py`) |
| `frontend/` | React and TypeScript, built with Vite |
| `db/` | The demo database image and its seed |

## License

[MIT](LICENSE)
