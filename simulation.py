"""Synthetic dataset and evaluation (report sections 4.5 and 4.6.3).

60 products in nine categories, 420 shelf-exit events (45 thefts, as in Table 4.4), a 94% scan
compliance rate and a +/-90 s matching window. Everything is seeded so the
results can be repeated."""
import bisect
import random
from datetime import datetime, timedelta

import correlation
import inventory
from db import fmt

CATEGORIES = {
    "Beverages": ["Bottled Water 75cl", "Orange Juice 1L", "Cola 50cl", "Malt Drink", "Energy Drink", "Iced Tea", "Yoghurt Drink"],
    "Snacks": ["Plantain Chips", "Peanuts 100g", "Biscuits Pack", "Chin Chin", "Popcorn", "Chocolate Bar", "Cashew Nuts"],
    "Household": ["Detergent 1kg", "Dish Soap", "Bleach 500ml", "Sponge Pack", "Air Freshener", "Bin Liners", "Matches Box"],
    "Personal Care": ["Bath Soap", "Toothpaste", "Body Lotion", "Deodorant", "Shampoo", "Razor Pack", "Tissue Roll"],
    "Dairy": ["Evaporated Milk", "Powdered Milk", "Butter 250g", "Cheese Slices", "Plain Yoghurt", "Cream 200ml", "Condensed Milk"],
    "Bakery": ["Sliced Bread", "Sweet Bread", "Doughnuts 4pk", "Meat Pie", "Cake Slice", "Buns 6pk", "Croissant 2pk"],
    "Frozen Foods": ["Frozen Chicken 1kg", "Fish Fillet", "Sausage Pack", "Ice Cream Tub", "Mixed Vegetables", "Fries 1kg"],
    "Stationery": ["Ballpoint Pens", "Exercise Book", "Pencil Box", "Marker Set", "Sticky Notes", "A4 Paper Ream"],
    "Electronics Accessories": ["Phone Charger", "USB Cable", "Earphones", "Power Bank", "Phone Case", "Memory Card"],
}


def generate_dataset(seed=9, n_events=420, n_theft=45, compliance=0.94, window=90, hours=4):
    rng = random.Random(seed)
    names = [(cat, n) for cat, items in CATEGORIES.items() for n in items][:60]
    products = [{"id": i + 1, "name": n, "sku": f"SKU{i + 1:04d}", "category": c,
                 "price": round(rng.uniform(200, 15000), 2),
                 "reorder_point": rng.randint(5, 20)} for i, (c, n) in enumerate(names)]
    day = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    events, transactions = [], []
    for i in range(n_events):
        t = day + timedelta(seconds=rng.randint(0, hours * 3600))
        pid = rng.choice(products)["id"]
        theft = i < n_theft
        events.append({"product_id": pid, "time": t, "theft": theft})
        if theft:
            continue
        if rng.random() < compliance:
            offset = rng.randint(-(window - 5), window - 5)
        else:  # customer scanned late or at the wrong moment: outside the window
            offset = rng.choice((-1, 1)) * rng.randint(window + 10, 900)
        transactions.append({"product_id": pid, "time": t + timedelta(seconds=offset),
                             "source": "qr_scan" if rng.random() < 0.8 else "pos_manual",
                             "till_id": f"T{rng.randint(1, 3)}", "quantity": 1})
    for _ in range(2):  # scan-only sales with no camera event (test case TC-01)
        transactions.append({"product_id": rng.choice(products)["id"],
                             "time": day + timedelta(seconds=rng.randint(0, hours * 3600)),
                             "source": "qr_scan", "till_id": "T1", "quantity": 1})
    rng.shuffle(events)
    transactions.sort(key=lambda x: x["time"])
    sold = {}
    for t in transactions:
        sold[t["product_id"]] = sold.get(t["product_id"], 0) + t["quantity"]
    for p in products:
        p["stock_level"] = sold.get(p["id"], 0) + rng.randint(6, 60)
    return {"products": products, "events": events, "transactions": transactions, "window": window}


def evaluate(ds):
    """Pure in-memory run of the correlation rule over every event."""
    by_product = {}
    for t in ds["transactions"]:
        by_product.setdefault(t["product_id"], []).append(t["time"])
    for times in by_product.values():
        times.sort()
    tp = fp = tn = fn = 0
    for e in ds["events"]:
        times = by_product.get(e["product_id"], [])
        i = bisect.bisect_left(times, e["time"] - timedelta(seconds=ds["window"] + 1))
        matched = any(correlation.within_window(e["time"], t, ds["window"]) for t in times[i:i + 50]
                      if t <= e["time"] + timedelta(seconds=ds["window"] + 1))
        flagged = not matched
        if flagged and e["theft"]: tp += 1
        elif flagged: fp += 1
        elif e["theft"]: fn += 1
        else: tn += 1
    return metrics(tp, fp, tn, fn)


def metrics(tp, fp, tn, fn):
    div = lambda a, b: a / b if b else 0.0
    precision, recall = div(tp, tp + fp), div(tp, tp + fn)
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "accuracy": div(tp + tn, tp + fp + tn + fn), "precision": precision, "recall": recall,
            "f1": div(2 * precision * recall, precision + recall),
            "false_alarm_rate": div(fp, tp + fp)}


def load_into_db(db, ds, window=90):
    """Insert products and sales (through the real inventory logic), then push
    every camera event through the real correlation engine. Returns results
    measured from the database, including inventory accuracy."""
    for p in ds["products"]:
        db.execute("INSERT INTO products (id, name, sku, category, price, stock_level, reorder_point) "
                   "VALUES (?,?,?,?,?,?,?)", (p["id"], p["name"], p["sku"], p["category"], p["price"],
                                              p["stock_level"], p["reorder_point"]))
    db.commit()
    expected = {p["id"]: p["stock_level"] for p in ds["products"]}
    for t in ds["transactions"]:
        inventory.record_transaction(db, t["product_id"], t["quantity"], t["source"], t["till_id"],
                                     fmt(t["time"]), window)
        expected[t["product_id"]] -= t["quantity"]
    for e in ds["events"]:
        correlation.process_exit_event(db, e["product_id"], fmt(e["time"]), window)
    alerts = {(a["product_id"], a["camera_event_time"]) for a in
              db.all("SELECT product_id, camera_event_time FROM alerts WHERE alert_type='theft'")}
    tp = fp = tn = fn = 0
    for e in ds["events"]:
        flagged = (e["product_id"], fmt(e["time"])) in alerts
        if flagged and e["theft"]: tp += 1
        elif flagged: fp += 1
        elif e["theft"]: fn += 1
        else: tn += 1
    stock = {r["id"]: r["stock_level"] for r in db.all("SELECT id, stock_level FROM products")}
    matched = sum(1 for pid, v in expected.items() if stock[pid] == v)
    result = metrics(tp, fp, tn, fn)
    result["inventory_accuracy"] = matched / len(expected)
    result["products_matched"] = f"{matched} of {len(expected)}"
    return result
