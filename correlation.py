"""Correlation engine (report section 4.4.3, Table 4.3).

A camera shelf-exit is only a *candidate* event. It is closed as a legitimate
sale when a transaction for the same product exists within +/- the matching
window; otherwise an unresolved theft alert is written."""
from datetime import timedelta

from db import now, fmt, parse


def within_window(event_time, txn_time, window=90):
    """True when the sale is at most `window` seconds from the exit (inclusive)."""
    return abs((event_time - txn_time).total_seconds()) <= window


def find_matching_transaction(db, product_id, event_time, window=90):
    lo, hi = event_time - timedelta(seconds=window), event_time + timedelta(seconds=window)
    rows = db.all("SELECT * FROM transactions WHERE product_id=? AND timestamp BETWEEN ? AND ? "
                  "ORDER BY timestamp", (product_id, fmt(lo), fmt(hi)))
    rows = [r for r in rows if within_window(event_time, parse(r["timestamp"]), window)]
    if not rows:
        return None
    return min(rows, key=lambda r: abs((parse(r["timestamp"]) - event_time).total_seconds()))


def process_exit_event(db, product_id, event_time=None, window=90, dedupe_seconds=0):
    """Decide what a detected shelf exit means. Returns a dict describing the outcome."""
    if not db.one("SELECT id FROM products WHERE id=?", (product_id,)):
        raise ValueError("Product not found")
    event_time = parse(event_time) if event_time else now()

    txn = find_matching_transaction(db, product_id, event_time, window)
    if txn:  # Camera and sales record agree: legitimate. Stock was already reduced by the sale.
        return {"outcome": "legitimate", "transaction_id": txn["id"], "alert_id": None}

    if dedupe_seconds:
        lo, hi = event_time - timedelta(seconds=dedupe_seconds), event_time + timedelta(seconds=dedupe_seconds)
        dup = db.one("SELECT id FROM alerts WHERE product_id=? AND alert_type='theft' AND status='unresolved' "
                     "AND camera_event_time BETWEEN ? AND ?", (product_id, fmt(lo), fmt(hi)))
        if dup:
            return {"outcome": "alert", "transaction_id": None, "alert_id": dup["id"], "duplicate": True}

    alert_id = db.insert("INSERT INTO alerts (product_id, alert_type, camera_event_time, status) "
                         "VALUES (?, 'theft', ?, 'unresolved')", (product_id, fmt(event_time)))
    db.commit()
    return {"outcome": "alert", "transaction_id": None, "alert_id": alert_id}
