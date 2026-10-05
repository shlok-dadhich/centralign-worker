"""Invoice portal on :3001. Server-rendered HTML, SQLite, no JS."""
import sqlite3
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from mockapps.paths import PORTAL_DB, ROOT
from mockapps.seed import format_inr, seed_portal

seed_portal(reset=False)

app = FastAPI(title="Invoice Portal")
app.add_middleware(SessionMiddleware, secret_key="portal-dev-secret")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.globals["format_inr"] = format_inr

PORTAL_USER = "ap@demo.test"
PORTAL_PASS = "demo123"


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(PORTAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def logged_in(request: Request) -> bool:
    return request.session.get("user") == PORTAL_USER


@app.get("/")
def home(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/invoices", status_code=303)


@app.get("/login")
def login_form(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request, 
        "portal_login.html", {"request": request, "error": error}
    )


@app.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    if email == PORTAL_USER and password == PORTAL_PASS:
        request.session["user"] = email
        return RedirectResponse("/invoices", status_code=303)
    return templates.TemplateResponse(
        request, 
        "portal_login.html",
        {"request": request, "error": "Invalid email or password."},
        status_code=401,
    )


@app.get("/invoices")
def invoice_list(request: Request, q: str = ""):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    if q.strip():
        like = f"%{q.strip()}%"
        rows = conn.execute(
            "SELECT * FROM invoices WHERE vendor LIKE ? OR number LIKE ?",
            (like, like),
        ).fetchall()
    else:
        # No ORDER BY — insertion/shuffle order, not date.
        rows = conn.execute("SELECT * FROM invoices").fetchall()
    conn.close()
    return templates.TemplateResponse(
        request, 
        "invoices.html", {"request": request, "rows": rows, "q": q}
    )


@app.get("/invoices/{invoice_id}")
def invoice_detail(request: Request, invoice_id: int):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    conn.close()
    if not row:
        return templates.TemplateResponse(
            request, 
            "invoices.html",
            {"request": request, "rows": [], "q": "", "error": "Invoice not found."},
        )
    return templates.TemplateResponse(
        request, 
        "invoice_detail.html", {"request": request, "row": row}
    )
