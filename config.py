"""Configuration: reads environment variables, plus an optional .env file."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load():
    _load_dotenv()
    env = os.environ.get
    return {
        "SECRET_KEY": env("SECRET_KEY", "change-this-secret-key"),
        "CAMERA_API_KEY": env("CAMERA_API_KEY", ""),
        "DB_TYPE": env("DB_TYPE", "sqlite").lower(),
        "DB_PATH": env("DB_PATH", os.path.join(BASE_DIR, "retail.db")),
        "MYSQL_HOST": env("MYSQL_HOST", "127.0.0.1"),
        "MYSQL_PORT": int(env("MYSQL_PORT", "3306")),
        "MYSQL_USER": env("MYSQL_USER", "retail"),
        "MYSQL_PASSWORD": env("MYSQL_PASSWORD", ""),
        "MYSQL_DATABASE": env("MYSQL_DATABASE", "retail_guard"),
        "MATCH_WINDOW_SECONDS": int(env("MATCH_WINDOW_SECONDS", "90")),
        "SCHEDULER_SECONDS": int(env("SCHEDULER_SECONDS", "60")),
    }
