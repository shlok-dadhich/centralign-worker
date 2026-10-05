"""Finance system on :3002. Admin toggles live in SQLite so eval can POST them."""
import sqlite3
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from mockapps.paths import FINANCE_DB, ROOT
from mockapps.seed import format_inr, seed_finance

seed_finance(reset=False)

app = FastAPI(title="Finance System")
app.add_middleware(SessionMiddleware, secret_key="finance-dev-secret")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.globals["format_inr"] = format_inr

FIN_USER = "fin@demo.test"
FIN_PASS = "demo123"


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(FINANCE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else "0"


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def logged_in(request: Request) -> bool:
    return request.session.get("user") == FIN_USER


def _truthy(v: Optional[str]) -> bool:
    return v in ("1", "true", "on", "yes")


@app.get("/")
def home(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/records", status_code=303)


@app.get("/login")
def login_form(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request, 
        "fin_login.html", {"request": request, "error": error}
    )


@app.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    if email == FIN_USER and password == FIN_PASS:
        request.session["user"] = email
        return RedirectResponse("/records", status_code=303)
    return templates.TemplateResponse(
        request, 
        "fin_login.html",
        {"request": request, "error": "Invalid email or password."},
        status_code=401,
    )


@app.get("/records")
def record_list(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    rows = conn.execute("SELECT * FROM records").fetchall()
    conn.close()
    return templates.TemplateResponse(
        request, 
        "records.html", {"request": request, "rows": rows}
    )


@app.get("/records/new")
def record_new(request: Request, error: Optional[str] = None):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    rename = setting(conn, "rename_button") == "1"
    conn.close()
    return templates.TemplateResponse(
        request, 
        "record_new.html",
        {
            "request": request,
            "error": error,
            "submit_label": "Save Record" if rename else "Submit Invoice",
        },
    )


@app.post("/records")
def record_create(
    request: Request,
    vendor: str = Form(""),
    invoice_number: str = Form(""),
    amount: str = Form(""),
    due_date: str = Form(""),
):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    rename = setting(conn, "rename_button") == "1"
    submit_label = "Save Record" if rename else "Submit Invoice"
    if setting(conn, "fail_first_save") == "1" and setting(conn, "fail_first_save_used") != "1":
        set_setting(conn, "fail_first_save_used", "1")
        conn.commit()
        conn.close()
        return templates.TemplateResponse(
            request, 
            "record_new.html",
            {
                "request": request,
                "error": "Temporary server error (500)",
                "submit_label": submit_label,
                "vendor": vendor,
                "invoice_number": invoice_number,
                "amount": amount,
                "due_date": due_date,
            },
        )
    raw = amount.replace("₹", "").replace(",", "").strip()
    try:
        amt = int(raw)
    except ValueError:
        conn.close()
        return templates.TemplateResponse(
            request, 
            "record_new.html",
            {
                "request": request,
                "error": "Invalid data: amount must be an integer.",
                "submit_label": submit_label,
                "vendor": vendor,
                "invoice_number": invoice_number,
                "amount": amount,
                "due_date": due_date,
            },
        )
    cur = conn.execute(
        "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
        (vendor.strip(), invoice_number.strip(), amt, due_date.strip(), "UNPAID"),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return RedirectResponse(f"/records/{new_id}", status_code=303)


@app.get("/records/{record_id}")
def record_detail(request: Request, record_id: int):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    conn.close()
    if not row:
        return templates.TemplateResponse(
            request, 
            "records.html",
            {"request": request, "rows": [], "error": "Record not found."},
        )
    return templates.TemplateResponse(
        request, 
        "record_detail.html", {"request": request, "row": row}
    )


@app.get("/records/{record_id}/edit")
def record_edit_form(request: Request, record_id: int, error: Optional[str] = None):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    conn.close()
    if not row:
        return RedirectResponse("/records", status_code=303)
    return templates.TemplateResponse(
        request, 
        "record_edit.html", {"request": request, "row": row, "error": error}
    )


@app.post("/records/{record_id}/edit")
def record_edit(
    request: Request,
    record_id: int,
    vendor: str = Form(""),
    invoice_number: str = Form(""),
    amount: str = Form(""),
    due_date: str = Form(""),
):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    raw = amount.replace("\u20b9", "").replace(",", "").strip()
    try:
        amt = int(raw)
    except ValueError:
        conn.close()
        return templates.TemplateResponse(
            request, 
            "record_edit.html",
            {"request": request, "row": row, "error": "Invalid data: amount must be an integer."},
            status_code=400,
        )
    conn.execute(
        "UPDATE records SET vendor=?, invoice_number=?, amount=?, due_date=? WHERE id=?",
        (vendor.strip(), invoice_number.strip(), amt, due_date.strip(), record_id),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/records/{record_id}", status_code=303)


@app.get("/records/{record_id}/pay")
def pay_form(request: Request, record_id: int):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    conn.close()
    if not row:
        return RedirectResponse("/records", status_code=303)
    return templates.TemplateResponse(
        request, 
        "record_pay.html", {"request": request, "row": row}
    )


@app.post("/records/{record_id}/pay")
def pay_confirm(request: Request, record_id: int):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    conn.execute("UPDATE records SET status='PAID' WHERE id=?", (record_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/records/{record_id}", status_code=303)


@app.get("/admin")
def admin_page(request: Request):
    conn = db()
    flags = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    conn.close()
    return templates.TemplateResponse(
        request, 
        "admin.html", {"request": request, "flags": flags}
    )


@app.post("/admin/set")
async def admin_set(request: Request):
    """Eval uses query string; the HTML form posts fields. Both are accepted."""
    conn = db()
    q = request.query_params
    form = await request.form()
    for key in ("fail_first_save", "rename_button"):
        if key in q:
            val = "1" if _truthy(q.get(key)) else "0"
        elif key in form:
            val = "1" if _truthy(str(form.get(key))) else "0"
        else:
            continue
        set_setting(conn, key, val)
        if key == "fail_first_save":
            set_setting(conn, "fail_first_save_used", "0")
    conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/reset")
def admin_reset():
    seed_finance(reset=True)
    return RedirectResponse("/admin", status_code=303)
