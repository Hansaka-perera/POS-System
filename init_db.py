from werkzeug.security import generate_password_hash
import sqlite3

DB_FILE = "pos.db"

def ensure_column(conn, table, column, definition):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cur.fetchall()]

    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        conn.commit()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # CUSTOMERS
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

    # PRODUCTS (UPDATED)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barcode TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        sku TEXT,
        brand TEXT,
        category TEXT,
        batch_no TEXT,
        expiry_date TEXT,
        uom TEXT,
        price REAL NOT NULL DEFAULT 0,
        wholesale_price REAL NOT NULL DEFAULT 0,
        discount_price REAL NOT NULL DEFAULT 0,
        stock INTEGER NOT NULL DEFAULT 0,
        package_qty INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # SALES
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
        payment_status TEXT NOT NULL DEFAULT 'paid',
        date TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # SALE ITEMS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT NOT NULL,
        product_name TEXT NOT NULL,
        barcode TEXT NOT NULL,
        batch_no TEXT,
        expiry_date TEXT,
        qty INTEGER NOT NULL,
        price REAL NOT NULL,
        line_total REAL NOT NULL,
        price_type TEXT NOT NULL DEFAULT 'retail'
    )
    """)

    # CREDIT LEDGER
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



    # ✅ DEFAULT ADMIN ONLY (NO DEMO PRODUCTS)
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("1234"), "admin")
        )

    # OPTIONAL: default customers
    cur.execute("SELECT COUNT(*) FROM customers")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO customers (name, customer_type, phone, balance) VALUES (?, ?, ?, ?)",
            [
                ("Walk-in Customer", "retail", None, 0),
                ("Wholesale Customer", "wholesale", None, 0),
                ("Credit Customer", "credit", None, 0),
            ]
        )

    conn.commit()
    conn.close()

    print("✅ SQLite Database ready (NO DEMO PRODUCTS)")