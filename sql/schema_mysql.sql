-- Run once as a MySQL admin:  mysql -u root -p < sql/schema_mysql.sql
CREATE DATABASE IF NOT EXISTS retail_guard CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'retail'@'%' IDENTIFIED BY 'retail_password';
GRANT ALL PRIVILEGES ON retail_guard.* TO 'retail'@'%';
FLUSH PRIVILEGES;
USE retail_guard;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','staff') NOT NULL DEFAULT 'staff',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  sku VARCHAR(20) NOT NULL UNIQUE,
  category VARCHAR(50) NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  stock_level INT NOT NULL DEFAULT 0,
  reorder_point INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  quantity INT NOT NULL DEFAULT 1,
  source ENUM('qr_scan','pos_manual') NOT NULL,
  till_id VARCHAR(20) NULL,
  timestamp DATETIME NOT NULL,
  FOREIGN KEY (product_id) REFERENCES products(id),
  INDEX idx_txn_product_time (product_id, timestamp)
);
-- alert_type is the one addition to Appendix A. It lets the dashboard tell
-- theft alerts apart from the automatic low-stock alerts required by TC-04.
CREATE TABLE IF NOT EXISTS alerts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  alert_type ENUM('theft','low_stock') NOT NULL DEFAULT 'theft',
  camera_event_time DATETIME NOT NULL,
  matched_transaction_id INT NULL,
  status ENUM('unresolved','resolved','false_alarm') NOT NULL DEFAULT 'unresolved',
  reviewed_by INT NULL,
  reviewed_at DATETIME NULL,
  FOREIGN KEY (product_id) REFERENCES products(id),
  FOREIGN KEY (matched_transaction_id) REFERENCES transactions(id),
  FOREIGN KEY (reviewed_by) REFERENCES users(id),
  INDEX idx_alert_status (status, alert_type)
);
