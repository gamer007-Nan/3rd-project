CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin','staff')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  sku TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL,
  price NUMERIC NOT NULL,
  stock_level INTEGER NOT NULL DEFAULT 0,
  reorder_point INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL CHECK (source IN ('qr_scan','pos_manual')),
  till_id TEXT NULL,
  timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id),
  alert_type TEXT NOT NULL DEFAULT 'theft' CHECK (alert_type IN ('theft','low_stock')),
  camera_event_time TEXT NOT NULL,
  matched_transaction_id INTEGER NULL REFERENCES transactions(id),
  status TEXT NOT NULL DEFAULT 'unresolved' CHECK (status IN ('unresolved','resolved','false_alarm')),
  reviewed_by INTEGER NULL REFERENCES users(id),
  reviewed_at TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_txn_product_time ON transactions(product_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status, alert_type);
