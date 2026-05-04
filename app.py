
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
import sqlite3
from datetime import datetime, date, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

DB_FILE = os.environ.get("DB_FILE", "pos.db")

LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "5"))
NEAR_EXPIRY_DAYS = int(os.environ.get("NEAR_EXPIRY_DAYS", "5"))


# ---------------- DB ----------------

def generate_sku_code(sku, barcode):
    if sku and str(sku).strip():
        return str(sku).strip()
    return f"SKU-{barcode}"

def dict_factory(cursor, row):
    return {cursor.description[idx][0]: row[idx] for idx in range(len(row))}

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = dict_factory
    return conn

def dict_cursor(conn):
    return conn.cursor()


# ---------------- HELPERS ----------------
def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default

def verify_password(stored_password, provided_password):
    if not stored_password:
        return False
    if stored_password.startswith(("pbkdf2:", "scrypt:", "argon2:")):
        return check_password_hash(stored_password, provided_password)
    return stored_password == provided_password

def parse_date(value):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

def login_required_page(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper

def login_required_api(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        return view(*args, **kwargs)
    return wrapper

def admin_required_api(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"success": False, "message": "Forbidden"}), 403
        return view(*args, **kwargs)
    return wrapper

def get_permissions(role: str):
    is_admin = role == "admin"
    return {
        "can_download_reports": is_admin,
        "can_edit_inventory": is_admin,
        "can_change_prices": is_admin,
        "can_bulk_upload": is_admin,
        "can_manage_users": is_admin,
    }

def local_today():
    return datetime.now().strftime("%Y-%m-%d")

def local_month():
    return datetime.now().strftime("%Y-%m")

def csv_response(filename, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def ensure_column(conn, table, column_name, column_definition):
    cur = dict_cursor(conn)
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row["name"] for row in cur.fetchall()]
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")

def money(v):
    return float(v or 0)

def fmt_date(value):
    if not value:
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return str(value)

def fmt_datetime(value):
    if not value:
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat(sep=" ", timespec="seconds")
        except TypeError:
            return value.isoformat()
    return str(value)

def is_near_expiry(expiry_date):
    if not expiry_date:
        return False
    if isinstance(expiry_date, str):
        expiry_date = parse_date(expiry_date)
    if not expiry_date:
        return False
    today = date.today()
    return 0 <= (expiry_date - today).days <= NEAR_EXPIRY_DAYS

def expiry_status(expiry_date):
    if not expiry_date:
        return "no_expiry"
    if isinstance(expiry_date, str):
        expiry_date = parse_date(expiry_date)
    if not expiry_date:
        return "no_expiry"
    today = date.today()
    if expiry_date < today:
        return "expired"
    if (expiry_date - today).days <= NEAR_EXPIRY_DAYS:
        return "near_expiry"
    return "ok"

def product_to_dict(row, customer_type="retail"):
    retail_price = float(row["price"])
    wholesale_price = float(row.get("wholesale_price") or 0)
    discount_price = float(row.get("discount_price") or 0)

    if customer_type == "wholesale":
        sale_price = wholesale_price if wholesale_price > 0 else retail_price
    else:
        sale_price = discount_price if discount_price > 0 else retail_price

    exp = row.get("expiry_date")
    exp_str = fmt_date(exp)

    active = int(row.get("active") if row.get("active") is not None else 1)

    return {
        "id": row["id"],
        "sku": row.get("sku"),
        "barcode": row["barcode"],
        "name": row["name"],
        "brand": row.get("brand"),
        "category": row.get("category"),
        "batch_no": row.get("batch_no"),
        "expiry_date": exp_str,
        "expiry_status": expiry_status(exp),
        "uom": row.get("uom") or "pcs",
        "price": retail_price,
        "wholesale_price": wholesale_price,
        "discount_price": discount_price,
        "sale_price": sale_price,
        "stock": int(row["stock"] or 0),
        "package_qty": int(row.get("package_qty") or 0),
        "active": active,
        "status": "active" if active else "inactive",
    }

def customer_to_dict(row):
    return {
        "id": row["id"],
        "customer_code": row.get("customer_code"),  # ✅ ADD THIS
        "name": row["name"],
        "customer_type": row["customer_type"],
        "phone": row.get("phone"),
        "balance": float(row.get("balance") or 0),
        "created_at": row.get("created_at"),
    }

def sale_to_dict(row):
    total = float(row["total"])
    amount_paid = float(row.get("amount_paid") or 0)
    balance = float(row.get("balance") or 0)
    change_amount = max(amount_paid - total, 0) if balance == 0 else 0.0
    due_amount = max(total - amount_paid, 0) if balance > 0 else 0.0

    return {
        "bill_no": row["bill_no"],
        "date": fmt_datetime(row["date"]),
        "total": total,
        "customer_type": row.get("customer_type") or "retail",
        "customer_name": row.get("customer_name"),
        "amount_paid": amount_paid,
        "customer_paid": amount_paid,
        "balance": balance,
        "change_amount": change_amount,
        "return_amount": change_amount,
        "due_amount": due_amount,
        "due_date": fmt_date(row.get("due_date")),
        "payment_status": row.get("payment_status") or "paid",
    }

def sale_price_for_product(product_row, customer_type):
    retail = float(product_row["price"])
    wholesale = float(product_row.get("wholesale_price") or 0)
    discount = float(product_row.get("discount_price") or 0)

    if customer_type == "wholesale":
        return (wholesale if wholesale > 0 else retail), "wholesale"
    if discount > 0:
        return discount, "discount"
    return retail, "retail"


def default_customer_name(customer_type):
    customer_type = (customer_type or "retail").strip().lower()
    if customer_type == "wholesale":
        return "Wholesale Customer"
    if customer_type == "credit":
        return "Credit Customer"
    return "Walk-in Customer"

def chart_day_label(ts):
    try:
        return ts.strftime("%H:00")
    except Exception:
        return str(ts)

def filter_sales_sql(customer_type=None, payment_status=None, bill_no=None, start_date=None, end_date=None, month=None, day=None):
    sql = "SELECT s.* FROM sales s WHERE 1=1"
    params = []
    if day:
        sql += " AND DATE(s.date) = ?"
        params.append(day)
    if month:
        sql += " AND strftime('%Y-%m', s.date) = ?"
        params.append(month)
    if start_date and end_date:
        sql += " AND DATE(s.date) BETWEEN ? AND ?"
        params.extend([start_date, end_date])
    if customer_type:
        sql += " AND s.customer_type = ?"
        params.append(customer_type)
    if payment_status:
        sql += " AND s.payment_status = ?"
        params.append(payment_status)
    if bill_no:
        sql += " AND s.bill_no LIKE ?"
        params.append(f"%{bill_no}%")
    sql += " ORDER BY s.date DESC"
    return sql, params

def download_name(prefix, suffix):
    return f"{prefix}_{suffix}.csv"

# ---------------- INIT / MIGRATION ----------------

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        customer_type TEXT NOT NULL DEFAULT 'retail',
        phone TEXT,
        balance REAL NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 👇 ADD THIS LINE
    ensure_column(conn, "customers", "customer_code", "customer_code TEXT")



    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT UNIQUE,
        barcode TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        brand TEXT,
        category TEXT,
        batch_no TEXT,
        expiry_date TEXT,
        uom TEXT DEFAULT 'pcs',
        price REAL NOT NULL DEFAULT 0,
        wholesale_price REAL NOT NULL DEFAULT 0,
        discount_price REAL NOT NULL DEFAULT 0,
        stock INTEGER NOT NULL DEFAULT 0,
        package_qty INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT UNIQUE NOT NULL,
        customer_id INTEGER,
        customer_name TEXT,
        customer_type TEXT NOT NULL DEFAULT 'retail',
        total REAL NOT NULL,
        amount_paid REAL NOT NULL DEFAULT 0,
        balance REAL NOT NULL DEFAULT 0,
        due_date TEXT,
        payment_status TEXT NOT NULL DEFAULT 'paid',
        date TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT NOT NULL,
        product_name TEXT NOT NULL,
        barcode TEXT NOT NULL,
        brand TEXT,
        category TEXT,
        batch_no TEXT,
        expiry_date TEXT,
        qty INTEGER NOT NULL,
        price REAL NOT NULL,
        line_total REAL NOT NULL,
        price_type TEXT NOT NULL DEFAULT 'retail'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS credit_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        bill_no TEXT,
        entry_type TEXT NOT NULL,
        amount REAL NOT NULL,
        note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # safe migrations
    ensure_column(conn, "users", "role", "role TEXT NOT NULL DEFAULT 'user'")
    ensure_column(conn, "users", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "customers", "phone", "phone TEXT")
    ensure_column(conn, "customers", "balance", "balance REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "customers", "created_at", "created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "products", "sku", "sku TEXT UNIQUE")
    ensure_column(conn, "products", "brand", "brand TEXT")
    ensure_column(conn, "products", "category", "category TEXT")
    ensure_column(conn, "products", "batch_no", "batch_no TEXT")
    ensure_column(conn, "products", "expiry_date", "expiry_date TEXT")
    ensure_column(conn, "products", "uom", "uom TEXT DEFAULT 'pcs'")
    ensure_column(conn, "products", "wholesale_price", "wholesale_price REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "products", "discount_price", "discount_price REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "products", "package_qty", "package_qty INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "products", "active", "active INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "products", "updated_at", "updated_at TEXT DEFAULT CURRENT_TIMESTAMP")

    ensure_column(conn, "sales", "customer_id", "customer_id INTEGER")
    ensure_column(conn, "sales", "customer_name", "customer_name TEXT")
    ensure_column(conn, "sales", "customer_type", "customer_type TEXT NOT NULL DEFAULT 'retail'")
    ensure_column(conn, "sales", "amount_paid", "amount_paid REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "sales", "balance", "balance REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "sales", "due_date", "due_date TEXT")
    ensure_column(conn, "sales", "payment_status", "payment_status TEXT NOT NULL DEFAULT 'paid'")

    ensure_column(conn, "sale_items", "brand", "brand TEXT")
    ensure_column(conn, "sale_items", "category", "category TEXT")
    ensure_column(conn, "sale_items", "batch_no", "batch_no TEXT")
    ensure_column(conn, "sale_items", "expiry_date", "expiry_date TEXT")
    ensure_column(conn, "sale_items", "price_type", "price_type TEXT NOT NULL DEFAULT 'retail'")

    cur.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("1234"), "admin")
        )

    cur.execute("SELECT COUNT(*) AS c FROM customers")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO customers (name, customer_type, phone, balance) VALUES (?, ?, ?, ?)",
            [
                ("Walk-in Customer", "retail", None, 0),
                ("Wholesale Customer", "wholesale", None, 0),
                ("Credit Customer", "credit", None, 0),
            ]
        )

    cur.execute("SELECT COUNT(*) AS c FROM products")
    if cur.fetchone()["c"] == 0:
        demo_products = []
        cur.executemany(
            """
            INSERT INTO products
            (sku, barcode, name, brand, category, batch_no, expiry_date, uom, price, wholesale_price, discount_price, stock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            demo_products
        )

    conn.commit()
    conn.close()


# ---------------- PAGES ----------------
@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cur.fetchone()
        conn.close()

        if user and verify_password(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user.get("role") or "user"
            return redirect(url_for("dashboard"))

        error = "Invalid username or password."

    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required_page
def dashboard():
    return render_template(
        "index.html",
        username=session.get("username", "Admin"),
        role=session.get("role", "user"),
        permissions=get_permissions(session.get("role", "user"))
    )

# ---------------- API ME ----------------
@app.route("/api/me")
@login_required_api
def api_me():
    role = session.get("role", "user")
    return jsonify({
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "role": role,
        "permissions": get_permissions(role),
    })

# ---------------- CUSTOMERS ----------------
@app.route("/api/customers", methods=["GET", "POST"])
@login_required_api
def api_customers():

    conn = get_db()
    cur = dict_cursor(conn)

    # ✅ GET (search + filter)
    if request.method == "GET":
        q = request.args.get("q", "").strip()
        ctype = request.args.get("type", "").strip()

        sql = "SELECT * FROM customers WHERE 1=1"
        params = []

        if q:
            sql += """
            AND (
                name LIKE ?
                OR customer_type LIKE ?
                OR phone LIKE ?
            )
            """
            like = f"%{q}%"
            params += [like, like, like]

        if ctype in ("retail", "wholesale", "credit"):
            sql += " AND customer_type=?"
            params.append(ctype)

        sql += " ORDER BY id DESC"

        cur.execute(sql, params)
        rows = [customer_to_dict(r) for r in cur.fetchall()]

        conn.close()
        return jsonify(rows)

    # ✅ POST (add customer)
    if session.get("role") != "admin":
        conn.close()
        return jsonify({"message": "Forbidden"}), 403

    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    customer_type = data.get("customer_type") or "retail"
    phone = (data.get("phone") or "").strip() or None

    if not name or customer_type not in ("retail", "wholesale", "credit"):
        conn.close()
        return jsonify({"message": "Invalid customer data"}), 400

    import uuid

    customer_code = "CUST-" + uuid.uuid4().hex[:6].upper()

    cur.execute(
        "INSERT INTO customers (customer_code, name, customer_type, phone) VALUES (?, ?, ?, ?)",
        (customer_code, name, customer_type, phone),
    )

    conn.commit()
    conn.close()

    return jsonify({"message": "Customer created"}), 201

# ---------------- PRODUCTS ----------------
@app.route("/api/products", methods=["GET"])
@login_required_api
def api_products():
    q = request.args.get("q", "").strip()
    customer_type = request.args.get("customer_type", "retail").strip()
    conn = get_db()
    cur = dict_cursor(conn)
    if q:
        cur.execute(
            """
            SELECT * FROM products
            WHERE name LIKE ? OR sku LIKE ? OR barcode LIKE ? OR batch_no LIKE ? OR brand LIKE ? OR category LIKE ?
            ORDER BY name
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        )
    else:
        cur.execute("SELECT * FROM products ORDER BY name")
    rows = [product_to_dict(r, customer_type=customer_type) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route("/api/product", methods=["POST"])
@login_required_api
def api_add_product():
    if session.get("role") != "admin":
        return jsonify({"message": "Forbidden"}), 403

    data = request.get_json(force=True)
    sku = (data.get("sku") or "").strip() or None
    name = (data.get("name") or "").strip()
    barcode = (data.get("barcode") or "").strip()
    brand = (data.get("brand") or "").strip() or None
    category = (data.get("category") or "").strip() or None
    batch_no = (data.get("batch_no") or "").strip() or None
    expiry_date = parse_date(data.get("expiry_date"))
    uom = (data.get("uom") or "pcs").strip() or "pcs"
    price = to_float(data.get("price"))
    wholesale_price = to_float(data.get("wholesale_price"))
    discount_price = to_float(data.get("discount_price"))
    stock = to_int(data.get("stock"))
    package_qty = to_int(data.get("package_qty"))
    active = 1 if str(data.get("active", 1)).lower() not in ("0", "false", "off", "no", "") else 0

    if not name or not barcode:
        return jsonify({"message": "Name and barcode are required"}), 400
    if price < 0 or wholesale_price < 0 or discount_price < 0 or stock < 0 or package_qty < 0:
        return jsonify({"message": "Prices, stock, and package_qty cannot be negative"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE barcode=?", (barcode,))
    existing = cur.fetchone()
    sku = generate_sku_code(sku, barcode)
    try:
        cur2 = conn.cursor()
        if existing:
            cur2.execute(
                """
                UPDATE products
                SET sku=?, name=?, brand=?, category=?, batch_no=?, expiry_date=?, uom=?, price=?,
                    wholesale_price=?, discount_price=?, stock=?, package_qty=?, active=?, updated_at=CURRENT_TIMESTAMP
                WHERE barcode=?
                """,
                (sku, name, brand, category, batch_no, expiry_date, uom, price,
                 wholesale_price, discount_price, stock, package_qty, active, barcode)
            )
        else:
            cur2.execute(
                """
                INSERT INTO products
                (sku, barcode, name, brand, category, batch_no, expiry_date, uom, price, wholesale_price, discount_price, stock, package_qty, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sku, barcode, name, brand, category, batch_no, expiry_date, uom, price, wholesale_price, discount_price, stock, package_qty, active),
            )
        conn.commit()
        return jsonify({"message": "Product saved successfully"})
    except Exception as e:
        conn.rollback()
        return jsonify({"message": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/product/<barcode>", methods=["GET", "PUT", "DELETE"])
@login_required_api
def api_product_by_barcode(barcode):
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("SELECT * FROM products WHERE barcode=?", (barcode,))
    row = cur.fetchone()

    if request.method == "GET":
        conn.close()
        if not row or int(row.get("active") or 1) != 1:
            return jsonify({"message": "Not found"}), 404
        return jsonify(product_to_dict(row, customer_type=request.args.get("customer_type", "retail")))

    if session.get("role") != "admin":
        conn.close()
        return jsonify({"message": "Forbidden"}), 403

    if not row:
        conn.close()
        return jsonify({"message": "Not found"}), 404

    if request.method == "DELETE":
        cur2 = conn.cursor()
        cur2.execute("DELETE FROM products WHERE barcode=?", (barcode,))
        conn.commit()
        conn.close()
        return jsonify({"message": "Deleted"})

    data = request.get_json(force=True)
    sku = (data.get("sku") or row.get("sku") or "").strip() or None
    name = (data.get("name") or row["name"]).strip()
    brand = (data.get("brand") or row.get("brand") or "").strip() or None
    category = (data.get("category") or row.get("category") or "").strip() or None
    batch_no = (data.get("batch_no") or row.get("batch_no") or "").strip() or None
    expiry_date = parse_date(data.get("expiry_date")) or row.get("expiry_date")
    uom = (data.get("uom") or row.get("uom") or "pcs").strip() or "pcs"
    price = to_float(data.get("price"), float(row["price"]))
    wholesale_price = to_float(data.get("wholesale_price"), float(row.get("wholesale_price") or 0))
    discount_price = to_float(data.get("discount_price"), float(row.get("discount_price") or 0))
    stock = to_int(data.get("stock"), int(row["stock"]))
    package_qty = to_int(data.get("package_qty"), int(row.get("package_qty") or 0))
    active = 1 if str(data.get("active", row.get("active", 1))).lower() not in ("0", "false", "off", "no", "") else 0

    if price < 0 or wholesale_price < 0 or discount_price < 0 or stock < 0 or package_qty < 0:
        conn.close()
        return jsonify({"message": "Prices, stock, and package_qty cannot be negative"}), 400

    cur.execute(
        """
        UPDATE products
        SET sku=?, name=?, brand=?, category=?, batch_no=?, expiry_date=?, uom=?, price=?,
            wholesale_price=?, discount_price=?, stock=?, package_qty=?, active=?, updated_at=CURRENT_TIMESTAMP
        WHERE barcode=?
        """,
        (sku, name, brand, category, batch_no, expiry_date, uom, price,
         wholesale_price, discount_price, stock, package_qty, active, barcode),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Updated"})

@app.route("/api/product/<barcode>/toggle", methods=["POST"])
@login_required_api
def api_toggle_product(barcode):
    if session.get("role") != "admin":
        return jsonify({"message": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    active = 1 if str(data.get("active", 1)).lower() not in ("0", "false", "off", "no", "") else 0

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE products SET active=?, updated_at=CURRENT_TIMESTAMP WHERE barcode=?", (active, barcode))
    conn.commit()
    conn.close()
    return jsonify({"message": "Product status updated", "active": active})

@app.route("/api/search_product")
@login_required_api
def api_search_product():
    q = request.args.get("query", "").strip()
    customer_type = request.args.get("customer_type", "retail").strip()
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute(
        """
        SELECT * FROM products
        WHERE active=1 AND (name LIKE ? OR sku LIKE ? OR barcode LIKE ? OR batch_no LIKE ? OR brand LIKE ? OR category LIKE ?)
        ORDER BY name
        """,
        (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
    )
    rows = [product_to_dict(r, customer_type=customer_type) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route("/api/get_product/<barcode>")
@login_required_api
def api_get_product(barcode):
    customer_type = request.args.get("customer_type", "retail").strip()
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("SELECT * FROM products WHERE barcode=? AND active=1", (barcode,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"message": "Product not found"}), 404
    return jsonify(product_to_dict(row, customer_type=customer_type))

# ---------------- BULK UPLOAD ----------------
@app.route("/api/upload_inventory", methods=["POST"])
@login_required_api
def api_upload_inventory():
    if session.get("role") != "admin":
        return jsonify({"message": "Forbidden"}), 403
    if "file" not in request.files:
        return jsonify({"message": "CSV file is required"}), 400

    file = request.files["file"]
    content = file.stream.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))

    required = {"barcode", "name", "price", "stock"}
    if not required.issubset(set([h.strip() for h in (reader.fieldnames or [])])):
        return jsonify({"message": "CSV columns must include barcode,name,price,stock"}), 400

    conn = get_db()
    cur = conn.cursor()
    count = 0
    try:
        for row in reader:
            barcode = (row.get("barcode") or "").strip()
            name = (row.get("name") or "").strip()
            if not barcode or not name:
                continue

            sku = (row.get("sku") or "").strip() or None
            brand = (row.get("brand") or "").strip() or None
            category = (row.get("category") or "").strip() or None
            batch_no = (row.get("batch_no") or "").strip() or None
            expiry_date = parse_date(row.get("expiry_date"))
            uom = (row.get("uom") or "pcs").strip() or "pcs"
            price = to_float(row.get("price"))
            wholesale_price = to_float(row.get("wholesale_price"))
            discount_price = to_float(row.get("discount_price"))
            stock = to_int(row.get("stock"))
            package_qty = to_int(row.get("package_qty"))

            cur.execute("SELECT id, sku FROM products WHERE barcode=?", (barcode,))
            exists = cur.fetchone()
            sku = generate_sku_code(sku or (exists["sku"] if exists else None), barcode)

            if exists:
                cur.execute(
                    """
                    UPDATE products
                    SET sku=?, name=?, brand=?, category=?, batch_no=?, expiry_date=?, uom=?, price=?,
                        wholesale_price=?, discount_price=?, stock=?
                    WHERE barcode=?
                    """,
                    (sku, name, brand, category, batch_no, expiry_date, uom, price, wholesale_price, discount_price, stock, barcode),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO products
    (sku, barcode, name, brand, category, batch_no, expiry_date, uom, price, wholesale_price, discount_price, stock, package_qty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sku, barcode, name, brand, category, batch_no, expiry_date, uom, price, wholesale_price, discount_price, stock),
                )
            count += 1

        conn.commit()
        return jsonify({"message": "Upload complete", "count": count})
    except Exception as e:
        conn.rollback()
        return jsonify({"message": str(e)}), 500
    finally:
        conn.close()

# ---------------- CHECKOUT ----------------
@app.route("/api/checkout", methods=["POST"])
@login_required_api
def api_checkout():
    data = request.get_json(force=True)
    items = data.get("items", [])
    customer_id = to_int(data.get("customer_id"), 0) or None
    customer_type = (data.get("customer_type") or "retail").strip().lower()
    amount_paid = to_float(data.get("amount_paid"), 0)

    if not items:
        return jsonify({"message": "Cart is empty"}), 400

    conn = get_db()
    cur = dict_cursor(conn)

    customer = None
    customer_name = default_customer_name(customer_type)

    try:
        conn.execute("BEGIN")

        if customer_id:
            cur.execute("SELECT * FROM customers WHERE id=?", (customer_id,))
            customer = cur.fetchone()
            if not customer:
                conn.close()
                raise ValueError("Customer not found")
            customer_type = (customer.get("customer_type") or "retail").strip().lower()
            customer_name = customer.get("name") or default_customer_name(customer_type)

        if customer_type not in ("retail", "wholesale", "credit"):
            raise ValueError("Invalid customer type")

        merged = {}
        for item in items:
            barcode = (item.get("barcode") or "").strip()
            qty = to_int(item.get("qty"), 0)
            if not barcode or qty <= 0:
                continue
            merged[barcode] = merged.get(barcode, 0) + qty

        if not merged:
            raise ValueError("No valid items in cart")

        products = {}
        for barcode, qty in merged.items():
            cur.execute("SELECT * FROM products WHERE barcode=?", (barcode,))
            product = cur.fetchone()
            if not product:
                raise ValueError(f"Product not found: {barcode}")
            if qty > int(product["stock"]):
                raise ValueError(
                    f"Insufficient stock for {product['name']} ({barcode}). Available: {product['stock']}, Requested: {qty}"
                )
            products[barcode] = product

        bill_no = f"BILL-{int(datetime.now().timestamp())}"
        total = 0.0
        sale_rows = []

        for barcode, qty in merged.items():
            p = products[barcode]
            unit_price, price_type = sale_price_for_product(p, customer_type)
            line_total = round(unit_price * qty, 2)
            total += line_total
            sale_rows.append({
                "barcode": barcode,
                "name": p["name"],
                "brand": p.get("brand"),
                "category": p.get("category"),
                "batch_no": p.get("batch_no"),
                "expiry_date": p.get("expiry_date"),
                "qty": qty,
                "price": unit_price,
                "line_total": line_total,
                "price_type": price_type,
            })

        total = round(total, 2)
        received_amount = round(max(amount_paid, 0), 2)
        change_amount = round(max(received_amount - total, 0), 2)
        balance = round(max(total - received_amount, 0), 2)

        if customer_type == "credit" and received_amount == 0:
            payment_status = "credit"
        elif balance > 0:
            payment_status = "partial"
        else:
            payment_status = "paid"

        due_date = None
        if payment_status in ("credit", "partial"):
            due_date = (datetime.now() + timedelta(days=7)).date()

        cur.execute(
            """
            INSERT INTO sales
            (bill_no, customer_id, customer_name, customer_type, total, amount_paid, balance, due_date, payment_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bill_no,
                customer_id,
                customer_name,
                customer_type,
                total,
                received_amount,
                balance,
                due_date,
                payment_status,
            ),
        )

        for row in sale_rows:
            cur.execute(
                """
                INSERT INTO sale_items
                (bill_no, product_name, barcode, brand, category, batch_no, expiry_date, qty, price, line_total, price_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_no,
                    row["name"],
                    row["barcode"],
                    row["brand"],
                    row["category"],
                    row["batch_no"],
                    row["expiry_date"],
                    row["qty"],
                    row["price"],
                    row["line_total"],
                    row["price_type"],
                ),
            )
            cur.execute("UPDATE products SET stock = stock - ? WHERE barcode=?", (row["qty"], row["barcode"]))

        if customer_id and balance > 0:
            cur.execute("UPDATE customers SET balance = balance + ? WHERE id=?", (balance, customer_id))
            cur.execute(
                """
                INSERT INTO credit_ledger (customer_id, bill_no, entry_type, amount, note)
                VALUES (?, ?, 'sale', ?, ?)
                """,
                (customer_id, bill_no, balance, "Credit sale"),
            )

        conn.commit()
        return jsonify({
            "bill": {
                "bill_no": bill_no,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "customer_id": customer_id,
                "customer_name": customer_name,
                "customer_type": customer_type,
                "total": total,
                "amount_paid": received_amount,
                "customer_paid": received_amount,
                "balance": balance,
                "change_amount": change_amount,
                "due_date": fmt_date(due_date),
                "payment_status": payment_status,
                "items": [
                    {
                        "barcode": r["barcode"],
                        "product_name": r["name"],
                        "brand": r["brand"],
                        "category": r["category"],
                        "batch_no": r["batch_no"],
                        "expiry_date": fmt_date(r["expiry_date"]),
                        "qty": r["qty"],
                        "price": r["price"],
                        "line_total": r["line_total"],
                        "price_type": r["price_type"],
                    }
                    for r in sale_rows
                ],
            }
        })

    except ValueError as e:
        conn.rollback()
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({"message": str(e)}), 500
    finally:
        conn.close()

# ---------------- BILL DETAIL ----------------
@app.route("/api/bill/<bill_no>")
@login_required_api
def api_bill_detail(bill_no):
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("SELECT * FROM sales WHERE bill_no=?", (bill_no,))
    sale = cur.fetchone()
    if not sale:
        conn.close()
        return jsonify({"message": "Bill not found"}), 404

    cur.execute("SELECT * FROM sale_items WHERE bill_no=? ORDER BY id", (bill_no,))
    items = cur.fetchall()
    conn.close()
    return jsonify({"bill": sale_to_dict(sale), "items": items})

# ---------------- DASHBOARD SUMMARY ----------------
@app.route("/api/dashboard_summary")
@login_required_api
def api_dashboard_summary():
    conn = get_db()
    cur = dict_cursor(conn)
    today = local_today()
    month = local_month()

    cur.execute("SELECT COALESCE(SUM(total),0) AS total_sales, COUNT(*) AS bill_count FROM sales WHERE DATE(date)=?", (today,))
    day = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(total),0) AS total_sales, COUNT(*) AS bill_count FROM sales WHERE strftime('%Y-%m', date)=?", (month,))
    mon = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(balance),0) AS total_outstanding FROM sales WHERE payment_status IN ('credit','partial') OR customer_type='credit'")
    credit = cur.fetchone()
    cur.execute("""
        SELECT COUNT(*) AS low_stock_count
        FROM products
        WHERE stock <= ?
    """, (LOW_STOCK_THRESHOLD,))
    low = cur.fetchone()
    cur.execute("""
        SELECT product_name, COALESCE(SUM(qty),0) AS qty_sold
        FROM sale_items
        GROUP BY product_name
        ORDER BY qty_sold DESC
        LIMIT 5
    """)
    top_products = cur.fetchall()
    cur.execute("""
        SELECT DATE(date) AS day, COALESCE(SUM(total),0) AS total
        FROM sales
        WHERE DATE(date) >= date('now', '-7 day')
        GROUP BY DATE(date)
        ORDER BY day
    """)
    trend = cur.fetchall()
    conn.close()
    return jsonify({
        "today_sales": float(day["total_sales"]),
        "today_bills": int(day["bill_count"]),
        "month_sales": float(mon["total_sales"]),
        "month_bills": int(mon["bill_count"]),
        "credit_outstanding": float(credit["total_outstanding"]),
        "low_stock_count": int(low["low_stock_count"]),
        "top_products": [{"name": r["product_name"], "qty": int(r["qty_sold"])} for r in top_products],
        "trend": [{"day": str(r["day"]), "total": float(r["total"])} for r in trend],
    })

# ---------------- REPORTS ----------------
@app.route("/api/day_report")
@login_required_api
def api_day_report():
    day = request.args.get("day") or local_today()
    customer_type = request.args.get("customer_type") or ""
    payment_status = request.args.get("payment_status") or ""
    bill_no = request.args.get("bill_no") or ""

    conn = get_db()
    cur = dict_cursor(conn)
    sql, params = filter_sales_sql(customer_type or None, payment_status or None, bill_no or None, day=day)
    cur.execute(sql, params)
    bills = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(total),0) AS total_sales,
               COUNT(*) AS bill_count,
               COALESCE(SUM(CASE WHEN payment_status IN ('credit','partial') THEN total ELSE 0 END),0) AS credit_sales_amount,
               COALESCE(SUM(CASE WHEN customer_type='wholesale' THEN total ELSE 0 END),0) AS wholesaler_sales_amount
        FROM sales
        WHERE DATE(date) = ?
    """, (day,))
    totals = cur.fetchone()

    cur.execute("""
        SELECT si.product_name, COALESCE(SUM(si.qty),0) AS qty_sold
        FROM sale_items si
        JOIN sales s ON s.bill_no = si.bill_no
        WHERE DATE(s.date) = ?
        GROUP BY si.product_name
        ORDER BY qty_sold DESC
        LIMIT 1
    """, (day,))
    most_sold = cur.fetchone()

    cur.execute("""
        SELECT COALESCE(s.customer_name, 'Walk-in Customer') AS customer_name,
               COALESCE(SUM(s.total),0) AS total_spent
        FROM sales s
        WHERE DATE(s.date) = ?
        GROUP BY COALESCE(s.customer_name, 'Walk-in Customer')
        ORDER BY total_spent DESC
        LIMIT 5
    """, (day,))
    top_customers = cur.fetchall()

    cur.execute("""
        SELECT CAST(strftime('%H', date) AS INTEGER) AS hour, COALESCE(SUM(total),0) AS total
        FROM sales
        WHERE DATE(date) = ?
        GROUP BY CAST(strftime('%H', date) AS INTEGER)
        ORDER BY hour
    """, (day,))
    sales_by_hour = cur.fetchall()

    cur.execute("""
        SELECT customer_type, COALESCE(SUM(total),0) AS total
        FROM sales
        WHERE DATE(date) = ?
        GROUP BY customer_type
    """, (day,))
    customer_breakdown = cur.fetchall()
    conn.close()

    return jsonify({
        "day": day,
        "summary": {
            "total_sales": float(totals["total_sales"]),
            "bill_count": int(totals["bill_count"]),
            "credit_sales_amount": float(totals["credit_sales_amount"]),
            "wholesaler_sales_amount": float(totals["wholesaler_sales_amount"]),
            "most_sold_product": most_sold["product_name"] if most_sold else None,
        },
        "customer_breakdown": [{"customer_type": r["customer_type"], "total": float(r["total"])} for r in customer_breakdown],
        "top_customers": [{"customer_name": r["customer_name"], "total_spent": float(r["total_spent"])} for r in top_customers],
        "sales_by_hour": [{"hour": int(r["hour"]), "total": float(r["total"])} for r in sales_by_hour],
        "bills": [
            {
                "bill_no": r["bill_no"],
                "date": fmt_datetime(fmt_datetime(r["date"])),
                "total": float(r["total"]),
                "customer_type": r["customer_type"],
                "customer_name": r.get("customer_name"),
                "amount_paid": float(r.get("amount_paid") or 0),
                "balance": float(r.get("balance") or 0),
                "payment_status": r.get("payment_status") or "paid",
            }
            for r in bills
        ],
    })

@app.route("/api/day_report.csv")
@login_required_api
@admin_required_api
def api_day_report_csv():
    day = request.args.get("day") or local_today()
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT bill_no, strftime('%Y-%m-%d %H:%M:%S', date) AS date, total, customer_type, customer_name, amount_paid, balance, payment_status
        FROM sales
        WHERE DATE(date) = ?
        ORDER BY date DESC
    """, (day,))
    rows = cur.fetchall()
    conn.close()
    return csv_response(
        download_name("day_report", day),
        ["Bill No", "Date", "Total", "Customer Type", "Customer Name", "Amount Paid", "Balance", "Payment Status"],
        [[r["bill_no"], fmt_datetime(r["date"]), r["total"], r["customer_type"], r.get("customer_name") or "", r["amount_paid"], r["balance"], r["payment_status"]] for r in rows],
    )

@app.route("/api/month_report")
@login_required_api
def api_month_report():
    month = request.args.get("month") or local_month()
    customer_type = request.args.get("customer_type") or ""
    payment_status = request.args.get("payment_status") or ""
    bill_no = request.args.get("bill_no") or ""

    conn = get_db()
    cur = dict_cursor(conn)
    sql, params = filter_sales_sql(customer_type or None, payment_status or None, bill_no or None, month=month)
    cur.execute(sql, params)
    bills = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(total),0) AS total_sales,
               COUNT(*) AS bill_count,
               COALESCE(SUM(CASE WHEN payment_status IN ('credit','partial') THEN total ELSE 0 END),0) AS credit_sales_total,
               COALESCE(SUM(CASE WHEN customer_type='wholesale' THEN total ELSE 0 END),0) AS wholesaler_sales_total
        FROM sales
        WHERE strftime('%Y-%m', date) = ?
    """, (month,))
    totals = cur.fetchone()

    cur.execute("""
        SELECT DATE(date) AS day, COALESCE(SUM(total),0) AS total
        FROM sales
        WHERE strftime('%Y-%m', date) = ?
        GROUP BY DATE(date)
        ORDER BY day
    """, (month,))
    daily_trend = cur.fetchall()

    cur.execute("""
        SELECT si.product_name, COALESCE(SUM(si.qty),0) AS qty_sold
        FROM sale_items si
        JOIN sales s ON s.bill_no = si.bill_no
        WHERE strftime('%Y-%m', s.date) = ?
        GROUP BY si.product_name
        ORDER BY qty_sold DESC
        LIMIT 10
    """, (month,))
    top_products = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(si.category, 'Uncategorized') AS category, COALESCE(SUM(si.line_total),0) AS total
        FROM sale_items si
        JOIN sales s ON s.bill_no = si.bill_no
        WHERE strftime('%Y-%m', s.date) = ?
        GROUP BY COALESCE(si.category, 'Uncategorized')
        ORDER BY total DESC
        LIMIT 10
    """, (month,))
    top_categories = cur.fetchall()
    conn.close()

    return jsonify({
        "month": month,
        "summary": {
            "total_sales": float(totals["total_sales"]),
            "bill_count": int(totals["bill_count"]),
            "credit_sales_total": float(totals["credit_sales_total"]),
            "wholesaler_sales_total": float(totals["wholesaler_sales_total"]),
        },
        "daily_trend": [{"day": str(r["day"]), "total": float(r["total"])} for r in daily_trend],
        "top_products": [{"product_name": r["product_name"], "qty_sold": int(r["qty_sold"])} for r in top_products],
        "top_categories": [{"category": r["category"], "total": float(r["total"])} for r in top_categories],
        "bills": [
            {
                "bill_no": r["bill_no"],
                "date": fmt_datetime(fmt_datetime(r["date"])),
                "total": float(r["total"]),
                "customer_type": r["customer_type"],
                "customer_name": r.get("customer_name"),
                "amount_paid": float(r.get("amount_paid") or 0),
                "balance": float(r.get("balance") or 0),
                "payment_status": r.get("payment_status") or "paid",
            }
            for r in bills
        ],
    })

@app.route("/api/month_report.csv")
@login_required_api
@admin_required_api
def api_month_report_csv():
    month = request.args.get("month") or local_month()
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT bill_no, strftime('%Y-%m-%d %H:%M:%S', date) AS date, total, customer_type, customer_name, amount_paid, balance, payment_status
        FROM sales
        WHERE strftime('%Y-%m', date) = ?
        ORDER BY date DESC
    """, (month,))
    rows = cur.fetchall()
    conn.close()
    return csv_response(
        download_name("month_report", month),
        ["Bill No", "Date", "Total", "Customer Type", "Customer Name", "Amount Paid", "Balance", "Payment Status"],
        [[r["bill_no"], fmt_datetime(r["date"]), r["total"], r["customer_type"], r.get("customer_name") or "", r["amount_paid"], r["balance"], r["payment_status"]] for r in rows],
    )

@app.route("/api/wholesaler_report")
@login_required_api
def api_wholesaler_report():
    start = request.args.get("start")
    end = request.args.get("end")
    bill_no = request.args.get("bill_no") or ""
    conn = get_db()
    cur = dict_cursor(conn)

    sql = """
        SELECT s.bill_no, strftime('%Y-%m-%d %H:%M:%S', s.date) AS date,
               s.customer_name, s.total, s.amount_paid, s.balance, s.payment_status
        FROM sales s
        WHERE s.customer_type='wholesale'
    """
    params = []
    if start and end:
        sql += " AND DATE(s.date) BETWEEN ? AND ?"
        params += [start, end]
    if bill_no:
        sql += " AND s.bill_no LIKE ?"
        params.append(f"%{bill_no}%")
    sql += " ORDER BY s.date DESC"
    cur.execute(sql, params)
    bills = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(customer_name, 'Wholesale') AS customer_name,
               COALESCE(SUM(total),0) AS total_sales,
               COUNT(*) AS purchase_count
        FROM sales
        WHERE customer_type='wholesale'
        GROUP BY COALESCE(customer_name, 'Wholesale')
        ORDER BY total_sales DESC
        LIMIT 10
    """)
    top_wholesalers = cur.fetchall()

    cur.execute("""
        SELECT DATE(date) AS day, COALESCE(SUM(total),0) AS total
        FROM sales
        WHERE customer_type='wholesale'
        GROUP BY DATE(date)
        ORDER BY day
    """)
    trend = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(total),0) AS total_sales,
               COUNT(*) AS bill_count,
               COALESCE(SUM(amount_paid),0) AS paid_total,
               COALESCE(SUM(balance),0) AS balance_total
        FROM sales
        WHERE customer_type='wholesale'
    """)
    totals = cur.fetchone()
    conn.close()

    return jsonify({
        "summary": {
            "total_sales": float(totals["total_sales"]),
            "bill_count": int(totals["bill_count"]),
            "paid_total": float(totals["paid_total"]),
            "balance_total": float(totals["balance_total"]),
        },
        "top_wholesalers": [{"customer_name": r["customer_name"], "total_sales": float(r["total_sales"]), "purchase_count": int(r["purchase_count"])} for r in top_wholesalers],
        "trend": [{"day": str(r["day"]), "total": float(r["total"])} for r in trend],
        "bills": [
            {
                "bill_no": r["bill_no"],
                "date": fmt_datetime(r["date"]),
                "customer_name": r.get("customer_name"),
                "total": float(r["total"]),
                "amount_paid": float(r["amount_paid"]),
                "balance": float(r["balance"]),
                "payment_status": r["payment_status"],
            }
            for r in bills
        ],
    })

@app.route("/api/wholesaler_report.csv")
@login_required_api
@admin_required_api
def api_wholesaler_report_csv():
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT s.bill_no, strftime('%Y-%m-%d %H:%M:%S', s.date) AS date,
               s.customer_name, s.total, s.amount_paid, s.balance, s.payment_status
        FROM sales s
        WHERE s.customer_type='wholesale'
        ORDER BY s.date DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return csv_response(
        "wholesaler_report.csv",
        ["Bill No", "Date", "Customer Name", "Total", "Amount Paid", "Balance", "Payment Status"],
        [[r["bill_no"], fmt_datetime(r["date"]), r.get("customer_name") or "", r["total"], r["amount_paid"], r["balance"], r["payment_status"]] for r in rows],
    )

@app.route("/api/credit_report")
@login_required_api
def api_credit_report():
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT s.bill_no, strftime('%Y-%m-%d %H:%M:%S', s.date) AS date,
               s.customer_name, s.total, s.amount_paid, s.balance, s.payment_status,
               date(s.due_date) AS due_date
        FROM sales s
        WHERE s.payment_status IN ('credit','partial') OR s.customer_type='credit'
        ORDER BY s.date DESC
    """)
    bills = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(balance), 0) AS total_outstanding,
               COUNT(*) AS bill_count
        FROM sales
        WHERE payment_status IN ('credit', 'partial') OR customer_type='credit'
    """)
    totals = cur.fetchone()

    cur.execute("""
        SELECT DATE(date) AS day, COALESCE(SUM(balance),0) AS total_balance
        FROM sales
        WHERE payment_status IN ('credit', 'partial') OR customer_type='credit'
        GROUP BY DATE(date)
        ORDER BY day
    """)
    trend = cur.fetchall()
    conn.close()
    return jsonify({
        "summary": {
            "total_outstanding": float(totals["total_outstanding"]),
            "bill_count": int(totals["bill_count"]),
        },
        "trend": [{"day": str(r["day"]), "total_balance": float(r["total_balance"])} for r in trend],
        "bills": bills,
    })

@app.route("/api/credit_report.csv")
@login_required_api
@admin_required_api
def api_credit_report_csv():
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT s.bill_no, strftime('%Y-%m-%d %H:%M:%S', s.date) AS date,
               s.customer_name, s.total, s.amount_paid, s.balance, s.payment_status,
               date(s.due_date) AS due_date
        FROM sales s
        WHERE s.payment_status IN ('credit','partial') OR s.customer_type='credit'
        ORDER BY s.date DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return csv_response(
        "credit_report.csv",
        ["Bill No", "Date", "Customer Name", "Total", "Amount Paid", "Balance", "Payment Status", "Due Date"],
        [[r["bill_no"], fmt_datetime(r["date"]), r.get("customer_name") or "", r["total"], r["amount_paid"], r["balance"], r["payment_status"], fmt_date(r.get("due_date")) or ""] for r in rows],
    )

@app.route("/api/stock_report")
@login_required_api
def api_stock_report():
    category = request.args.get("category", "").strip()
    brand = request.args.get("brand", "").strip()
    batch_no = request.args.get("batch_no", "").strip()
    status = request.args.get("status", "").strip()
    expiry_from = request.args.get("expiry_from", "").strip()
    expiry_to = request.args.get("expiry_to", "").strip()
    conn = get_db()
    cur = dict_cursor(conn)

    sql = """
        SELECT id, sku, barcode, name, brand, category, batch_no, expiry_date, uom, stock, package_qty,
               CASE
                   WHEN stock = 0 THEN 'out_of_stock'
                   WHEN stock <= ? THEN 'low_stock'
                   ELSE 'in_stock'
               END AS stock_status
        FROM products
        WHERE 1=1
    """
    params = [LOW_STOCK_THRESHOLD]
    if category:
        sql += " AND COALESCE(category,'') = ?"
        params.append(category)
    if brand:
        sql += " AND COALESCE(brand,'') = ?"
        params.append(brand)
    if batch_no:
        sql += " AND COALESCE(batch_no,'') LIKE ?"
        params.append(f"%{batch_no}%")
    if expiry_from:
        sql += " AND expiry_date >= ?"
        params.append(expiry_from)
    if expiry_to:
        sql += " AND expiry_date <= ?"
        params.append(expiry_to)
    sql += " ORDER BY stock ASC, name ASC"

    cur.execute(sql, params)
    products = cur.fetchall()

    if status:
        products = [p for p in products if p["stock_status"] == status]

    low_stock = [p for p in products if p["stock_status"] == "low_stock"]
    out_of_stock = [p for p in products if p["stock_status"] == "out_of_stock"]
    alerts = []
    for p in products:
        if p.get("expiry_date"):
            st = expiry_status(p["expiry_date"])
            if st in ("expired", "near_expiry"):
                alerts.append({
                    "barcode": p["barcode"],
                    "name": p["name"],
                    "brand": p.get("brand"),
                    "expiry_date": fmt_date(p["expiry_date"]),
                    "status": st,
                })

    cur.execute("""
        SELECT COALESCE(category, 'Uncategorized') AS category, COALESCE(SUM(stock),0) AS total_stock
        FROM products
        GROUP BY COALESCE(category, 'Uncategorized')
        ORDER BY total_stock DESC
    """)
    category_summary = cur.fetchall()
    conn.close()

    return jsonify({
        "summary": {
            "total_products": len(products),
            "low_stock_count": len(low_stock),
            "out_of_stock_count": len(out_of_stock),
        },
        "products": [
            {
                "id": p["id"],
                "sku": p.get("sku"),
                "barcode": p["barcode"],
                "name": p["name"],
                "brand": p.get("brand"),
                "category": p.get("category"),
                "batch_no": p.get("batch_no"),
                "expiry_date": fmt_date(p["expiry_date"]),
                "package_qty": int(p.get("package_qty") or 0),
                "stock": int(p["stock"]),
                "stock_status": p["stock_status"],
                "expiry_status": expiry_status(p.get("expiry_date")),
            }
            for p in products
        ],
        "low_stock": low_stock,
        "out_of_stock": out_of_stock,
        "alerts": alerts,
        "category_summary": [{"category": r["category"], "total_stock": int(r["total_stock"])} for r in category_summary],
    })

@app.route("/api/stock_report.csv")
@login_required_api
@admin_required_api
def api_stock_report_csv():
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT sku, barcode, name, brand, category, batch_no, expiry_date, uom, stock,
               CASE
                   WHEN stock = 0 THEN 'out_of_stock'
                   WHEN stock <= ? THEN 'low_stock'
                   ELSE 'in_stock'
               END AS stock_status
        FROM products
        ORDER BY stock ASC, name ASC
    """, (LOW_STOCK_THRESHOLD,))
    rows = cur.fetchall()
    conn.close()
    return csv_response(
        "stock_report.csv",
        ["SKU", "Barcode", "Name", "Brand", "Category", "UOM", "Batch No", "Expiry Date", "Stock", "Stock Status"],
        [[r.get("sku") or "", r["barcode"], r["name"], r.get("brand") or "", r.get("category") or "", r.get("uom") or "pcs", r.get("batch_no") or "", fmt_date(r.get("expiry_date")) or "", r["stock"], r["stock_status"]] for r in rows],
    )

@app.route("/api/inventory_report")
@login_required_api
def api_inventory_report():
    return api_stock_report()

@app.route("/api/inventory_report.csv")
@login_required_api
@admin_required_api
def api_inventory_report_csv():
    return api_stock_report_csv()

@app.route("/api/customer_ledger/<int:customer_id>")
@login_required_api
def api_customer_ledger(customer_id):
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("SELECT * FROM customers WHERE id=?", (customer_id,))
    customer = cur.fetchone()
    if not customer:
        conn.close()
        return jsonify({"message": "Customer not found"}), 404
    cur.execute("SELECT * FROM credit_ledger WHERE customer_id=? ORDER BY created_at DESC", (customer_id,))
    ledger = cur.fetchall()
    conn.close()
    return jsonify({"customer": customer_to_dict(customer), "ledger": ledger})

@app.route("/api/send_expiry_alerts", methods=["POST"])
@login_required_api
@admin_required_api
def api_send_expiry_alerts():
    # Placeholder for SMS gateway integration (Twilio/other provider)
    conn = get_db()
    cur = dict_cursor(conn)
    cur.execute("""
        SELECT barcode, name, brand, expiry_date, stock
        FROM products
        WHERE expiry_date IS NOT NULL AND expiry_date <= date('now', '+' || ? || ' day')
        ORDER BY expiry_date ASC
    """, (NEAR_EXPIRY_DAYS,))
    rows = cur.fetchall()
    conn.close()
    return jsonify({
        "message": "Expiry alerts prepared",
        "count": len(rows),
        "items": [
            {
                "barcode": r["barcode"],
                "name": r["name"],
                "brand": r.get("brand"),
                "expiry_date": fmt_date(r.get("expiry_date")),
                "stock": int(r["stock"]),
            }
            for r in rows
        ],
        "sms_integration": "Configure SMS provider env vars to send real phone alerts",
    })

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"message": "Route not found"}), 404
    return render_template("404.html"), 404

@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith("/api/"):
        return jsonify({"message": "Forbidden"}), 403
    return render_template("403.html"), 403

# ---------------- RUN ----------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
