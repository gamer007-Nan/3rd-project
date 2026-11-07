"""Flask application: authentication, inventory CRUD, transactions, alerts,
camera-event intake and audit-log export (report Appendix C, Table C.2)."""
import csv
import hmac
import io
import threading
import time
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

import config as config_module
import correlation
import inventory
from db import Database, IntegrityErrors, create_user, fmt, now, parse


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(config_module.load())
    if overrides:
        app.config.update(overrides)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    failures = {}  # (ip, username) -> [timestamps]; simple brute-force guard

    # ---------- helpers ----------
    def get_db():
        if "db" not in g:
            g.db = Database(app.config)
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def wants_json():
        return request.accept_mimetypes.best == "application/json"

    def deny(code, message):
        if wants_json():
            return jsonify(error=message), code
        return redirect(url_for("login_page"))

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if "user_id" not in session:
                return deny(401, "Please sign in")
            return fn(*a, **k)
        return wrapper

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if "user_id" not in session:
                return deny(401, "Please sign in")
            if session.get("role") != "admin":
                return deny(403, "Administrator access required")
            return fn(*a, **k)
        return wrapper

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def bad(message, code=400):
        return jsonify(error=message), code

    def validate_product(data, partial=False):
        clean = {}
        for field, limit in (("name", 100), ("sku", 20), ("category", 50)):
            if field in data or not partial:
                value = str(data.get(field, "")).strip()
                if not value or len(value) > limit:
                    return None, f"{field} is required (max {limit} characters)"
                clean[field] = value
        if "price" in data or not partial:
            try:
                price = Decimal(str(data.get("price")))
                if price < 0 or price.as_tuple().exponent < -2:
                    raise InvalidOperation
            except (InvalidOperation, TypeError):
                return None, "price must be a non-negative number with at most 2 decimals"
            clean["price"] = str(price)
        for field in ("stock_level", "reorder_point"):
            if field in data or not partial:
                value = data.get(field, 0)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return None, f"{field} must be a whole number, 0 or more"
                clean[field] = value
        return clean, None

    # ---------- pages ----------
    @app.get("/login")
    def login_page():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", username=session["username"], role=session["role"])

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    # ---------- authentication ----------
    @app.post("/login")
    def login():
        data = body() or request.form
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        key = (request.remote_addr, username.lower())
        recent = [t for t in failures.get(key, []) if time.time() - t < 300]
        if len(recent) >= 5:
            return bad("Too many failed attempts. Try again in a few minutes.", 429)
        user = get_db().one("SELECT * FROM users WHERE username=?", (username,))
        if not user or not check_password_hash(user["password_hash"], password):
            failures[key] = recent + [time.time()]
            return bad("Invalid username or password", 401)
        failures.pop(key, None)
        session.clear()
        session.update(user_id=user["id"], username=user["username"], role=user["role"])
        if request.is_json:
            return jsonify(ok=True, username=user["username"], role=user["role"])
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/users")
    @admin_required
    def add_user():
        data = body()
        try:
            uid = create_user(get_db(), str(data.get("username", "")).strip(),
                              str(data.get("password", "")), data.get("role", "staff"))
        except ValueError as exc:
            return bad(str(exc))
        except IntegrityErrors:
            get_db().rollback()
            return bad("That username already exists", 409)
        return jsonify(id=uid), 201

    # ---------- products ----------
    @app.get("/products")
    @login_required
    def list_products():
        rows = get_db().all("SELECT * FROM products ORDER BY name")
        for r in rows:
            r["low_stock"] = r["stock_level"] <= r["reorder_point"]
        return jsonify(rows)

    @app.post("/products")
    @admin_required
    def add_product():
        clean, error = validate_product(body())
        if error:
            return bad(error)
        db = get_db()
        try:
            pid = db.insert("INSERT INTO products (name, sku, category, price, stock_level, reorder_point) "
                            "VALUES (?,?,?,?,?,?)",
                            (clean["name"], clean["sku"], clean["category"], clean["price"],
                             clean["stock_level"], clean["reorder_point"]))
        except IntegrityErrors:
            db.rollback()
            return bad("That SKU already exists", 409)
        inventory.check_low_stock(db, pid)
        return jsonify(id=pid), 201

    @app.put("/products/<int:pid>")
    @admin_required
    def edit_product(pid):
        db = get_db()
        if not db.one("SELECT id FROM products WHERE id=?", (pid,)):
            return bad("Product not found", 404)
        clean, error = validate_product(body(), partial=True)
        if error:
            return bad(error)
        if not clean:
            return bad("Nothing to update")
        columns = [c for c in ("name", "sku", "category", "price", "stock_level", "reorder_point") if c in clean]
        try:
            db.execute(f"UPDATE products SET {', '.join(c + '=?' for c in columns)} WHERE id=?",
                       [clean[c] for c in columns] + [pid])
        except IntegrityErrors:
            db.rollback()
            return bad("That SKU already exists", 409)
        inventory.check_low_stock(db, pid)
        return jsonify(ok=True)

    @app.delete("/products/<int:pid>")
    @admin_required
    def delete_product(pid):
        db = get_db()
        if not db.one("SELECT id FROM products WHERE id=?", (pid,)):
            return bad("Product not found", 404)
        used = db.one("SELECT (SELECT COUNT(*) FROM transactions WHERE product_id=?) + "
                      "(SELECT COUNT(*) FROM alerts WHERE product_id=?) AS n", (pid, pid))
        if used["n"]:
            return bad("This product has sales or alerts on record and cannot be removed", 409)
        db.execute("DELETE FROM products WHERE id=?", (pid,))
        db.commit()
        return jsonify(ok=True)

    @app.get("/products/<int:pid>/stock")
    @login_required
    def product_stock(pid):
        p = get_db().one("SELECT id, name, sku, stock_level, reorder_point FROM products WHERE id=?", (pid,))
        if not p:
            return bad("Product not found", 404)
        p["needs_reorder"] = p["stock_level"] <= p["reorder_point"]
        return jsonify(p)

    # ---------- transactions ----------
    @app.post("/transactions")
    @login_required
    def add_transaction():
        data = body()
        db = get_db()
        pid = data.get("product_id")
        if pid is None and data.get("sku"):
            row = db.one("SELECT id FROM products WHERE sku=?", (str(data["sku"]).strip(),))
            pid = row["id"] if row else None
        till = data.get("till_id")
        if till is not None and len(str(till)) > 20:
            return bad("till_id is too long (max 20 characters)")
        timestamp = data.get("timestamp") if session.get("role") == "admin" else None
        try:
            result = inventory.record_transaction(
                db, pid, data.get("quantity", 1), data.get("source", "qr_scan"),
                str(till) if till else None, timestamp, app.config["MATCH_WINDOW_SECONDS"])
        except (inventory.InventoryError, ValueError) as exc:
            db.rollback()
            return bad(str(exc))
        return jsonify(result), 201

    @app.get("/transactions")
    @login_required
    def list_transactions():
        limit = min(max(request.args.get("limit", 20, type=int), 1), 200)
        return jsonify(get_db().all(
            "SELECT t.*, p.name AS product_name FROM transactions t JOIN products p ON p.id=t.product_id "
            "ORDER BY t.timestamp DESC, t.id DESC LIMIT ?", (limit,)))

    # ---------- camera events ----------
    @app.post("/camera/events")
    def camera_event():
        key = app.config["CAMERA_API_KEY"]
        sent = request.headers.get("X-API-Key", "")
        key_ok = bool(key) and hmac.compare_digest(sent, key)
        if not key_ok and "user_id" not in session:
            return bad("Camera key or login required", 401)
        data = body()
        db = get_db()
        pid = data.get("product_id")
        if pid is None and data.get("sku"):
            row = db.one("SELECT id FROM products WHERE sku=?", (str(data["sku"]).strip(),))
            pid = row["id"] if row else None
        try:
            result = correlation.process_exit_event(
                db, pid, data.get("timestamp"), app.config["MATCH_WINDOW_SECONDS"], dedupe_seconds=5)
        except ValueError as exc:
            return bad(str(exc))
        return jsonify(result), 201

    # ---------- alerts ----------
    @app.get("/alerts")
    @login_required
    def list_alerts():
        sql = ("SELECT a.*, p.name AS product_name, p.sku FROM alerts a JOIN products p ON p.id=a.product_id")
        clauses, params = [], []
        status = request.args.get("status")
        if status in ("unresolved", "resolved", "false_alarm"):
            clauses.append("a.status=?"); params.append(status)
        kind = request.args.get("type")
        if kind in ("theft", "low_stock"):
            clauses.append("a.alert_type=?"); params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
        sql += " ORDER BY a.camera_event_time DESC, a.id DESC LIMIT ?"
        return jsonify(get_db().all(sql, params + [limit]))

    @app.post("/alerts/<int:aid>/resolve")
    @admin_required
    def resolve_alert(aid):
        status = body().get("status", "resolved")
        if status not in ("resolved", "false_alarm"):
            return bad("status must be 'resolved' or 'false_alarm'")
        db = get_db()
        if not db.one("SELECT id FROM alerts WHERE id=?", (aid,)):
            return bad("Alert not found", 404)
        db.execute("UPDATE alerts SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                   (status, session["user_id"], fmt(now()), aid))
        db.commit()
        return jsonify(ok=True)

    @app.get("/summary")
    @login_required
    def summary():
        db = get_db()
        day_start = now().replace(hour=0, minute=0, second=0)
        return jsonify(
            products=db.one("SELECT COUNT(*) AS n FROM products")["n"],
            low_stock=db.one("SELECT COUNT(*) AS n FROM products WHERE stock_level <= reorder_point")["n"],
            open_theft_alerts=db.one("SELECT COUNT(*) AS n FROM alerts WHERE alert_type='theft' "
                                     "AND status='unresolved'")["n"],
            sales_today=db.one("SELECT COALESCE(SUM(quantity),0) AS n FROM transactions WHERE timestamp >= ?",
                               (fmt(day_start),))["n"])

    # ---------- reports ----------
    @app.get("/reports/audit-log")
    @admin_required
    def audit_log():
        start = request.args.get("from") or "1970-01-01"
        end = request.args.get("to") or "2999-12-31"
        try:
            lo, hi = parse(start + " 00:00:00" if len(start) == 10 else start), \
                     parse(end + " 23:59:59" if len(end) == 10 else end)
        except ValueError:
            return bad("Dates must look like 2026-11-07")
        db = get_db()
        sales = db.all("SELECT t.id, t.timestamp, p.name, p.sku, t.quantity, t.source, t.till_id "
                       "FROM transactions t JOIN products p ON p.id=t.product_id "
                       "WHERE t.timestamp BETWEEN ? AND ?", (fmt(lo), fmt(hi)))
        alerts = db.all("SELECT a.id, a.camera_event_time, p.name, p.sku, a.alert_type, a.status, "
                        "a.matched_transaction_id, a.reviewed_by FROM alerts a JOIN products p ON p.id=a.product_id "
                        "WHERE a.camera_event_time BETWEEN ? AND ?", (fmt(lo), fmt(hi)))
        rows = [[s["timestamp"], "inventory_change", s["id"], s["name"], s["sku"],
                 f"sold {s['quantity']} via {s['source']} till={s['till_id'] or '-'}"] for s in sales]
        rows += [[a["camera_event_time"], f"alert_{a['alert_type']}", a["id"], a["name"], a["sku"],
                  f"status={a['status']} matched_txn={a['matched_transaction_id'] or '-'} "
                  f"reviewed_by={a['reviewed_by'] or '-'}"] for a in alerts]
        rows.sort(key=lambda r: r[0])
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["time", "event", "record_id", "product", "sku", "details"])
        # Prefix cells that start with =,+,-,@ so spreadsheets don't run them as formulas.
        writer.writerows([[("'" + str(c)) if str(c)[:1] in "=+-@" else c for c in r] for r in rows])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=audit-log.csv"})

    # ---------- scheduled low-stock check (report section 4.4.2) ----------
    seconds = app.config["SCHEDULER_SECONDS"]
    if seconds > 0 and not app.config.get("TESTING"):
        def loop():
            while True:
                time.sleep(seconds)
                try:
                    db = Database(app.config)
                    inventory.check_low_stock(db)
                    db.close()
                except Exception as exc:  # keep the scheduler alive
                    print("Scheduler error:", exc)
        threading.Thread(target=loop, daemon=True).start()

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=False)
