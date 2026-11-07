"""Tiny database layer. SQLite by default, MySQL (PyMySQL) when DB_TYPE=mysql.
SQL is written with '?' placeholders; they are converted to '%s' for MySQL."""
import os
import sqlite3
from datetime import datetime
from decimal import Decimal

from werkzeug.security import generate_password_hash

DT_FMT = "%Y-%m-%d %H:%M:%S"
HERE = os.path.dirname(os.path.abspath(__file__))

IntegrityErrors = (sqlite3.IntegrityError,)
try:
    import pymysql
    IntegrityErrors = (sqlite3.IntegrityError, pymysql.err.IntegrityError)
except ImportError:  # MySQL driver is optional when using SQLite
    pymysql = None


def now():
    return datetime.now().replace(microsecond=0)


def fmt(dt):
    return dt.strftime(DT_FMT)


def parse(value):
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("T", " ").rstrip("Z")
    return datetime.strptime(text[:19], DT_FMT)


def _clean(row):
    out = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            value = fmt(value)
        elif isinstance(value, Decimal):
            value = float(value)
        out[key] = value
    return out


class Database:
    def __init__(self, cfg):
        self.kind = cfg["DB_TYPE"]
        if self.kind == "mysql":
            if pymysql is None:
                raise RuntimeError("DB_TYPE=mysql needs PyMySQL: pip install PyMySQL cryptography")
            self.conn = pymysql.connect(
                host=cfg["MYSQL_HOST"], port=cfg["MYSQL_PORT"], user=cfg["MYSQL_USER"],
                password=cfg["MYSQL_PASSWORD"], database=cfg["MYSQL_DATABASE"],
                charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)
        else:
            self.conn = sqlite3.connect(cfg["DB_PATH"], timeout=15)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.kind == "mysql" else sql

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def all(self, sql, params=()):
        return [_clean(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        row = self.execute(sql, params).fetchone()
        return _clean(row) if row else None

    def insert(self, sql, params=()):
        return self.execute(sql, params).lastrowid

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def init_schema(self):
        if self.kind == "mysql":
            path = os.path.join(HERE, "sql", "schema_mysql.sql")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            # The CREATE DATABASE/USER part needs admin rights; skip it here.
            text = text[text.index("CREATE TABLE"):]
            for stmt in [s.strip() for s in text.split(";") if s.strip()]:
                self.execute("\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")))
        else:
            path = os.path.join(HERE, "sql", "schema_sqlite.sql")
            with open(path, encoding="utf-8") as fh:
                self.conn.executescript(fh.read())
        self.commit()


def create_user(db, username, password, role="staff"):
    if role not in ("admin", "staff"):
        raise ValueError("role must be 'admin' or 'staff'")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    uid = db.insert("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                    (username, generate_password_hash(password), role))
    db.commit()
    return uid
