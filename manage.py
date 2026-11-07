#!/usr/bin/env python3
"""Command-line helper.

  python manage.py init-db                       create tables
  python manage.py create-user NAME ROLE         role = admin | staff (asks for a password)
  python manage.py seed-demo                     load the report's synthetic dataset and print its results
  python manage.py evaluate                      print the report's metrics (no database needed)
"""
import getpass
import sys

import config
import simulation
from db import Database, IntegrityErrors, create_user


def pct(x):
    return f"{x * 100:.1f}%"


def show(r):
    print(f"TP={r['TP']}  FP={r['FP']}  TN={r['TN']}  FN={r['FN']}")
    print(f"Accuracy {pct(r['accuracy'])} | Precision {pct(r['precision'])} | Recall {pct(r['recall'])} | "
          f"F1 {pct(r['f1'])} | False-alarm rate {pct(r['false_alarm_rate'])}")
    if "inventory_accuracy" in r:
        print(f"Inventory accuracy {pct(r['inventory_accuracy'])} ({r['products_matched']} products)")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cfg = config.load()
    cmd = argv[1]
    if cmd == "evaluate":
        show(simulation.evaluate(simulation.generate_dataset()))
        return 0
    db = Database(cfg)
    db.init_schema()
    if cmd == "init-db":
        print("Tables are ready.")
    elif cmd == "create-user":
        if len(argv) < 4:
            print("Usage: python manage.py create-user NAME admin|staff")
            return 1
        password = getpass.getpass("Password (8+ characters): ")
        try:
            create_user(db, argv[2], password, argv[3])
            print(f"Created {argv[3]} account '{argv[2]}'.")
        except IntegrityErrors:
            print("That username already exists.")
            return 1
        except ValueError as exc:
            print("Error:", exc)
            return 1
    elif cmd == "seed-demo":
        if db.one("SELECT COUNT(*) AS n FROM products")["n"]:
            print("The database already has products. Use a fresh database for the demo.")
            return 1
        ds = simulation.generate_dataset()
        print("Loading 60 products, 377 sales and 420 camera events through the real system code...")
        show(simulation.load_into_db(db, ds, cfg["MATCH_WINDOW_SECONDS"]))
        for name, pw, role in (("admin", "Admin#12345", "admin"), ("staff", "Staff#12345", "staff")):
            try:
                create_user(db, name, pw, role)
                print(f"Demo account: {name} / {pw}  (change these before real use)")
            except IntegrityErrors:
                db.rollback()
    else:
        print(__doc__)
        return 1
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
