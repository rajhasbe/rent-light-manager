"""
Simple Rent & Light Bill Manager — Flask + SQLite/PostgreSQL
-----------------------------------------------------------
One-file Flask app with a pluggable database layer:
- Uses **SQLite** locally (default)
- Automatically switches to **PostgreSQL** if `DATABASE_URL` is set (for cloud hosting on Render, etc.)

Run locally:
  1) pip install -r requirements.txt   (or at least: pip install flask)
  2) python app.py
  3) Open http://127.0.0.1:5000

On Render (cloud):
  - Add `DATABASE_URL` (from Render PostgreSQL) and `SECRET_KEY` env vars
  - Use `gunicorn app:app` as start command

This is an MVP — secure it before using on the open internet. For local use, it's fine.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from flask import (
    Flask,
    g,
    redirect,
    render_template_string,
    request,
    url_for,
    flash,
    session,
    Response,
)
from werkzeug.security import generate_password_hash, check_password_hash

# Optional: Postgres driver (only used if DATABASE_URL is present)
try:
    import psycopg2
    import psycopg2.extras
except Exception:  # pragma: no cover
    psycopg2 = None  # type: ignore

APP_TITLE = "Rent & Light Bill Manager"
DB_PATH = Path("rent_manager.db")
DATABASE_URL = os.getenv("DATABASE_URL")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production")  # required for sessions

# --------------------- Lightweight DB Abstraction ---------------------

class PGResult:
    def __init__(self, cur):
        self.cur = cur
    def fetchall(self):
        rows = self.cur.fetchall()
        return rows
    def fetchone(self):
        return self.cur.fetchone()

class PGConn:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, query: str, params: Iterable[Any] = ()):  # mimic sqlite3 Connection API
        q = query.replace("?", "%s")  # basic placeholder swap
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(q, params)
        return PGResult(cur)
    def commit(self):
        self._conn.commit()
    def close(self):
        self._conn.close()


def using_postgres() -> bool:
    return bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))


def get_db():
    if "db" in g:
        return g.db
    if using_postgres():
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is required for PostgreSQL but not installed. pip install psycopg2-binary")
        conn = psycopg2.connect(DATABASE_URL, sslmode=os.getenv("PGSSLMODE", "require"))
        g.db = PGConn(conn)
        return g.db
    # default sqlite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass

# --------------------- Schema (SQLite vs Postgres) ---------------------

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    room TEXT,
    monthly_rent INTEGER NOT NULL,
    rate_per_unit REAL NOT NULL DEFAULT 8.0,
    last_reading INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    month INTEGER NOT NULL,
    year INTEGER NOT NULL,
    start_reading INTEGER NOT NULL,
    end_reading INTEGER NOT NULL,
    units INTEGER NOT NULL,
    light_bill REAL NOT NULL,
    total REAL NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    room TEXT,
    monthly_rent INTEGER NOT NULL,
    rate_per_unit REAL NOT NULL DEFAULT 8.0,
    last_reading INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS bills (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    month INTEGER NOT NULL,
    year INTEGER NOT NULL,
    start_reading INTEGER NOT NULL,
    end_reading INTEGER NOT NULL,
    units INTEGER NOT NULL,
    light_bill REAL NOT NULL,
    total REAL NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);
"""


def init_db():
    if using_postgres():
        db = get_db()
        for stmt in POSTGRES_SCHEMA.strip().split(";\n\n"):
            if stmt.strip():
                db.execute(stmt)
        db.commit()
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executescript(SQLITE_SCHEMA)

# Initialize DB on startup
init_db()

# --------------------- Templates ---------------------

BASE_HTML = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{{ title }}</title>
  <style>
    body{font-family:system-ui, -apple-system, Segoe UI, Roboto, Arial; margin:0; background:#f7f7fb; color:#111}
    header{background:#222; color:#fff; padding:16px 20px;}
    header h1{margin:0; font-size:20px}
    main{max-width:980px; margin:24px auto; background:#fff; padding:20px; border-radius:14px; box-shadow:0 4px 12px rgba(0,0,0,.06)}
    .row{display:flex; gap:12px; flex-wrap:wrap}
    .card{flex:1; min-width:220px; background:#fafafa; border:1px solid #eee; padding:14px; border-radius:12px}
    table{width:100%; border-collapse:collapse; margin-top:10px}
    th,td{padding:10px; border-bottom:1px solid #eee; text-align:left}
    th{background:#fafafa}
    a.button, button, input[type=submit]{background:#111; color:#fff; border:none; padding:10px 12px; border-radius:10px; text-decoration:none; cursor:pointer}
    .ghost{background:#f0f0f5; color:#111}
    .ok{color:#0a7d2a; font-weight:600}
    .bad{color:#b30000; font-weight:600}
    form .group{margin:10px 0}
    input, select{padding:10px; border:1px solid #ddd; border-radius:10px; width:100%}
    .toolbar{display:flex; gap:8px; flex-wrap:wrap}
    .flash{background:#e7f7ed; color:#0a7d2a; padding:8px 12px; border-radius:8px; margin:8px 0}
  </style>
</head>
<body>
  <header style=\"display:flex; align-items:center; justify-content:space-between\">
    <h1>{{ app_title }}</h1>
    <div>
      {% if session.get('user_id') %}
        <span style=\"margin-right:10px\">👤 {{ session.get('username') }}</span>
        <a class=\"button ghost\" href=\"{{ url_for('logout') }}\">Logout</a>
      {% else %}
        <a class=\"button ghost\" href=\"{{ url_for('login') }}\">Login</a>
      {% endif %}
    </div>
  </header>
  <main>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for m in messages %}<div class=\"flash\">{{ m }}</div>{% endfor %}
      {% endif %}
    {% endwith %}
    {{ body|safe }}
  </main>
</body>
</html>
"""

# --------------------- Auth ---------------------

@app.before_request
def require_login():
    open_endpoints = {"login", "auth_init", "static", "health"}
    if request.endpoint in open_endpoints:
        return
    # If you want to allow public receipts, uncomment below
    # if request.endpoint == "download_receipt":
    #     return
    if not session.get("user_id") and request.path not in ("/login", "/auth/init", "/health"):
        return redirect(url_for("login"))

@app.route("/auth/init", methods=["GET","POST"])
def auth_init():
    db = get_db()
    user = db.execute("SELECT id FROM users LIMIT 1").fetchone()
    if user:
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            flash("Username and password are required.")
        else:
            db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                (username, generate_password_hash(password), datetime.now().isoformat()),
            )
            db.commit()
            flash("Admin user created. Please login.")
            return redirect(url_for("login"))
    body = render_template_string(
        """
        <h2>Initialize Admin</h2>
        <p>No users found. Create the first admin account.</p>
        <form method=\"post\">
          <div class=\"group\"><label>Username</label><input name=\"username\" required></div>
          <div class=\"group\"><label>Password</label><input type=\"password\" name=\"password\" required></div>
          <input type=\"submit\" value=\"Create Admin\">
        </form>
        """
    )
    return render_template_string(BASE_HTML, title="Init | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/login", methods=["GET","POST"])
def login():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            flash("Welcome back!")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.")
    body = render_template_string(
        """
        <h2>Login</h2>
        <form method=\"post\">
          <div class=\"group\"><label>Username</label><input name=\"username\" required></div>
          <div class=\"group\"><label>Password</label><input type=\"password\" name=\"password\" required></div>
          <input type=\"submit\" value=\"Login\">
        </form>
        <p style=\"margin-top:10px;color:#555\">First time? If you haven't created a user yet, go to <a href=\"{{ url_for('auth_init') }}\">Initialize Admin</a>.</p>
        """
    )
    return render_template_string(BASE_HTML, title="Login | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))

@app.route("/health")
def health():
    return {"status": "ok", "app": APP_TITLE}

# --------------------- Routes ---------------------

@app.route("/")
def dashboard():
    db = get_db()
    tenants = db.execute("SELECT * FROM tenants ORDER BY id DESC").fetchall()
    unpaid = db.execute(
        "SELECT b.*, t.name, t.room FROM bills b JOIN tenants t ON t.id=b.tenant_id WHERE paid=0 ORDER BY year DESC, month DESC"
    ).fetchall()
    this_month = datetime.now().month
    this_year = datetime.now().year

    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button\" href=\"{{ url_for('new_tenant') }}\">➕ Add Tenant</a>
          <a class=\"button ghost\" href=\"{{ url_for('tenants_list') }}\">👥 Tenants</a>
          <a class=\"button ghost\" href=\"{{ url_for('bills_list') }}\">🧾 Bills</a>
          <a class=\"button ghost\" href=\"{{ url_for('reports') }}\">📊 Reports</a>
          <a class=\"button ghost\" href=\"{{ url_for('new_reading') }}\">⚡ New Reading</a>
        </div>

        <div class=\"row\" style=\"margin-top:12px\">
          <div class=\"card\">
            <div><strong>Total tenants</strong></div>
            <div style=\"font-size:28px\">{{ tenants|length }}</div>
          </div>
          <div class=\"card\">
            <div><strong>Unpaid bills</strong></div>
            <div style=\"font-size:28px\">{{ unpaid|length }}</div>
          </div>
          <div class=\"card\">
            <div><strong>Current cycle</strong></div>
            <div>{{ this_month }}/{{ this_year }}</div>
          </div>
        </div>

        <h2>Unpaid Bills</h2>
        <table>
          <tr>
            <th>Tenant</th><th>Room</th><th>Month</th><th>Units</th><th>Light Bill</th><th>Total</th><th>Status</th><th>Action</th>
          </tr>
          {% for b in unpaid %}
          <tr>
            <td>{{ b['name'] }}</td>
            <td>{{ b['room'] or '-' }}</td>
            <td>{{ '%02d/%d' % (b['month'], b['year']) }}</td>
            <td>{{ b['units'] }}</td>
            <td>₹{{ '%.0f' % b['light_bill'] }}</td>
            <td><strong>₹{{ '%.0f' % b['total'] }}</strong></td>
            <td class=\"bad\">Unpaid</td>
            <td>
              <form method=\"post\" action=\"{{ url_for('mark_paid', bill_id=b['id']) }}\">
                <input type=\"submit\" value=\"Mark Paid\">
              </form>
            </td>
          </tr>
          {% endfor %}
        </table>
        """,
        tenants=tenants,
        unpaid=unpaid,
        this_month=this_month,
        this_year=this_year,
    )

    return render_template_string(BASE_HTML, title=APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/tenants")
def tenants_list():
    db = get_db()
    tenants = db.execute("SELECT * FROM tenants ORDER BY name").fetchall()
    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button\" href=\"{{ url_for('new_tenant') }}\">➕ Add Tenant</a>
          <a class=\"button ghost\" href=\"{{ url_for('dashboard') }}\">🏠 Dashboard</a>
        </div>
        <h2>Tenants</h2>
        <table>
          <tr><th>Name</th><th>Room</th><th>Rent (₹)</th><th>Rate/Unit (₹)</th><th>Last Reading</th><th>Actions</th></tr>
          {% for t in tenants %}
          <tr>
            <td>{{ t['name'] }}</td><td>{{ t['room'] or '-' }}</td><td>{{ t['monthly_rent'] }}</td><td>{{ '%.2f' % t['rate_per_unit'] }}</td><td>{{ t['last_reading'] }}</td>
            <td><a class=\"button ghost\" href=\"{{ url_for('edit_tenant', tenant_id=t['id']) }}\">Edit</a></td>
          </tr>
          {% endfor %}
        </table>
        """,
        tenants=tenants,
    )
    return render_template_string(BASE_HTML, title="Tenants | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/tenant/new", methods=["GET","POST"])
def new_tenant():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        room = request.form.get("room", "").strip()
        monthly_rent = int(request.form.get("monthly_rent", 0) or 0)
        rate_per_unit = float(request.form.get("rate_per_unit", 8.0) or 8.0)
        last_reading = int(request.form.get("last_reading", 0) or 0)
        if not name or monthly_rent <= 0:
            flash("Name and monthly rent are required.")
        else:
            db = get_db()
            db.execute(
                "INSERT INTO tenants (name, room, monthly_rent, rate_per_unit, last_reading, created_at) VALUES (?,?,?,?,?,?)",
                (name, room, monthly_rent, rate_per_unit, last_reading, datetime.now().isoformat()),
            )
            db.commit()
            flash("Tenant added.")
            return redirect(url_for("tenants_list"))
    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button ghost\" href=\"{{ url_for('tenants_list') }}\">← Back</a>
        </div>
        <h2>New Tenant</h2>
        <form method=\"post\">
          <div class=\"group\"><label>Name</label><input name=\"name\" required></div>
          <div class=\"group\"><label>Room</label><input name=\"room\"></div>
          <div class=\"group\"><label>Monthly Rent (₹)</label><input name=\"monthly_rent\" type=\"number\" required></div>
          <div class=\"group\"><label>Rate per Unit (₹)</label><input name=\"rate_per_unit\" type=\"number\" step=\"0.01\" value=\"8\"></div>
          <div class=\"group\"><label>Initial Meter Reading</label><input name=\"last_reading\" type=\"number\" value=\"0\"></div>
          <input type=\"submit\" value=\"Save\">
        </form>
        """
    )
    return render_template_string(BASE_HTML, title="New Tenant | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/tenant/<int:tenant_id>/edit", methods=["GET","POST"])
def edit_tenant(tenant_id):
    db = get_db()
    tenant = db.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    if not tenant:
        flash("Tenant not found")
        return redirect(url_for("tenants_list"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        room = request.form.get("room", "").strip()
        monthly_rent = int(request.form.get("monthly_rent", 0) or 0)
        rate_per_unit = float(request.form.get("rate_per_unit", 8.0) or 8.0)
        last_reading = int(request.form.get("last_reading", 0) or 0)
        db.execute(
            "UPDATE tenants SET name=?, room=?, monthly_rent=?, rate_per_unit=?, last_reading=? WHERE id=?",
            (name, room, monthly_rent, rate_per_unit, last_reading, tenant_id),
        )
        db.commit()
        flash("Tenant updated.")
        return redirect(url_for("tenants_list"))
    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button ghost\" href=\"{{ url_for('tenants_list') }}\">← Back</a>
        </div>
        <h2>Edit Tenant</h2>
        <form method=\"post\">
          <div class=\"group\"><label>Name</label><input name=\"name\" value=\"{{ t['name'] }}\" required></div>
          <div class=\"group\"><label>Room</label><input name=\"room\" value=\"{{ t['room'] }}\"></div>
          <div class=\"group\"><label>Monthly Rent (₹)</label><input name=\"monthly_rent\" type=\"number\" value=\"{{ t['monthly_rent'] }}\" required></div>
          <div class=\"group\"><label>Rate per Unit (₹)</label><input name=\"rate_per_unit\" type=\"number\" step=\"0.01\" value=\"{{ t['rate_per_unit'] }}\"></div>
          <div class=\"group\"><label>Last Meter Reading</label><input name=\"last_reading\" type=\"number\" value=\"{{ t['last_reading'] }}\"></div>
          <input type=\"submit\" value=\"Save Changes\">
        </form>
        """,
        t=tenant,
    )
    return render_template_string(BASE_HTML, title="Edit Tenant | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/reading/new", methods=["GET","POST"])
def new_reading():
    db = get_db()
    tenants = db.execute("SELECT * FROM tenants ORDER BY name").fetchall()
    if request.method == "POST":
        tenant_id = int(request.form.get("tenant_id"))
        end_reading = int(request.form.get("end_reading", 0) or 0)
        month = int(request.form.get("month", datetime.now().month))
        year = int(request.form.get("year", datetime.now().year))

        tenant = db.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        if not tenant:
            flash("Tenant not found")
            return redirect(url_for("new_reading"))
        start_reading = tenant["last_reading"]
        if end_reading < start_reading:
            flash("End reading cannot be less than last reading.")
            return redirect(url_for("new_reading"))
        units = end_reading - start_reading
        light_bill = round(units * float(tenant["rate_per_unit"]), 2)
        total = float(tenant["monthly_rent"]) + light_bill

        # create bill
        db.execute(
            "INSERT INTO bills (tenant_id, month, year, start_reading, end_reading, units, light_bill, total, paid, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                tenant_id,
                month,
                year,
                start_reading,
                end_reading,
                units,
                light_bill,
                total,
                0,
                datetime.now().isoformat(),
            ),
        )
        # update last_reading
        db.execute("UPDATE tenants SET last_reading=? WHERE id=?", (end_reading, tenant_id))
        db.commit()
        flash(f"Bill created: Units {units}, Light ₹{light_bill:.0f}, Total ₹{total:.0f}")
        return redirect(url_for("bills_list"))

    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button ghost\" href=\"{{ url_for('dashboard') }}\">← Back</a>
        </div>
        <h2>New Meter Reading / Create Bill</h2>
        <form method=\"post\">
          <div class=\"group\">
            <label>Tenant</label>
            <select name=\"tenant_id\" required>
              {% for t in tenants %}
                <option value=\"{{ t['id'] }}\">{{ t['name'] }} (Room {{ t['room'] or '-' }}) — Last {{ t['last_reading'] }} / Rate ₹{{ '%.2f' % t['rate_per_unit'] }}</option>
              {% endfor %}
            </select>
          </div>
          <div class=\"row\">
            <div class=\"group\" style=\"flex:1\">
              <label>Month</label>
              <input type=\"number\" name=\"month\" min=\"1\" max=\"12\" value=\"{{ now.month }}\">
            </div>
            <div class=\"group\" style=\"flex:1\">
              <label>Year</label>
              <input type=\"number\" name=\"year\" value=\"{{ now.year }}\">
            </div>
          </div>
          <div class=\"group\"><label>Current Meter Reading (end)</label><input type=\"number\" name=\"end_reading\" required></div>
          <input type=\"submit\" value=\"Create Bill\">
        </form>
        """,
        tenants=tenants,
        now=datetime.now(),
    )
    return render_template_string(BASE_HTML, title="New Reading | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/bills")
def bills_list():
    db = get_db()
    month = request.args.get("month")
    year = request.args.get("year")
    params = []
    where = []
    if month:
        where.append("month=?")
        params.append(int(month))
    if year:
        where.append("year=?")
        params.append(int(year))
    where_sql = (" WHERE "+" AND ".join(where)) if where else ""
    bills = db.execute(
        f"SELECT b.*, t.name, t.room FROM bills b JOIN tenants t ON t.id=b.tenant_id {where_sql} ORDER BY year DESC, month DESC, id DESC",
        params,
    ).fetchall()

    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button ghost\" href=\"{{ url_for('dashboard') }}\">🏠 Dashboard</a>
          <a class=\"button ghost\" href=\"{{ url_for('new_reading') }}\">⚡ New Reading</a>
          <a class=\"button\" href=\"{{ url_for('reports', month=request.args.get('month'), year=request.args.get('year')) }}\">📊 Reports</a>
        </div>
        <h2>Bills</h2>
        <form method=\"get\" class=\"row\">
          <input name=\"month\" placeholder=\"Month (1-12)\" value=\"{{ request.args.get('month','') }}\" style=\"max-width:160px\">
          <input name=\"year\" placeholder=\"Year (e.g. 2025)\" value=\"{{ request.args.get('year','') }}\" style=\"max-width:160px\">
          <input type=\"submit\" value=\"Filter\">
          <a class=\"button ghost\" href=\"{{ url_for('bills_list') }}\">Clear</a>
        </form>
        <table>
          <tr>
            <th>Tenant</th><th>Room</th><th>Month</th><th>Start</th><th>End</th><th>Units</th><th>Light Bill (₹)</th><th>Total (₹)</th><th>Status</th><th>Actions</th>
          </tr>
          {% for b in bills %}
          <tr>
            <td>{{ b['name'] }}</td>
            <td>{{ b['room'] or '-' }}</td>
            <td>{{ '%02d/%d' % (b['month'], b['year']) }}</td>
            <td>{{ b['start_reading'] }}</td>
            <td>{{ b['end_reading'] }}</td>
            <td>{{ b['units'] }}</td>
            <td>{{ '%.0f' % b['light_bill'] }}</td>
            <td><strong>{{ '%.0f' % b['total'] }}</strong></td>
            <td class=\"{{ 'ok' if b['paid'] else 'bad' }}\">{{ 'Paid' if b['paid'] else 'Unpaid' }}</td>
            <td>
              {% if not b['paid'] %}
              <form style=\"display:inline\" method=\"post\" action=\"{{ url_for('mark_paid', bill_id=b['id']) }}\">
                <input type=\"submit\" value=\"Mark Paid\">
              </form>
              {% endif %}
              <a class=\"button ghost\" href=\"{{ url_for('download_receipt', bill_id=b['id']) }}\">Receipt</a>
            </td>
          </tr>
          {% endfor %}
        </table>
        """,
        bills=bills,
    )
    return render_template_string(BASE_HTML, title="Bills | "+APP_TITLE, app_title=APP_TITLE, body=body)

# --------------------- Reports ---------------------

@app.route("/reports")
def reports():
    db = get_db()
    month = request.args.get("month")
    year = request.args.get("year")

    filters = []
    params = []
    scope_label = "All Time"
    if year and year.isdigit():
        filters.append("year=?")
        params.append(int(year))
        scope_label = f"Year {year}"
    if month and month.isdigit():
        filters.append("month=?")
        params.append(int(month))
        scope_label = f"{int(month):02d}/{year}" if year else f"Month {month}"

    where_sql = (" WHERE "+" AND ".join(filters)) if filters else ""

    rows = db.execute(
        f"SELECT b.*, t.name, t.room FROM bills b JOIN tenants t ON t.id=b.tenant_id {where_sql} ORDER BY year DESC, month DESC, id DESC",
        params,
    ).fetchall()

    # Aggregates
    # NOTE: For Postgres rows are dicts; for SQLite rows behave like dict via Row mapping
    total_units = sum(r["units"] for r in rows) if rows else 0
    total_light = sum(float(r["light_bill"]) for r in rows) if rows else 0.0
    # monthly_rent per bill's tenant at the time of bill — approximated from current tenant rent
    total_rent = 0.0
    for r in rows:
        rent_row = db.execute("SELECT monthly_rent FROM tenants WHERE id=?", (r["tenant_id"],)).fetchone()
        total_rent += float(rent_row["monthly_rent"]) if rent_row else 0.0
    grand_total = sum(float(r["total"]) for r in rows) if rows else 0.0
    received = sum(float(r["total"]) for r in rows if r["paid"]) if rows else 0.0
    outstanding = grand_total - received

    body = render_template_string(
        """
        <div class=\"toolbar\">
          <a class=\"button ghost\" href=\"{{ url_for('dashboard') }}\">🏠 Dashboard</a>
          <a class=\"button ghost\" href=\"{{ url_for('bills_list', month=request.args.get('month'), year=request.args.get('year')) }}\">🧾 Bills</a>
          <a class=\"button\" href=\"{{ url_for('export_csv', month=request.args.get('month'), year=request.args.get('year')) }}\"⬇️ Export CSV</a>
        </div>
        <h2>Reports — {{ scope }}</h2>
        <form method=\"get\" class=\"row\">
          <input name=\"month\" placeholder=\"Month (1-12)\" value=\"{{ request.args.get('month','') }}\" style=\"max-width:160px\">
          <input name=\"year\" placeholder=\"Year (e.g. 2025)\" value=\"{{ request.args.get('year','') }}\" style=\"max-width:160px\">
          <input type=\"submit\" value=\"Filter\">
          <a class=\"button ghost\" href=\"{{ url_for('reports') }}\">Clear</a>
        </form>

        <div class=\"row\" style=\"margin-top:12px\">
          <div class=\"card\"><div><strong>Total Units</strong></div><div style=\"font-size:24px\">{{ total_units }}</div></div>
          <div class=\"card\"><div><strong>Light Bill</strong></div><div style=\"font-size:24px\">₹{{ '%.0f' % total_light }}</div></div>
          <div class=\"card\"><div><strong>Rent</strong></div><div style=\"font-size:24px\">₹{{ '%.0f' % total_rent }}</div></div>
          <div class=\"card\"><div><strong>Grand Total</strong></div><div style=\"font-size:24px\">₹{{ '%.0f' % grand_total }}</div></div>
          <div class=\"card\"><div><strong>Received</strong></div><div style=\"font-size:24px\" class=\"ok\">₹{{ '%.0f' % received }}</div></div>
          <div class=\"card\"><div><strong>Outstanding</strong></div><div style=\"font-size:24px\" class=\"bad\">₹{{ '%.0f' % outstanding }}</div></div>
        </div>

        <h3>Bill Details</h3>
        <table>
          <tr>
            <th>Tenant</th><th>Room</th><th>Month</th><th>Units</th><th>Light (₹)</th><th>Total (₹)</th><th>Status</th>
          </tr>
          {% for r in rows %}
          <tr>
            <td>{{ r['name'] }}</td>
            <td>{{ r['room'] or '-' }}</td>
            <td>{{ '%02d/%d' % (r['month'], r['year']) }}</td>
            <td>{{ r['units'] }}</td>
            <td>{{ '%.0f' % r['light_bill'] }}</td>
            <td><strong>{{ '%.0f' % r['total'] }}</strong></td>
            <td class=\"{{ 'ok' if r['paid'] else 'bad' }}\">{{ 'Paid' if r['paid'] else 'Unpaid' }}</td>
          </tr>
          {% endfor %}
        </table>
        """,
        rows=rows,
        scope=scope_label,
        total_units=total_units,
        total_light=total_light,
        total_rent=total_rent,
        grand_total=grand_total,
        received=received,
        outstanding=outstanding,
    )
    return render_template_string(BASE_HTML, title="Reports | "+APP_TITLE, app_title=APP_TITLE, body=body)

@app.route("/reports/export")
def export_csv():
    db = get_db()
    month = request.args.get("month")
    year = request.args.get("year")

    filters = []
    params = []
    fname = "reports_all_time.csv"
    if year and year.isdigit():
        filters.append("year=?")
        params.append(int(year))
        fname = f"reports_{year}.csv"
    if month and month.isdigit():
        filters.append("month=?")
        params.append(int(month))
        if year and year.isdigit():
            fname = f"reports_{int(month):02d}-{year}.csv"
        else:
            fname = f"reports_month_{month}.csv"

    where_sql = (" WHERE "+" AND ".join(filters)) if filters else ""

    rows = db.execute(
        f"SELECT b.id, t.name, t.room, b.month, b.year, b.start_reading, b.end_reading, b.units, b.light_bill, b.total, b.paid FROM bills b JOIN tenants t ON t.id=b.tenant_id {where_sql} ORDER BY year DESC, month DESC, b.id DESC",
        params,
    ).fetchall()

    # Build CSV (Excel-compatible)
    output_lines = [
        ["Bill ID","Tenant","Room","Month","Year","Start","End","Units","Light Bill (₹)","Total (₹)","Paid"],
    ]
    for r in rows:
        output_lines.append([
            r["id"], r["name"], r["room"], r["month"], r["year"], r["start_reading"], r["end_reading"], r["units"], int(round(float(r["light_bill"]))), int(round(float(r["total"]))), "Yes" if r["paid"] else "No"
        ])

    # write manually to avoid extra dependency
    csv_text = "\r\n".join(",".join(map(lambda x: f'"{str(x).replace("\"","\"\"")}"', row)) for row in output_lines)
    return Response(csv_text, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={fname}"})

@app.route("/bill/<int:bill_id>/paid", methods=["POST"])  # FIXED: removed stray "), methods=[\"POST\"]"
def mark_paid(bill_id):
    db = get_db()
    db.execute("UPDATE bills SET paid=1 WHERE id=?", (bill_id,))
    db.commit()
    flash("Marked as paid.")
    return redirect(request.referrer or url_for("bills_list"))

@app.route("/bill/<int:bill_id>/receipt")
def download_receipt(bill_id):
    db = get_db()
    b = db.execute(
        "SELECT b.*, t.name, t.room, t.monthly_rent, t.rate_per_unit FROM bills b JOIN tenants t ON t.id=b.tenant_id WHERE b.id=?",
        (bill_id,),
    ).fetchone()
    if not b:
        flash("Bill not found")
        return redirect(url_for("bills_list"))
    html = render_template_string(
        """
        <div style=\"max-width:680px; margin:24px auto; font-family:Arial\">
          <h2>Rent & Electricity Receipt</h2>
          <p>Date: {{ now }}</p>
          <hr>
          <p><strong>Tenant:</strong> {{ b['name'] }} &nbsp; <strong>Room:</strong> {{ b['room'] or '-' }}</p>
          <p><strong>Month:</strong> {{ '%02d/%d' % (b['month'], b['year']) }}</p>
          <table style=\"width:100%; border-collapse:collapse\" border=\"1\" cellpadding=\"8\">
            <tr><th align=\"left\">Description</th><th align=\"right\">Amount (₹)</th></tr>
            <tr><td>Rent</td><td align=\"right\">{{ '%.0f' % b['monthly_rent'] }}</td></tr>
            <tr><td>Electricity ({{ b['units'] }} units @ ₹{{ '%.2f' % b['rate_per_unit'] }}/unit)</td><td align=\"right\">{{ '%.0f' % b['light_bill'] }}</td></tr>
            <tr><td><strong>Total</strong></td><td align=\"right\"><strong>{{ '%.0f' % b['total'] }}</strong></td></tr>
          </table>
          <p>Meter: {{ b['start_reading'] }} → {{ b['end_reading'] }}</p>
          <p>Status: {{ 'Paid' if b['paid'] else 'Unpaid' }}</p>
          <p style=\"margin-top:24px\">— Generated by {{ app_title }}</p>
        </div>
        """,
        b=b,
        now=datetime.now().strftime("%d-%m-%Y %H:%M"),
        app_title=APP_TITLE,
    )
    return html

if __name__ == "__main__":
    # Local dev server
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=not using_postgres())
