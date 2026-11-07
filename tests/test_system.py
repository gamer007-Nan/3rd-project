"""Runs the five test cases from the report (TC-01 to TC-05) plus extra checks.
    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import correlation
import inventory
import simulation
from app import create_app
from db import Database, create_user, fmt
from detector import ExitTracker

JSON = {"Accept": "application/json"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"DB_PATH": os.path.join(self.tmp.name, "t.db"), "TESTING": True,
                               "SECRET_KEY": "test", "CAMERA_API_KEY": "camkey"})
        self.db = Database(self.app.config)
        self.db.init_schema()
        create_user(self.db, "admin", "Admin#12345", "admin")
        create_user(self.db, "staff", "Staff#12345", "staff")
        self.db.execute("INSERT INTO products (name, sku, category, price, stock_level, reorder_point) "
                        "VALUES ('Cola', 'C1', 'Beverages', 500, 10, 2)")
        self.db.commit()
        self.pid = 1

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def login(self, who="staff"):
        c = self.app.test_client()
        r = c.post("/login", json={"username": who, "password": who.capitalize() + "#12345"})
        self.assertEqual(r.status_code, 200)
        return c

    def stock(self):
        return self.db.one("SELECT stock_level FROM products WHERE id=1")["stock_level"]

    def alerts(self, kind="theft"):
        return self.db.all("SELECT * FROM alerts WHERE alert_type=?", (kind,))


class ReportTestCases(Base):
    def test_tc01_scan_without_camera_event(self):
        c = self.login()
        r = c.post("/transactions", json={"product_id": self.pid, "quantity": 1, "source": "qr_scan"}, headers=JSON)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.stock(), 9)
        self.assertEqual(self.alerts(), [])

    def test_tc02_exit_matched_to_sale_decrements_once(self):
        t = datetime.now().replace(microsecond=0)
        inventory.record_transaction(self.db, self.pid, 1, "pos_manual", "T1", fmt(t))
        result = correlation.process_exit_event(self.db, self.pid, fmt(t + timedelta(seconds=30)))
        self.assertEqual(result["outcome"], "legitimate")
        self.assertEqual(self.stock(), 9)  # once, not twice
        self.assertEqual(self.alerts(), [])

    def test_tc03_exit_without_sale_raises_alert(self):
        c = self.login()
        r = c.post("/camera/events", json={"product_id": self.pid}, headers=JSON)
        self.assertEqual(r.get_json()["outcome"], "alert")
        shown = c.get("/alerts?status=unresolved", headers=JSON).get_json()
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["status"], "unresolved")

    def test_tc04_low_stock_alert_at_reorder_point(self):
        self.db.execute("UPDATE products SET stock_level=3 WHERE id=1")
        self.db.commit()
        inventory.record_transaction(self.db, self.pid, 1, "qr_scan")  # 3 -> 2 = reorder point
        low = self.alerts("low_stock")
        self.assertEqual(len(low), 1)
        inventory.record_transaction(self.db, self.pid, 1, "qr_scan")
        self.assertEqual(len(self.alerts("low_stock")), 1)  # no duplicate

    def test_tc05_staff_blocked_from_admin_routes(self):
        c = self.login("staff")
        for method, url in (("post", "/products"), ("get", "/reports/audit-log")):
            r = getattr(c, method)(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login", r.headers["Location"])
        self.assertEqual(c.post("/products", json={}, headers=JSON).status_code, 403)
        self.assertEqual(self.login("admin").get("/reports/audit-log").status_code, 200)


class ExtraChecks(Base):
    def test_window_boundary_is_inclusive_at_90_seconds(self):
        t = datetime.now().replace(microsecond=0)
        inventory.record_transaction(self.db, self.pid, 1, "qr_scan", None, fmt(t))
        self.assertEqual(correlation.process_exit_event(self.db, self.pid, fmt(t + timedelta(seconds=90)))["outcome"], "legitimate")
        self.assertEqual(correlation.process_exit_event(self.db, self.pid, fmt(t + timedelta(seconds=91)))["outcome"], "alert")

    def test_late_sale_resolves_pending_alert(self):
        t = datetime.now().replace(microsecond=0)
        correlation.process_exit_event(self.db, self.pid, fmt(t))
        r = inventory.record_transaction(self.db, self.pid, 1, "qr_scan", None, fmt(t + timedelta(seconds=40)))
        self.assertEqual(len(r["resolved_alerts"]), 1)
        self.assertEqual(self.alerts()[0]["status"], "resolved")

    def test_camera_requires_key_or_login(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/camera/events", json={"product_id": 1}, headers=JSON).status_code, 401)
        ok = c.post("/camera/events", json={"product_id": 1}, headers={**JSON, "X-API-Key": "camkey"})
        self.assertEqual(ok.status_code, 201)

    def test_login_rejects_bad_password_and_validation(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/login", json={"username": "admin", "password": "nope"}).status_code, 401)
        a = self.login("admin")
        self.assertEqual(a.post("/products", json={"name": "x"}, headers=JSON).status_code, 400)
        good = {"name": "Milk", "sku": "M1", "category": "Dairy", "price": "350.50", "stock_level": 5, "reorder_point": 2}
        self.assertEqual(a.post("/products", json=good, headers=JSON).status_code, 201)
        self.assertEqual(a.post("/products", json=good, headers=JSON).status_code, 409)

    def test_cannot_delete_product_with_history(self):
        inventory.record_transaction(self.db, self.pid, 1, "qr_scan")
        self.assertEqual(self.login("admin").delete("/products/1", headers=JSON).status_code, 409)


class DetectorLogic(unittest.TestCase):
    ZONE = (0, 0, 100, 100)

    def test_exit_after_leaving_zone(self):
        t, events = ExitTracker(self.ZONE, 3, 3, 10), []
        for _ in range(4): events.append(t.update(1, "bottle", 50, 50))
        for _ in range(4): events.append(t.update(1, "bottle", 300, 50))
        fired = [e for e in events if e]
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["reason"], "left_zone")

    def test_vanishing_from_zone_counts_as_exit(self):
        t = ExitTracker(self.ZONE, 3, 3, 5)
        for _ in range(4): t.update(1, "cup", 20, 20)
        fired = []
        for _ in range(6): fired += t.end_frame(set())
        self.assertEqual([e["reason"] for e in fired], ["lost_in_zone"])

    def test_item_that_never_sat_on_shelf_is_ignored(self):
        t = ExitTracker(self.ZONE, 3, 3, 5)
        self.assertTrue(all(t.update(9, "bottle", 500, 500) is None for _ in range(10)))


class ReportNumbers(unittest.TestCase):
    def test_dataset_reproduces_report_tables(self):
        ds = simulation.generate_dataset()
        self.assertEqual((len(ds["products"]), len(ds["events"]), len(ds["transactions"])), (60, 420, 377))
        r = simulation.evaluate(ds)
        self.assertEqual((r["TP"], r["FP"], r["TN"], r["FN"]), (41, 24, 351, 4))
        self.assertEqual([round(r[k] * 100, 1) for k in ("accuracy", "precision", "recall", "f1", "false_alarm_rate")],
                         [93.3, 63.1, 91.1, 74.5, 36.9])

    def test_database_run_matches_pure_run_and_stock_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database({"DB_TYPE": "sqlite", "DB_PATH": os.path.join(tmp, "s.db")})
            db.init_schema()
            ds = simulation.generate_dataset()
            r = simulation.load_into_db(db, ds)
            db.close()
        self.assertEqual((r["TP"], r["FP"], r["TN"], r["FN"]), (41, 24, 351, 4))
        self.assertEqual(r["inventory_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
