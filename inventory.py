"""Inventory module (report section 4.4.2): transaction-triggered stock decrement
and the reorder-point check that writes low-stock alerts."""
from datetime import timedelta

from db import now, fmt, parse

SOURCES = ("qr_scan", "pos_manual")


class InventoryError(ValueError):
    pass


def record_transaction(db, product_id, quantity=1, source="qr_scan", till_id=None,
                       timestamp=None, window=90):
    """Log one sale. Every sale reduces stock; staff never edit stock by hand."""
    product = db.one("SELECT * FROM products WHERE id=?", (product_id,))
    if not product:
        raise InventoryError("Product not found")
    if not isinstance(quantity, int) or quantity < 1:
        raise InventoryError("Quantity must be a whole number of at least 1")
    if source not in SOURCES:
        raise InventoryError("Source must be 'qr_scan' or 'pos_manual'")
    ts = parse(timestamp) if timestamp else now()

    tid = db.insert(
        "INSERT INTO transactions (product_id, quantity, source, till_id, timestamp) VALUES (?,?,?,?,?)",
        (product_id, quantity, source, till_id, fmt(ts)))
    db.execute(
        "UPDATE products SET stock_level = CASE WHEN stock_level >= ? THEN stock_level - ? ELSE 0 END WHERE id=?",
        (quantity, quantity, product_id))
    oversold = product["stock_level"] < quantity

    resolved = resolve_pending_alerts(db, product_id, ts, tid, quantity, window)
    low = check_low_stock(db, product_id)
    db.commit()
    return {"transaction_id": tid, "stock_level": db.one(
        "SELECT stock_level FROM products WHERE id=?", (product_id,))["stock_level"],
        "oversold": oversold, "resolved_alerts": resolved, "low_stock_alerts": low}


def resolve_pending_alerts(db, product_id, ts, txn_id, quantity, window):
    """A sale that is logged shortly after a camera event explains that event."""
    lo, hi = ts - timedelta(seconds=window), ts + timedelta(seconds=window)
    rows = db.all(
        "SELECT id, camera_event_time FROM alerts WHERE product_id=? AND alert_type='theft' "
        "AND status='unresolved' AND camera_event_time BETWEEN ? AND ?",
        (product_id, fmt(lo), fmt(hi)))
    rows.sort(key=lambda r: abs((parse(r["camera_event_time"]) - ts).total_seconds()))
    resolved = []
    for row in rows[:quantity]:
        db.execute("UPDATE alerts SET status='resolved', matched_transaction_id=? WHERE id=?",
                   (txn_id, row["id"]))
        resolved.append(row["id"])
    return resolved


def check_low_stock(db, product_id=None):
    """Write a low-stock alert when stock_level <= reorder_point (once per product),
    and close old low-stock alerts once a product has been restocked."""
    where, params = ("AND id=?", (product_id,)) if product_id else ("", ())
    created = []
    for p in db.all(f"SELECT id FROM products WHERE stock_level <= reorder_point {where}", params):
        open_alert = db.one("SELECT id FROM alerts WHERE product_id=? AND alert_type='low_stock' "
                            "AND status='unresolved'", (p["id"],))
        if not open_alert:
            created.append(db.insert(
                "INSERT INTO alerts (product_id, alert_type, camera_event_time, status) "
                "VALUES (?, 'low_stock', ?, 'unresolved')", (p["id"], fmt(now()))))
    for a in db.all("SELECT a.id FROM alerts a JOIN products p ON p.id=a.product_id "
                    "WHERE a.alert_type='low_stock' AND a.status='unresolved' "
                    "AND p.stock_level > p.reorder_point"):
        db.execute("UPDATE alerts SET status='resolved' WHERE id=?", (a["id"],))
    db.commit()
    return created
