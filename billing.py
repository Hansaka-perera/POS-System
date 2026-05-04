from flask import Blueprint, render_template, request, redirect
import mysql.connector

billing = Blueprint("billing", __name__)

def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        port=3308,
        database="pos_system"
    )

# 🧾 VIEW BILL
@billing.route("/bill/<bill_no>")
def view_bill(bill_no):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT * FROM sales WHERE bill_no=%s", (bill_no,))
    bill = cur.fetchone()

    cur.execute("SELECT * FROM sale_items WHERE bill_no=%s", (bill_no,))
    items = cur.fetchall()

    return render_template("bill.html", bill=bill, items=items)


# ✏️ EDIT BILL (change customer name)
@billing.route("/edit_bill/<bill_no>", methods=["GET", "POST"])
def edit_bill(bill_no):
    db = get_db()
    cur = db.cursor(dictionary=True)

    if request.method == "POST":
        name = request.form["customer_name"]

        cur.execute(
            "UPDATE sales SET customer_name=%s WHERE bill_no=%s",
            (name, bill_no)
        )
        db.commit()

    cur.execute("SELECT * FROM sales WHERE bill_no=%s", (bill_no,))
    bill = cur.fetchone()

    cur.execute("SELECT * FROM sale_items WHERE bill_no=%s", (bill_no,))
    items = cur.fetchall()

    return render_template("edit_bill.html", bill=bill, items=items)


# ➕ ADD ITEM
@billing.route("/add_item/<bill_no>", methods=["POST"])
def add_item(bill_no):
    name = request.form["product_name"]
    qty = int(request.form["qty"])
    price = float(request.form["price"])

    total = qty * price

    db = get_db()
    cur = db.cursor()

    cur.execute("""
    INSERT INTO sale_items (bill_no, product_name, barcode, qty, price, line_total)
    VALUES (%s, %s, %s, %s, %s, %s)
    """, (bill_no, name, "manual", qty, price, total))

    cur.execute("""
    UPDATE sales SET total = total + %s WHERE bill_no=%s
    """, (total, bill_no))

    db.commit()

    return redirect(f"/edit_bill/{bill_no}")

