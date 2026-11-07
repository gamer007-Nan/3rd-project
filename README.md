# Retail Guard: AI-powered inventory management and anti-theft

Implements the project report (SCI22CSC138): a Flask web dashboard, a MySQL (or SQLite) database,
a YOLOv8 + OpenCV detector, and a **correlation engine** that only raises a theft alert when a camera
shelf-exit has **no matching sale within ±90 seconds**.

| Report item | File |
|---|---|
| Four tables (Appendix A) | `sql/schema_mysql.sql`, `sql/schema_sqlite.sql` |
| REST endpoints (Table C.2) | `app.py` |
| Inventory + reorder check (4.4.2) | `inventory.py` |
| Correlation rules (Table 4.3) | `correlation.py` |
| YOLOv8 / OpenCV pipeline (4.4.3) | `detector.py`, `class_map.json` |
| Synthetic dataset + metrics (4.5, 4.6.3) | `simulation.py` |
| Test cases TC-01 to TC-05 (Appendix B) | `tests/test_system.py` |
| Dashboard (3.5, 4.4.4) | `templates/`, `static/` |

## 1. Quick start (SQLite, about 2 minutes)

Needs Python 3.10 or newer.

```bash
cd ai-inventory-antitheft
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install Flask                 # enough for the dashboard
cp .env.example .env              # then edit SECRET_KEY and CAMERA_API_KEY
python manage.py seed-demo        # loads the report's dataset and prints its results
python app.py                     # open http://localhost:5000
```

In GitHub Codespaces, open the "Ports" tab and click the link for port 5000.

Demo logins (created by `seed-demo`): `admin / Admin#12345` and `staff / Staff#12345`.
**Change them** before real use. For an empty system instead of the demo:

```bash
python manage.py init-db
python manage.py create-user boss admin
python manage.py create-user till1 staff
```

## 2. Verify it matches the report

```bash
python -m unittest discover -s tests -v    # 15 tests, including TC-01..TC-05
python manage.py evaluate
```

Expected: TP 41, FP 24, TN 351, FN 4, accuracy 93.3%, precision 63.1%, recall 91.1%, F1 74.5%,
false-alarm rate 36.9%. `seed-demo` also shows inventory accuracy 100% (60 of 60).

## 3. Try it in the dashboard

1. Sign in as `staff`. On **Camera test**, pick a product and click *Simulate shelf exit*. With no sale, you get a theft alert.
2. On **Sales**, record a sale for a product, then simulate an exit for it. It is closed as a legitimate sale.
3. Log a sale *after* an alert was raised (within 90 s) and the alert resolves itself.
4. Sign in as `admin` to add or edit products, resolve alerts, and download the audit log (**Reports**).

## 4. Using MySQL (as in the report)

```bash
mysql -u root -p < sql/schema_mysql.sql      # creates database, user and tables
pip install PyMySQL cryptography
# in .env:  DB_TYPE=mysql   (and the MYSQL_* values)
python manage.py create-user boss admin
python app.py
```

## 5. Running the camera detector (needs internet once, for model weights)

```bash
pip install opencv-python ultralytics
# edit class_map.json so object names map to your product IDs
python detector.py --source 0 --key YOUR_CAMERA_API_KEY --zone 0.05,0.10,0.45,0.90 --show
# or a recorded video:  --source shelf.mp4
```

`--zone` is the shelf area as fractions of the frame (x1,y1,x2,y2). Use `--weights best.pt` for a
model fine-tuned on your own footage. On a CPU-only machine keep `--stride 3` or higher.
Stock COCO weights only know everyday objects (bottle, cup, book...), so real stores need fine-tuning.
Run `python detector.py` on the same machine as the server, or point `--api` at the server's address.

## 6. Notes and honest limits

- **One schema addition:** `alerts.alert_type` ('theft' or 'low_stock'), so low-stock alerts (TC-04) fit in the same table.
- **Extra endpoints** beyond Table C.2: `POST /camera/events` (detector input), `GET /transactions`, `GET /summary`, `POST /users`.
- Times use the server's local clock; keep the camera PC and server on the same time.
- Each sale can explain any exit inside its window, regardless of quantity. A store that sells many units of one product in a minute may want stricter matching.
- The 93.3% / 63.1% figures measure the correlation logic on synthetic data, not YOLOv8 accuracy or a real store, exactly as the report says. The dataset seed (9) was chosen so the generated data reproduces the report's confusion matrix.
- For real deployment: set strong `SECRET_KEY` and `CAMERA_API_KEY`, serve over HTTPS (for example `gunicorn` behind nginx), and follow the Nigeria Data Protection Act for video monitoring.
