# -*- coding: utf-8 -*-
"""
BusinessApp — Flask Backend
Toutes les routes sont ici : auth, profil, admin, ETL, KPI, prévisions, analytics.
Aucun Blueprint, aucun dossier routes/ nécessaire.
"""

import base64
import difflib
import gc
import gzip
import importlib.util as _ilu
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import time
import traceback
import unicodedata
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import numpy as np
import pandas as pd
import jwt
import requests
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except ImportError:
    Limiter = None

    def get_remote_address():
        return request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1")

from db import get_connection

warnings.filterwarnings("ignore")

_base = Path(__file__).resolve().parent
load_dotenv(_base / ".env")


def _env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name, default=None):
    raw = os.getenv(name)
    if raw is None:
        return list(default or [])
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]


APP_DEBUG = _env_flag("FLASK_DEBUG", False)
APP_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("FLASK_PORT", "5000"))
AUTH_RATE_LIMIT = os.getenv("AUTH_RATE_LIMIT", "5 per minute")
CONTACT_RATE_LIMIT = os.getenv("CONTACT_RATE_LIMIT", "3 per minute")
UPLOAD_RATE_LIMIT = os.getenv("UPLOAD_RATE_LIMIT", "10 per minute")
DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:5000",
    "http://localhost:5000",
]


def _resolve_secret_key():
    secret = os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET_KEY")
    if secret:
        return secret
    if APP_DEBUG:
        warnings.warn("SECRET_KEY not set; using development fallback secret.")
        return "finova-dev-secret-key"
    generated = secrets.token_urlsafe(48)
    warnings.warn("SECRET_KEY not set; using an ephemeral secret for this process.")
    return generated

# ═══════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__)
if Limiter is not None:
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[],
        storage_uri=os.getenv("RATE_LIMIT_STORAGE_URI", "memory://"),
    )
else:
    class _NoopLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator

    limiter = _NoopLimiter()
    warnings.warn("flask-limiter is not installed; rate limiting is disabled.")
app.secret_key = _resolve_secret_key()
app.config["PROPAGATE_EXCEPTIONS"] = False
app.config["TRAP_HTTP_EXCEPTIONS"] = True


@app.errorhandler(Exception)
def _handle_unhandled_exception(error):
    if isinstance(error, HTTPException):
        return jsonify({"success": False, "error": str(error.description or error.name)}), error.code
    app.logger.exception("Unhandled exception")
    return jsonify({"success": False, "error": "Erreur interne du serveur"}), 500
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY") or app.secret_key
JWT_SECRET = app.config["JWT_SECRET_KEY"]
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_HOURS = 24  # Extended from 2 to 24 hours
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:latest")
SYSTEM_PROMPT = """Tu es l’assistant BI du projet Finova.
Réponds en français, clairement et sans jargon inutile.
Utilise uniquement le contexte financier fourni.
N’invente jamais de chiffres si les données ne sont pas disponibles.
Si l’utilisateur demande les transactions, réponds avec le nombre réel de transactions et une explication liée à la base pfe_bd.
Tu peux aider sur les revenus, dépenses, solde net, KPI, import CSV, traitement ETL, prévisions et simulations What-If."""

CORS_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGINS)
CORS(app,
     resources={r"/api/*": {"origins": CORS_ORIGINS}},
     supports_credentials=False,
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

from routes.auth import create_auth_blueprint
from routes.admin import create_admin_blueprint
from routes.analytics import create_analytics_blueprint
from routes.chatbot import create_chatbot_blueprint
from routes.contact import create_contact_blueprint
from routes.debug import create_debug_blueprint
from routes.etl import create_etl_blueprint
from routes.finance import create_finance_blueprint
from routes.ml_assistance import create_ml_assistance_blueprint
from routes.reports import create_reports_blueprint
from routes.static_pages import create_static_blueprint

FRONTEND = next(
    (p for p in [_base.parent / "frontend", _base / "frontend", _base]
     if p.exists() and (p / "dash.html").exists()),
    _base.parent / "frontend",
)

UPLOAD_FOLDER = _base / "uploads_etl"
UPLOAD_FOLDER.mkdir(exist_ok=True)
REPORTS_FOLDER = _base / "reports_exports"
REPORTS_FOLDER.mkdir(exist_ok=True)

app.register_blueprint(create_contact_blueprint(limiter, CONTACT_RATE_LIMIT))


def _safe_upload_path(filename):
    safe_name = re.sub(r"[^\w._-]", "_", filename or "")
    safe_name = Path(safe_name).name
    if not safe_name:
        raise ValueError("Nom de fichier invalide")
    resolved = (UPLOAD_FOLDER / safe_name).resolve()
    upload_root = UPLOAD_FOLDER.resolve()
    if resolved.parent != upload_root:
        raise ValueError("Chemin de fichier invalide")
    return safe_name, resolved


def _internal_error_response(message="Une erreur interne est survenue.", status=500, success=False):
    payload = {"error": message}
    if success is not None:
        payload["success"] = success
    return jsonify(payload), status

def _is_strong_password(password):
    if not password or len(password) < 8:
        return False
    has_upper = any(ch.isupper() for ch in password)
    has_lower = any(ch.islower() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    has_symbol = any(not ch.isalnum() for ch in password)
    return has_upper and has_lower and has_digit and has_symbol

# ETL engine — chargé une seule fois au démarrage
_spec = _ilu.spec_from_file_location("etl_generic", str(_base / "etl_generic.py"))
_etl_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_etl_mod)
run_generic_etl = _etl_mod.run_generic_etl

# ═══════════════════════════════════════════════════════════════
# DB HELPERS
# ═══════════════════════════════════════════════════════════════
def run_query(query, params=None, fetch_one=False):
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor(dictionary=True)
        cur.execute(query, params or ())
        result = cur.fetchone() if fetch_one else cur.fetchall()
        cur.close()
        return result
    finally:
        if conn:
            conn.close()


def run_update(query, params=None):
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(query, params or ())
        conn.commit()
        last_id = cur.lastrowid
        cur.close()
        return last_id
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════
# JSON SAFETY & COMPRESSION
# ═══════════════════════════════════════════════════════════════
def _decode_text(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8")
        except Exception:
            return raw.decode("latin-1", errors="ignore")
    return str(raw)


def make_json_safe(value):
    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return make_json_safe(value.item())
        except Exception:
            return str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def compress_payload(payload) -> str:
    raw = json.dumps(make_json_safe(payload), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, compresslevel=9)).decode("ascii")


def decompress_payload(text: str):
    if not text:
        return None
    try:
        return json.loads(gzip.decompress(base64.b64decode(text.encode("ascii"))).decode("utf-8"))
    except Exception:
        return None


def _extract_rows_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("rows", "data", "after_rows", "imported_rows", "transactions", "records"):
            rows = value.get(key)
            if isinstance(rows, list):
                return rows
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        return _extract_rows_list(parsed)
    return []


# ═══════════════════════════════════════════════════════════════
# AUTH HELPER
# ═══════════════════════════════════════════════════════════════
def _extract_bearer_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    return token or None


def create_access_token(user: dict) -> str:
    now = datetime.utcnow()
    user_id = int(user["id"])
    payload = {
        "sub": str(user_id),
        "id": user_id,
        "email": (user.get("email") or "").strip().lower(),
        "role": (user.get("role") or "user").strip().lower(),
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRES_HOURS),
    }
    encoded = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded.decode("utf-8") if isinstance(encoded, bytes) else encoded


def decode_access_token(token: str | None = None) -> dict | None:
    raw = token or _extract_bearer_token()
    if not raw:
        return None
    try:
        return jwt.decode(raw, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _load_user_from_payload(payload: dict | None) -> dict | None:
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub") or payload.get("id") or 0)
    except Exception:
        return None
    if not user_id:
        return None
    return run_query(
        "SELECT id,firstname,lastname,email,COALESCE(role,'user') AS role FROM users WHERE id=%s",
        (user_id,),
        True,
    )


def get_current_user() -> int | None:
    current = getattr(g, "current_user", None)
    if isinstance(current, dict) and current.get("id"):
        try:
            return int(current["id"])
        except Exception:
            return None
    token = _extract_bearer_token()
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    try:
        return int(payload.get("sub") or payload.get("id") or 0) or None
    except Exception:
        return None


def jwt_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        raw = _extract_bearer_token()
        if not raw:
            return jsonify({"success": False, "error": "Auth requis"}), 401
        payload = decode_access_token(raw)
        if not payload:
            return jsonify({"success": False, "error": "Token invalide ou expire"}), 401
        user = _load_user_from_payload(payload)
        if not user:
            return jsonify({"success": False, "error": "Utilisateur introuvable"}), 401
        g.jwt_payload = payload
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        raw = _extract_bearer_token()
        if not raw:
            return jsonify({"success": False, "error": "Auth requis"}), 401
        payload = decode_access_token(raw)
        if not payload:
            return jsonify({"success": False, "error": "Token invalide ou expire"}), 401
        user = _load_user_from_payload(payload)
        if not user:
            return jsonify({"success": False, "error": "Utilisateur introuvable"}), 401
        if (user.get("role") or "user").strip().lower() != "admin":
            return jsonify({"success": False, "error": "Acces interdit"}), 403
        g.jwt_payload = payload
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapper


def require_auth(fn):
    return jwt_required(fn)


def require_admin(fn):
    return admin_required(fn)


def require_role(roles):
    allowed = {str(r or "").strip().lower() for r in (roles or []) if str(r or "").strip()}
    if not allowed:
        allowed = {"admin"}

    def decorator(fn):
        @jwt_required
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None) or {}
            role = str(user.get("role") or "user").strip().lower()
            if role not in allowed:
                return jsonify({"success": False, "error": "Acces interdit"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def get_user_display_name(user_id) -> str | None:
    try:
        row = run_query("SELECT COALESCE(firstname, email) AS n FROM users WHERE id=%s", (user_id,), True)
        return row["n"] if row else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# HISTORIQUE IMPORTS — save helper
# ═══════════════════════════════════════════════════════════════
def _write_longtext_chunks(conn, import_id: int, field: str, text: str, chunk=700_000):
    """Écrit un texte volumineux en plusieurs UPDATE pour éviter max_allowed_packet."""
    cur = conn.cursor()
    try:
        for i in range(0, len(text), chunk):
            cur.execute(
                f"UPDATE historique_imports SET {field}=CONCAT(COALESCE({field},''),%s) WHERE id=%s",
                (text[i:i + chunk], import_id),
            )
        conn.commit()
    finally:
        cur.close()


def _table_columns(cur, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    return {str(r[0]) for r in cur.fetchall()}


def ensure_login_history_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS login_history (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            user_id        INT NULL,
            email          VARCHAR(255) NOT NULL,
            login_status   VARCHAR(50)  NOT NULL,
            login_type     VARCHAR(50)  NOT NULL DEFAULT 'email',
            ip_address     VARCHAR(100) NULL,
            user_agent     TEXT NULL,
            login_date     DATETIME     DEFAULT CURRENT_TIMESTAMP,
            failure_reason VARCHAR(255) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    existing = _table_columns(cur, "login_history")
    missing = []
    if "user_id" not in existing:
        missing.append("ADD COLUMN user_id INT NULL")
    if "email" not in existing:
        missing.append("ADD COLUMN email VARCHAR(255) NOT NULL DEFAULT ''")
    if "login_status" not in existing:
        missing.append("ADD COLUMN login_status VARCHAR(50) NOT NULL DEFAULT 'failed'")
    if "login_type" not in existing:
        missing.append("ADD COLUMN login_type VARCHAR(50) NOT NULL DEFAULT 'email'")
    if "ip_address" not in existing:
        missing.append("ADD COLUMN ip_address VARCHAR(100) NULL")
    if "user_agent" not in existing:
        missing.append("ADD COLUMN user_agent TEXT NULL")
    if "login_date" not in existing:
        missing.append("ADD COLUMN login_date DATETIME DEFAULT CURRENT_TIMESTAMP")
    if "failure_reason" not in existing:
        missing.append("ADD COLUMN failure_reason VARCHAR(255) NULL")

    if missing:
        cur.execute(f"ALTER TABLE login_history {', '.join(missing)}")

    # Optional indexes for admin filtering/sorting.
    try:
        cur.execute("SHOW INDEX FROM login_history WHERE Key_name = 'idx_login_history_date'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_login_history_date ON login_history(login_date, id)")
    except Exception:
        pass
    try:
        cur.execute("SHOW INDEX FROM login_history WHERE Key_name = 'idx_login_history_email'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_login_history_email ON login_history(email)")
    except Exception:
        pass
    try:
        cur.execute("SHOW INDEX FROM login_history WHERE Key_name = 'idx_login_history_status'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_login_history_status ON login_history(login_status)")
    except Exception:
        pass
    try:
        cur.execute("SHOW INDEX FROM login_history WHERE Key_name = 'idx_login_history_user'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_login_history_user ON login_history(user_id)")
    except Exception:
        pass

    # Add FK if possible (skip if DB already has incompatible data).
    try:
        cur.execute(
            """
            SELECT CONSTRAINT_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'login_history'
              AND COLUMN_NAME = 'user_id'
              AND REFERENCED_TABLE_NAME = 'users'
            LIMIT 1
            """
        )
        has_fk = cur.fetchone()
        if not has_fk:
            cur.execute(
                """
                ALTER TABLE login_history
                ADD CONSTRAINT fk_login_history_user
                FOREIGN KEY (user_id) REFERENCES users(id)
                ON DELETE SET NULL ON UPDATE CASCADE
                """
            )
    except Exception:
        pass


def ensure_audit_logs_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id_log      INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT NULL,
            action      VARCHAR(100) NOT NULL,
            ressource   VARCHAR(150) NULL,
            statut      VARCHAR(30)  DEFAULT 'success',
            ip_address  VARCHAR(100) NULL,
            details     TEXT NULL,
            date_action DATETIME     DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    existing = _table_columns(cur, "audit_logs")
    missing = []
    if "user_id" not in existing:
        missing.append("ADD COLUMN user_id INT NULL")
    if "action" not in existing:
        missing.append("ADD COLUMN action VARCHAR(100) NOT NULL DEFAULT 'erreur_systeme'")
    if "ressource" not in existing:
        missing.append("ADD COLUMN ressource VARCHAR(150) NULL")
    if "statut" not in existing:
        missing.append("ADD COLUMN statut VARCHAR(30) DEFAULT 'success'")
    if "ip_address" not in existing:
        missing.append("ADD COLUMN ip_address VARCHAR(100) NULL")
    if "details" not in existing:
        missing.append("ADD COLUMN details TEXT NULL")
    if "date_action" not in existing:
        missing.append("ADD COLUMN date_action DATETIME DEFAULT CURRENT_TIMESTAMP")
    if missing:
        cur.execute(f"ALTER TABLE audit_logs {', '.join(missing)}")

    try:
        cur.execute("SHOW INDEX FROM audit_logs WHERE Key_name = 'idx_audit_date'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_audit_date ON audit_logs(date_action, id_log)")
    except Exception:
        pass
    try:
        cur.execute("SHOW INDEX FROM audit_logs WHERE Key_name = 'idx_audit_action'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_audit_action ON audit_logs(action)")
    except Exception:
        pass
    try:
        cur.execute("SHOW INDEX FROM audit_logs WHERE Key_name = 'idx_audit_status'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_audit_status ON audit_logs(statut)")
    except Exception:
        pass
    try:
        cur.execute("SHOW INDEX FROM audit_logs WHERE Key_name = 'idx_audit_user'")
        if not cur.fetchall():
            cur.execute("CREATE INDEX idx_audit_user ON audit_logs(user_id)")
    except Exception:
        pass


def _table_exists_in_current_db(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    row = cur.fetchone()
    if isinstance(row, dict):
        return bool(row.get("n"))
    if isinstance(row, (list, tuple)) and row:
        return bool(row[0])
    return False


def _index_exists(cur, table_name: str, index_name: str) -> bool:
    try:
        cur.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = %s", (index_name,))
        return bool(cur.fetchall())
    except Exception:
        return False


def _ensure_index(cur, table_name: str, index_name: str, columns_sql: str):
    if not _table_exists_in_current_db(cur, table_name):
        return
    if _index_exists(cur, table_name, index_name):
        return
    try:
        cur.execute(f"CREATE INDEX `{index_name}` ON `{table_name}` ({columns_sql})")
    except Exception:
        pass


def ensure_rapports_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rapports (
            id_rapport       INT AUTO_INCREMENT PRIMARY KEY,
            nom_rapport      VARCHAR(255) NOT NULL,
            type_rapport     VARCHAR(100) DEFAULT 'financier',
            format           VARCHAR(20)  NOT NULL,
            chemin_fichier   VARCHAR(500) NULL,
            created_by       INT NULL,
            date_generation  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    existing = _table_columns(cur, "rapports")
    missing = []
    if "nom_rapport" not in existing:
        missing.append("ADD COLUMN nom_rapport VARCHAR(255) NOT NULL DEFAULT 'Rapport'")
    if "type_rapport" not in existing:
        missing.append("ADD COLUMN type_rapport VARCHAR(100) DEFAULT 'financier'")
    if "format" not in existing:
        missing.append("ADD COLUMN format VARCHAR(20) NOT NULL DEFAULT 'pdf'")
    if "chemin_fichier" not in existing:
        missing.append("ADD COLUMN chemin_fichier VARCHAR(500) NULL")
    if "created_by" not in existing:
        missing.append("ADD COLUMN created_by INT NULL")
    if "date_generation" not in existing:
        missing.append("ADD COLUMN date_generation DATETIME DEFAULT CURRENT_TIMESTAMP")
    if missing:
        try:
            cur.execute(f"ALTER TABLE rapports {', '.join(missing)}")
        except Exception:
            pass

    _ensure_index(cur, "rapports", "idx_rapports_created_by", "created_by")
    _ensure_index(cur, "rapports", "idx_rapports_date", "date_generation")
    _ensure_index(cur, "rapports", "idx_rapports_format", "format")


def ensure_performance_indexes(cur):
    # rapports / audit logs (idempotent)
    _ensure_index(cur, "rapports", "idx_rapports_created_by", "created_by")
    _ensure_index(cur, "rapports", "idx_rapports_date", "date_generation")
    _ensure_index(cur, "rapports", "idx_rapports_format", "format")
    _ensure_index(cur, "audit_logs", "idx_audit_user", "user_id")
    _ensure_index(cur, "audit_logs", "idx_audit_action", "action")
    _ensure_index(cur, "audit_logs", "idx_audit_statut", "statut")
    _ensure_index(cur, "audit_logs", "idx_audit_date", "date_action")

    # transactions table can vary by dataset; create only on existing columns.
    if _table_exists_in_current_db(cur, "transactions"):
        tx_cols = _table_columns(cur, "transactions")
        if "Date_ID" in tx_cols:
            _ensure_index(cur, "transactions", "idx_tx_date_id", "Date_ID")
        if "Departement_ID" in tx_cols:
            _ensure_index(cur, "transactions", "idx_tx_departement_id", "Departement_ID")
        if "Date_ID" in tx_cols and "Departement_ID" in tx_cols:
            _ensure_index(cur, "transactions", "idx_tx_date_dep", "Date_ID, Departement_ID")
        if "Montant_Signe" in tx_cols:
            _ensure_index(cur, "transactions", "idx_tx_montant_signe", "Montant_Signe")
        if "Transaction_ID" in tx_cols:
            _ensure_index(cur, "transactions", "idx_tx_transaction_id", "Transaction_ID")

    # valeur_kpi table indexes.
    if _table_exists_in_current_db(cur, "valeur_kpi"):
        kpi_cols = _table_columns(cur, "valeur_kpi")
        if "kpiNom" in kpi_cols:
            _ensure_index(cur, "valeur_kpi", "idx_vkpi_nom", "kpiNom")
        if "periode" in kpi_cols:
            _ensure_index(cur, "valeur_kpi", "idx_vkpi_periode", "periode")
        if "updated_at" in kpi_cols:
            _ensure_index(cur, "valeur_kpi", "idx_vkpi_updated_at", "updated_at")
        if "created_at" in kpi_cols:
            _ensure_index(cur, "valeur_kpi", "idx_vkpi_created_at", "created_at")

    # date and departement dimensions if present.
    if _table_exists_in_current_db(cur, "date"):
        date_cols = _table_columns(cur, "date")
        if "Date" in date_cols:
            _ensure_index(cur, "date", "idx_date_value", "`Date`")
        if "Date_ID" in date_cols:
            _ensure_index(cur, "date", "idx_date_id", "Date_ID")
    if _table_exists_in_current_db(cur, "departement"):
        dep_cols = _table_columns(cur, "departement")
        if "Departement_ID" in dep_cols:
            _ensure_index(cur, "departement", "idx_dep_id", "Departement_ID")
        if "NomDepartement" in dep_cols:
            _ensure_index(cur, "departement", "idx_dep_name", "NomDepartement")


def _client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    xri = (request.headers.get("X-Real-IP") or "").strip()
    rip = (request.remote_addr or "").strip()
    return (xff or xri or rip or "")[:100]


def save_login_history(user_id, email, status, login_type, failure_reason=None):
    status = (status or "").strip().lower()
    if status not in {"success", "failed"}:
        status = "failed"

    login_type = (login_type or "email").strip().lower() or "email"
    normalized_email = (email or "").strip().lower()
    reason = (failure_reason or "").strip() or None
    user_agent = (request.headers.get("User-Agent") or "")[:4000]

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        ensure_login_history_schema(cur)
        conn.commit()
        cur.execute(
            """
            INSERT INTO login_history
                (user_id, email, login_status, login_type, ip_address, user_agent, login_date, failure_reason)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
            """,
            (user_id, normalized_email, status, login_type, _client_ip(), user_agent, reason),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[LOGIN_HISTORY] WARN: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            conn.close()


def _normalize_audit_status(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"success", "succes", "ok"}:
        return "success"
    if raw in {"failed", "failure", "echec", "error"}:
        return "failed"
    return "success"


def _stringify_audit_details(details) -> str | None:
    if details is None:
        return None
    if isinstance(details, str):
        val = details.strip()
        return val[:2000] if val else None
    try:
        val = json.dumps(details, ensure_ascii=False)
        return val[:2000] if val else None
    except Exception:
        try:
            val = str(details).strip()
            return val[:2000] if val else None
        except Exception:
            return None


def log_audit(user_id, action, ressource, statut="success", details=None, ip_address=None):
    conn = None
    try:
        norm_action = (action or "erreur_systeme").strip().lower()[:100]
        norm_resource = (ressource or "").strip()[:150] or None
        norm_status = _normalize_audit_status(statut)
        norm_details = _stringify_audit_details(details)
        norm_ip = (ip_address or _client_ip() or "")[:100] or None

        conn = get_connection()
        cur = conn.cursor()
        ensure_audit_logs_schema(cur)
        conn.commit()
        cur.execute(
            """
            INSERT INTO audit_logs
                (user_id, action, ressource, statut, ip_address, details, date_action)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (user_id, norm_action, norm_resource, norm_status, norm_ip, norm_details),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[AUDIT_LOG] WARN: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            conn.close()


def ensure_historique_imports_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS historique_imports (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT DEFAULT NULL,
            nom_fichier VARCHAR(255)  NOT NULL,
            date_import DATETIME     DEFAULT CURRENT_TIMESTAMP,
            nb_lignes   INT          DEFAULT 0,
            nb_erreurs  INT          DEFAULT 0,
            statut      VARCHAR(20)  NOT NULL DEFAULT 'succes',
            departement VARCHAR(255) DEFAULT NULL,
            importe_par VARCHAR(255) DEFAULT NULL,
            details     LONGTEXT     DEFAULT NULL,
            data        LONGTEXT     DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    existing = _table_columns(cur, "historique_imports")
    missing = []
    if "user_id" not in existing:
        missing.append("ADD COLUMN user_id INT DEFAULT NULL")
    if "nom_fichier" not in existing:
        missing.append("ADD COLUMN nom_fichier VARCHAR(255) NOT NULL DEFAULT ''")
    if "date_import" not in existing:
        missing.append("ADD COLUMN date_import DATETIME DEFAULT CURRENT_TIMESTAMP")
    if "nb_lignes" not in existing:
        missing.append("ADD COLUMN nb_lignes INT DEFAULT 0")
    if "nb_erreurs" not in existing:
        missing.append("ADD COLUMN nb_erreurs INT DEFAULT 0")
    if "statut" not in existing:
        missing.append("ADD COLUMN statut VARCHAR(20) NOT NULL DEFAULT 'succes'")
    if "departement" not in existing:
        missing.append("ADD COLUMN departement VARCHAR(255) DEFAULT NULL")
    if "importe_par" not in existing:
        missing.append("ADD COLUMN importe_par VARCHAR(255) DEFAULT NULL")
    if "details" not in existing:
        missing.append("ADD COLUMN details LONGTEXT NULL")
    if "data" not in existing:
        missing.append("ADD COLUMN data LONGTEXT NULL")

    if missing:
        cur.execute(f"ALTER TABLE historique_imports {', '.join(missing)}")

    # Legacy compatibility: enforce text payload columns.
    cur.execute("ALTER TABLE historique_imports MODIFY COLUMN details LONGTEXT NULL")
    cur.execute("ALTER TABLE historique_imports MODIFY COLUMN data LONGTEXT NULL")
    cur.execute("ALTER TABLE historique_imports MODIFY COLUMN statut VARCHAR(20) NOT NULL DEFAULT 'succes'")

    try:
        cur.execute("SHOW INDEX FROM historique_imports WHERE Key_name = 'idx_hist_user_status_date'")
        idx_rows = cur.fetchall()
        if not idx_rows:
            cur.execute("CREATE INDEX idx_hist_user_status_date ON historique_imports(user_id, statut, date_import, id)")
    except Exception:
        # Optional index (performance only)
        pass


def save_import_history(user_id, filename, stats=None, log=None, cleaned_data=None, success=True):
    stats, log, cleaned_data = stats or {}, log or [], cleaned_data or []
    cleaned_count = len(cleaned_data) if isinstance(cleaned_data, list) else 0
    nb_lignes   = int(stats.get("lignes", cleaned_count) or 0)
    nb_erreurs  = int(stats.get("nb_erreurs", 0) or 0)
    departement = stats.get("departement")
    importe_par = get_user_display_name(user_id) if user_id else None
    statut      = "succes" if success else "echec"

    if success and (nb_lignes <= 0 or cleaned_count <= 0):
        raise ValueError("Import invalide: aucune donnée nettoyée exploitable à sauvegarder.")

    details_json = json.dumps({"compressed": True, "format": "gzip+base64+json",
                               "content": compress_payload(log)}, ensure_ascii=False)
    data_json    = json.dumps({"compressed": True, "format": "gzip+base64+json",
                               "total_rows": nb_lignes,
                               "content": compress_payload(cleaned_data)}, ensure_ascii=False)
    conn = None
    try:
        conn = get_connection()
        try:
            conn.ping(reconnect=True, attempts=3, delay=2)
        except Exception:
            pass
        cur = conn.cursor()
        ensure_historique_imports_schema(cur)
        conn.commit()
        cur.execute(
            """INSERT INTO historique_imports
               (user_id,nom_fichier,date_import,nb_lignes,nb_erreurs,statut,departement,importe_par,details,data)
               VALUES (%s,%s,NOW(),%s,%s,%s,%s,%s,'','')""",
            (user_id, filename, nb_lignes, nb_erreurs, statut, departement, importe_par),
        )
        import_id = cur.lastrowid
        conn.commit()
        cur.close()
        _write_longtext_chunks(conn, import_id, "details", details_json)
        _write_longtext_chunks(conn, import_id, "data",    data_json)
        print(f"[HISTO] OK id={import_id} file={filename} rows={nb_lignes} status={statut}")
        return import_id
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"[HISTO] FAILED: {e}")
        raise
    finally:
        if conn:
            conn.close()


def _no_import_response():
    return jsonify({
        "success": False,
        "error": "Aucun import disponible",
        "message": "Veuillez importer un fichier depuis la page Data Import."
    }), 404


def _to_float(value, default=0.0):
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def _safe_report_stem(text: str, fallback: str = "rapport") -> str:
    raw = re.sub(r"[^\w\s.-]", "_", str(text or "").strip(), flags=re.UNICODE)
    raw = re.sub(r"\s+", "_", raw).strip("._")
    return raw[:120] if raw else fallback


def _normalize_report_format(value: str) -> str:
    fmt = str(value or "").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt in {"pdf", "png", "jpg", "csv", "docx"}:
        return fmt
    return ""


def _decode_export_file_payload(file_base64: str | None, fallback_text: str = "") -> bytes:
    raw = (file_base64 or "").strip()
    if not raw:
        return fallback_text.encode("utf-8")
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:
        return fallback_text.encode("utf-8")


def _get_user_by_id(user_id: int | None) -> dict | None:
    try:
        if not user_id:
            return None
        return run_query(
            "SELECT id,firstname,lastname,email,COALESCE(role,'user') AS role FROM users WHERE id=%s",
            (user_id,),
            True,
        )
    except Exception:
        return None


def _normalize_period(year, month):
    y = _to_int(year, 0)
    m = _to_int(month, 0)
    if y > 0 and 1 <= m <= 12:
        return f"{y}-{m:02d}"
    return ""


def decode_import_data_value(raw):
    """Decode historical import payload and return normalized rows list."""
    if raw is None:
        return []

    parsed = raw
    if isinstance(raw, (bytes, bytearray, memoryview)):
        parsed = _decode_text(raw)

    if isinstance(parsed, str):
        text = parsed.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return _extract_rows_list(text)

    if isinstance(parsed, dict):
        if parsed.get("compressed"):
            content = decompress_payload(parsed.get("content"))
            rows = _extract_rows_list(content)
            if rows:
                return rows
            # Legacy: compressed payload can be a direct JSON list string
            if isinstance(content, str):
                return _extract_rows_list(content)
            return []
        return _extract_rows_list(parsed)

    rows = _extract_rows_list(parsed)
    if rows:
        return rows

    return []


def decode_import_rows(raw):
    return decode_import_data_value(raw)


def _extract_import_rows_from_history(data_raw: str):
    return decode_import_rows(data_raw)


def _clean_label_value(value, default="Non renseigne", keep_na=False):
    text = str(value or "").strip()
    if not text:
        return default
    normalized = text.lower()
    if normalized in {"n/a", "na", "null", "none", "inconnu", "unknown", "non renseigne", "non renseigné", "unspecified", "-"}:
        if keep_na and normalized in {"n/a", "na"}:
            return "Non classée (N/A)"
        return default
    return text


def _normalize_transaction_label(value, signed_value=None):
    raw = str(value or "").strip()
    norm = unicodedata.normalize("NFKD", raw)
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch)).strip().lower()
    revenue_tokens = ("revenu", "recette", "income", "revenue", "vente", "credit", "encaissement", "entree", "in")
    expense_tokens = ("depense", "expense", "charge", "achat", "debit", "sortie", "out", "cout")
    if any(token in norm for token in revenue_tokens):
        return "Revenu"
    if any(token in norm for token in expense_tokens):
        return "Depense"
    signed = _to_float(signed_value, 0.0)
    if signed < 0:
        return "Depense"
    if signed > 0:
        return "Revenu"
    return "Revenu"


def normalize_import_row(row, idx=1):
    row = row or {}

    date_val = row.get("date_val") or row.get("Date")
    date_obj = pd.to_datetime(date_val, errors="coerce")

    year = row.get("annee", row.get("Année"))
    month = row.get("mois", row.get("Mois"))
    quarter = row.get("trimestre", row.get("Trimestre"))
    year_month = row.get("year_month") or row.get("YearMonth") or _normalize_period(year, month)

    montant = abs(_to_float(row.get("Montant"), 0.0))
    signed = _to_float(row.get("Montant_Signe"), 0.0)
    tx_raw = row.get("type_transaction") or row.get("TypeTransaction")
    tx_norm = unicodedata.normalize("NFKD", str(tx_raw or ""))
    tx_norm = "".join(ch for ch in tx_norm if not unicodedata.combining(ch)).strip().lower()
    if any(token in tx_norm for token in ("depense", "charge", "expense", "debit", "sortie", "out")):
        signed = -abs(montant) if signed == 0 else signed
    elif any(token in tx_norm for token in ("revenu", "income", "credit", "encaissement", "entree", "in")):
        signed = abs(montant) if signed == 0 else signed
    elif signed == 0:
        signed = montant
    tx_label = _normalize_transaction_label(tx_raw, signed)

    y_int = _to_int(year, 0)
    m_int = _to_int(month, 0)
    q_int = _to_int(quarter, 0)
    weekday = None
    if pd.notna(date_obj):
        if y_int == 0:
            y_int = int(date_obj.year)
        if m_int == 0:
            m_int = int(date_obj.month)
        if q_int == 0:
            q_int = int(date_obj.quarter)
        if not year_month:
            year_month = date_obj.strftime("%Y-%m")
        weekday = date_obj.day_name()

    departement = _clean_label_value(row.get("departement") or row.get("Département"))
    type_depense = _clean_label_value(
        row.get("type_depense") or row.get("TypeDépense"),
        default="N/A",
        keep_na=False,
    )
    responsable = _clean_label_value(row.get("responsable") or row.get("Responsable"))
    client_fournisseur = _clean_label_value(row.get("client_fournisseur") or row.get("Client_Fournisseur"))
    projet = _clean_label_value(row.get("projet") or row.get("Projet"), default="Sans projet")
    departement_id = row.get("departement_id", row.get("Departement_ID"))
    departement_id = row.get("DepartementID", departement_id)
    departement_id = _to_int(departement_id, 0) or None

    cf_type = str(row.get("cf_type") or "").strip()
    if not cf_type or cf_type.lower() in ("none", "null", ""):
        cf_type = "Client" if signed >= 0 else "Fournisseur"

    date_iso = date_obj.strftime("%Y-%m-%d") if pd.notna(date_obj) else None
    normalized = {
        "Transaction_ID": _to_int(row.get("Transaction_ID"), idx),
        "Montant": round(float(montant), 3),
        "Montant_Signe": round(float(signed), 3),
        "date_val": date_iso,
        "annee": y_int if y_int > 0 else None,
        "mois": m_int if m_int > 0 else None,
        "trimestre": q_int if q_int > 0 else None,
        "year_month": year_month or "",
        "departement": str(departement),
        "departement_id": departement_id,
        "type_transaction": tx_label,
        "type_depense": _clean_label_value(type_depense, default="N/A", keep_na=False),
        "responsable": _clean_label_value(responsable),
        "client_fournisseur": _clean_label_value(client_fournisseur),
        "cf_type": str(cf_type),
        "projet": _clean_label_value(projet, default="Sans projet"),
    }
    normalized["Date"] = normalized["date_val"]
    normalized["Département"] = normalized["departement"]
    normalized["DepartementID"] = normalized["departement_id"]
    normalized["TypeTransaction"] = "Dépense" if normalized["type_transaction"] == "Depense" else normalized["type_transaction"]
    normalized["TypeDépense"] = normalized["type_depense"]
    normalized["Responsable"] = normalized["responsable"]
    normalized["Client_Fournisseur"] = normalized["client_fournisseur"]
    normalized["Projet"] = normalized["projet"]
    normalized["Année"] = normalized["annee"]
    normalized["Mois"] = normalized["mois"]
    normalized["Trimestre"] = normalized["trimestre"]
    normalized["YearMonth"] = normalized["year_month"]
    normalized["AnnéeFiscale"] = normalized["annee"]
    normalized["JourSemaine"] = weekday
    return normalized


def _normalize_import_row(row, idx=1):
    return normalize_import_row(row, idx=1)


def is_success_status(status):
    value = str(status or "").strip().lower()
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(ch for ch in ascii_value if not unicodedata.combining(ch))
    return ascii_value in {"success", "succes", "reussi", "ok", "termine"}


def get_last_successful_import(user_id):
    if not user_id:
        return None

    rows = run_query(
        """
        SELECT id, user_id, nom_fichier, date_import, nb_lignes, statut, details, data, CHAR_LENGTH(data) AS data_length
        FROM historique_imports
        WHERE user_id=%s
            AND COALESCE(nb_lignes, 0) > 0
        ORDER BY date_import DESC, id DESC
        LIMIT 50
        """,
        (user_id,),
    )
    if not rows:
        return None

    for row in rows:
        if is_success_status(row.get("statut")):
            return row
    # Legacy fallback: garder le dernier import non vide si statut historique non standard
    # (mais ignorer explicitement les statuts d'échec).
    for row in rows:
        status_norm = _normalize_token(row.get("statut"))
        if status_norm and status_norm in {"echec", "failed", "failure", "error"}:
            continue
        return row
    return None


_SAFE_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _normalize_token(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _safe_table_identifier(name) -> str | None:
    if name is None:
        return None
    candidate = str(name).strip().strip("`").strip()
    if not candidate:
        return None
    if not _SAFE_SQL_IDENTIFIER_RE.fullmatch(candidate):
        return None
    return candidate


def _table_row_count(table_name: str) -> int:
    safe_name = _safe_table_identifier(table_name)
    if not safe_name or not _table_exists(safe_name):
        return 0
    try:
        row = run_query(f"SELECT COUNT(*) AS n FROM `{safe_name}`", fetch_one=True) or {"n": 0}
        return int(row.get("n") or 0)
    except Exception:
        return 0


def _table_columns_ordered(table_name: str) -> list[str]:
    safe_name = _safe_table_identifier(table_name)
    if not safe_name:
        return []
    rows = run_query(
        """
        SELECT COLUMN_NAME AS col
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
        """,
        (safe_name,),
    ) or []
    return [str(r.get("col")) for r in rows if r.get("col")]


def _extract_table_candidates_from_value(value, max_depth=4):
    if max_depth <= 0:
        return []

    candidates = []
    if isinstance(value, dict):
        for key in (
            "table_name",
            "table",
            "transaction_table",
            "transactions_table",
            "target_table",
            "source_table",
            "db_table",
        ):
            if value.get(key):
                candidates.append(value.get(key))

        if value.get("compressed"):
            payload = decompress_payload(value.get("content"))
            candidates.extend(_extract_table_candidates_from_value(payload, max_depth=max_depth - 1))

        for sub_value in value.values():
            candidates.extend(_extract_table_candidates_from_value(sub_value, max_depth=max_depth - 1))
        return candidates

    if isinstance(value, list):
        for item in value:
            candidates.extend(_extract_table_candidates_from_value(item, max_depth=max_depth - 1))
        return candidates

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            candidates.extend(_extract_table_candidates_from_value(parsed, max_depth=max_depth - 1))
        except Exception:
            pass

        for match in re.finditer(r"(?i)\b(?:from|into|join|update|table)\s+`?([A-Za-z0-9_]+)`?", text):
            candidates.append(match.group(1))
        for match in re.finditer(r"(?i)\btransactions[_A-Za-z0-9]*\b", text):
            candidates.append(match.group(0))
        return candidates

    return candidates


def resolve_import_table(import_row):
    """
    Détermine la table de transactions active liée à un import.
    Priorité:
      1) champ explicite dans l'import
      2) détails JSON (ou payload compressé) de l'import
      3) conventions de nommage ETL
      4) fallback contrôlé vers transactions (si existe et non vide)
    """
    import_row = import_row or {}
    candidates = []

    for key in (
        "table_name",
        "table",
        "transaction_table",
        "transactions_table",
        "target_table",
        "source_table",
        "db_table",
    ):
        if import_row.get(key):
            candidates.append(import_row.get(key))

    details_payload = import_row.get("details")
    candidates.extend(_extract_table_candidates_from_value(details_payload))

    file_stem = Path(str(import_row.get("nom_fichier") or "")).stem
    stem_norm = unicodedata.normalize("NFKD", file_stem)
    stem_norm = "".join(ch for ch in stem_norm if not unicodedata.combining(ch))
    stem_norm = re.sub(r"[^A-Za-z0-9]+", "_", stem_norm).strip("_").lower()
    import_id = import_row.get("id")
    user_id = import_row.get("user_id")
    if stem_norm:
        candidates.extend([
            stem_norm,
            f"transactions_{stem_norm}",
            f"transaction_{stem_norm}",
            f"import_{stem_norm}",
        ])
    if import_id:
        candidates.extend([
            f"transactions_import_{import_id}",
            f"transactions_{import_id}",
            f"import_{import_id}",
        ])
        if user_id:
            candidates.extend([
                f"transactions_user_{user_id}_{import_id}",
                f"transactions_u{user_id}_{import_id}",
            ])

    seen = set()
    for candidate in candidates:
        safe_name = _safe_table_identifier(candidate)
        if not safe_name or safe_name in seen:
            continue
        seen.add(safe_name)
        if _table_exists(safe_name):
            return safe_name

    if _table_exists("transactions") and _table_row_count("transactions") > 0:
        return "transactions"
    return None


def _first_value(row: dict, column_name: str | None, default=None):
    if not column_name:
        return default
    if column_name not in row:
        return default
    value = row.get(column_name)
    if value is None:
        return default
    return value


def _first_non_empty_text(row: dict, column_name: str | None, default: str):
    value = _first_value(row, column_name, None)
    text = str(value or "").strip()
    return text if text else default


def _pick_column(column_lookup: dict[str, str], aliases: list[str]) -> str | None:
    for alias in aliases:
        key = _normalize_token(alias)
        found = column_lookup.get(key)
        if found:
            return found
    return None


def _infer_signed_amount(amount_value, signed_value, tx_type_value):
    signed = _to_float(signed_value, None)
    amount = abs(_to_float(amount_value, 0.0))
    if signed is not None:
        return float(signed)

    tx_norm = _normalize_token(tx_type_value)
    if any(token in tx_norm for token in ("depense", "expense", "debit", "sortie", "charge")):
        return -abs(amount)
    if any(token in tx_norm for token in ("revenu", "income", "credit", "entree")):
        return abs(amount)
    return float(amount)


def _fetch_star_schema_rows(table_name: str, table_cols: set[str]):
    joins = []
    has_date_dim = (
        "Date_ID" in table_cols
        and _table_exists("date")
        and {"Date_ID", "Date"}.issubset(_table_columns_runtime("date"))
    )
    if has_date_dim:
        joins.append("LEFT JOIN `date` d ON d.Date_ID = t.Date_ID")

    has_dep_dim = (
        "Departement_ID" in table_cols
        and _table_exists("departement")
        and {"Departement_ID", "NomDepartement"}.issubset(_table_columns_runtime("departement"))
    )
    if has_dep_dim:
        joins.append("LEFT JOIN departement dep ON dep.Departement_ID = t.Departement_ID")

    has_tt_dim = (
        "TypeTransaction_ID" in table_cols
        and _table_exists("typetransaction")
        and {"TypeTransaction_ID", "TypeTransaction"}.issubset(_table_columns_runtime("typetransaction"))
    )
    if has_tt_dim:
        joins.append("LEFT JOIN typetransaction tt ON tt.TypeTransaction_ID = t.TypeTransaction_ID")

    has_td_dim = (
        "TypeDepense_ID" in table_cols
        and _table_exists("typedepense")
        and {"TypeDepense_ID", "TypeDepense"}.issubset(_table_columns_runtime("typedepense"))
    )
    if has_td_dim:
        joins.append("LEFT JOIN typedepense td ON td.TypeDepense_ID = t.TypeDepense_ID")

    has_resp_dim = (
        "Responsable_ID" in table_cols
        and _table_exists("responsable")
        and {"Responsable_ID", "NomResponsable"}.issubset(_table_columns_runtime("responsable"))
    )
    if has_resp_dim:
        joins.append("LEFT JOIN responsable r ON r.Responsable_ID = t.Responsable_ID")

    has_cf_dim = (
        "ClientFournisseur_ID" in table_cols
        and _table_exists("clientfournisseur")
        and {"ClientFournisseur_ID", "NomClientFournisseur"}.issubset(_table_columns_runtime("clientfournisseur"))
    )
    if has_cf_dim:
        joins.append("LEFT JOIN clientfournisseur cf ON cf.ClientFournisseur_ID = t.ClientFournisseur_ID")

    has_proj_dim = (
        "Projet_ID" in table_cols
        and _table_exists("projet")
        and {"Projet_ID", "NomProjet"}.issubset(_table_columns_runtime("projet"))
    )
    if has_proj_dim:
        joins.append("LEFT JOIN projet p ON p.Projet_ID = t.Projet_ID")

    date_expr = "DATE_FORMAT(d.`Date`, '%Y-%m-%d')" if has_date_dim else "NULL"
    annee_expr = "YEAR(d.`Date`)" if has_date_dim else "NULL"
    mois_expr = "MONTH(d.`Date`)" if has_date_dim else "NULL"
    trimestre_expr = "QUARTER(d.`Date`)" if has_date_dim else "NULL"

    if has_date_dim:
        ym_expr = "COALESCE(NULLIF(TRIM(d.`YearMonth`), ''), DATE_FORMAT(d.`Date`, '%Y-%m'))"
    elif "year_month" in table_cols:
        ym_expr = "NULLIF(TRIM(t.`year_month`), '')"
    else:
        ym_expr = "NULL"

    tx_id_expr = "t.`Transaction_ID`" if "Transaction_ID" in table_cols else "NULL"
    dep_id_expr = "t.`Departement_ID`" if "Departement_ID" in table_cols else "NULL"
    montant_expr = "COALESCE(t.`Montant`, 0)" if "Montant" in table_cols else "0"
    signed_expr = "COALESCE(t.`Montant_Signe`, COALESCE(t.`Montant`, 0))" if "Montant_Signe" in table_cols else "COALESCE(t.`Montant`, 0)"
    dep_expr = "COALESCE(dep.NomDepartement, 'Non renseigné')" if has_dep_dim else "'Non renseigné'"
    tt_expr = "COALESCE(tt.TypeTransaction, 'Non renseigné')" if has_tt_dim else "'Non renseigné'"
    td_expr = "COALESCE(td.TypeDepense, 'N/A')" if has_td_dim else "'N/A'"
    resp_expr = "COALESCE(r.NomResponsable, 'Non renseigné')" if has_resp_dim else "'Non renseigné'"
    cf_expr = "COALESCE(cf.NomClientFournisseur, 'Non renseigné')" if has_cf_dim else "'Non renseigné'"
    cf_type_expr = "COALESCE(cf.`Type`, '')" if has_cf_dim else "''"
    proj_expr = "COALESCE(p.NomProjet, 'Sans projet')" if has_proj_dim else "'Sans projet'"
    order_clause = "ORDER BY t.`Transaction_ID` ASC" if "Transaction_ID" in table_cols else ""

    query = f"""
    SELECT
        {tx_id_expr} AS Transaction_ID,
        {dep_id_expr} AS departement_id,
        {montant_expr} AS Montant,
        {signed_expr} AS Montant_Signe,
        {date_expr} AS date_val,
        {annee_expr} AS annee,
        {mois_expr} AS mois,
        {trimestre_expr} AS trimestre,
        {ym_expr} AS ym_value,
        {dep_expr} AS departement,
        {tt_expr} AS type_transaction,
        {td_expr} AS type_depense,
        {resp_expr} AS responsable,
        {cf_expr} AS client_fournisseur,
        {cf_type_expr} AS cf_type,
        {proj_expr} AS projet
    FROM `{table_name}` t
    {' '.join(joins)}
    {order_clause}
    """
    rows = run_query(query) or []
    for row in rows:
        row["year_month"] = row.get("ym_value")
    return rows


def _fetch_flat_rows(table_name: str, table_cols_ordered: list[str]):
    if not table_cols_ordered:
        return []

    table_cols_set = set(table_cols_ordered)
    column_lookup = {}
    for col in table_cols_ordered:
        norm = _normalize_token(col)
        if norm and norm not in column_lookup:
            column_lookup[norm] = col

    col_map = {
        "transaction_id": _pick_column(column_lookup, ["Transaction_ID", "transaction_id", "id_transaction", "tx_id", "id"]),
        "departement_id": _pick_column(column_lookup, ["Departement_ID", "DepartementID", "department_id", "dept_id"]),
        "date": _pick_column(column_lookup, ["Date", "date_val", "date_transaction", "transaction_date", "operation_date"]),
        "annee": _pick_column(column_lookup, ["annee", "year"]),
        "mois": _pick_column(column_lookup, ["mois", "month"]),
        "trimestre": _pick_column(column_lookup, ["trimestre", "quarter"]),
        "year_month": _pick_column(column_lookup, ["year_month", "yearmonth", "periode", "period", "month_year"]),
        "montant": _pick_column(column_lookup, ["Montant", "amount", "valeur", "total"]),
        "montant_signe": _pick_column(column_lookup, ["Montant_Signe", "montant_signe", "signed_amount", "amount_signed", "net_amount"]),
        "departement": _pick_column(column_lookup, ["Département", "departement", "department", "service"]),
        "type_transaction": _pick_column(column_lookup, ["TypeTransaction", "type_transaction", "transaction_type", "type"]),
        "type_depense": _pick_column(column_lookup, ["TypeDépense", "type_depense", "expense_type", "categorie", "category"]),
        "responsable": _pick_column(column_lookup, ["Responsable", "manager", "owner"]),
        "client_fournisseur": _pick_column(column_lookup, ["Client_Fournisseur", "client_fournisseur", "client", "fournisseur", "supplier", "customer"]),
        "cf_type": _pick_column(column_lookup, ["cf_type", "partenaire_type", "partner_type", "supplier_customer_type"]),
        "projet": _pick_column(column_lookup, ["Projet", "project"]),
    }

    print(
        "[DATASET] flat-map table="
        f"{table_name} mapped={{{', '.join(f'{k}:{v}' for k, v in col_map.items() if v)}}}"
    )

    raw_rows = run_query(f"SELECT * FROM `{table_name}`") or []
    mapped_rows = []
    for idx, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            continue
        montant_value = _first_value(raw, col_map["montant"], 0.0)
        tx_type = _first_non_empty_text(raw, col_map["type_transaction"], "Non renseigné")
        signed_value = _infer_signed_amount(montant_value, _first_value(raw, col_map["montant_signe"], None), tx_type)

        mapped_rows.append(
            {
                "Transaction_ID": _first_value(raw, col_map["transaction_id"], idx),
                "DepartementID": _first_value(raw, col_map["departement_id"], None),
                "Date": _first_value(raw, col_map["date"], None),
                "date_val": _first_value(raw, col_map["date"], None),
                "annee": _first_value(raw, col_map["annee"], None),
                "mois": _first_value(raw, col_map["mois"], None),
                "trimestre": _first_value(raw, col_map["trimestre"], None),
                "year_month": _first_value(raw, col_map["year_month"], None),
                "Montant": montant_value,
                "Montant_Signe": signed_value,
                "Département": _first_non_empty_text(raw, col_map["departement"], "Non renseigné"),
                "TypeTransaction": tx_type,
                "TypeDépense": _first_non_empty_text(raw, col_map["type_depense"], "N/A"),
                "Responsable": _first_non_empty_text(raw, col_map["responsable"], "Non renseigné"),
                "Client_Fournisseur": _first_non_empty_text(raw, col_map["client_fournisseur"], "Non renseigné"),
                "cf_type": _first_non_empty_text(raw, col_map["cf_type"], ""),
                "Projet": _first_non_empty_text(raw, col_map["projet"], "Sans projet"),
            }
        )
    return mapped_rows


def _get_transactions_dataset(table_name: str):
    safe_name = _safe_table_identifier(table_name)
    if not safe_name:
        return {
            "success": False,
            "rows": [],
            "source": "transactions",
            "table_name": None,
            "message": "Nom de table invalide.",
            "reason": "invalid_table_name",
        }

    if not _table_exists(safe_name):
        return {
            "success": False,
            "rows": [],
            "source": "transactions",
            "table_name": safe_name,
            "message": f"Table {safe_name} introuvable.",
            "reason": "table_not_found",
        }

    table_cols_ordered = _table_columns_ordered(safe_name)
    table_cols_set = set(table_cols_ordered)
    row_count = _table_row_count(safe_name)
    print(
        f"[DATASET] table={safe_name} rows={row_count} cols={len(table_cols_ordered)} "
        f"user_columns={table_cols_ordered[:20]}"
    )

    if row_count <= 0:
        return {
            "success": False,
            "rows": [],
            "source": "transactions",
            "table_name": safe_name,
            "message": f"Table {safe_name} vide.",
            "reason": "table_empty",
        }

    raw_rows = []
    try:
        if {"Date_ID", "Montant"}.issubset(table_cols_set):
            raw_rows = _fetch_star_schema_rows(safe_name, table_cols_set)
        if not raw_rows:
            raw_rows = _fetch_flat_rows(safe_name, table_cols_ordered)
    except Exception as e:
        return {
            "success": False,
            "rows": [],
            "source": "transactions",
            "table_name": safe_name,
            "message": f"Erreur lecture table {safe_name}: {e}",
            "reason": "table_read_error",
        }

    if not raw_rows:
        return {
            "success": False,
            "rows": [],
            "source": "transactions",
            "table_name": safe_name,
            "message": f"Table {safe_name} sans lignes exploitables.",
            "reason": "no_raw_rows",
        }

    rows = []
    for i, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict):
            continue
        try:
            rows.append(normalize_import_row(row, idx=i))
        except Exception:
            continue

    print(
        f"[DATASET] normalized table={safe_name} raw_rows={len(raw_rows)} normalized_rows={len(rows)} "
        f"normalized_cols={list(rows[0].keys()) if rows else []}"
    )

    if not rows:
        return {
            "success": False,
            "rows": [],
            "source": "transactions",
            "table_name": safe_name,
            "message": f"Table {safe_name} présente mais aucune ligne exploitable après normalisation.",
            "reason": "no_normalized_rows",
        }

    return {
        "success": True,
        "rows": rows,
        "import_id": None,
        "filename": safe_name,
        "date_import": None,
        "source": "transactions",
        "table_name": safe_name,
        "row_count": int(len(rows)),
    }


def fetch_transactions_dataset(user_id=None, import_row=None):
    import_id = import_row.get("id") if isinstance(import_row, dict) else None
    print(
        f"[DATASET] fetch_transactions_dataset user_id={user_id} import_id={import_id}"
    )

    candidate_tables = []
    if isinstance(import_row, dict):
        resolved = resolve_import_table(import_row)
        if resolved:
            candidate_tables.append(resolved)
    if "transactions" not in candidate_tables:
        candidate_tables.append("transactions")

    seen = set()
    attempts = []
    for table_name in candidate_tables:
        safe_name = _safe_table_identifier(table_name)
        if not safe_name or safe_name in seen:
            continue
        seen.add(safe_name)

        dataset = _get_transactions_dataset(safe_name)
        row_count = int(dataset.get("row_count") or len(dataset.get("rows") or []))
        attempts.append({
            "table": safe_name,
            "success": bool(dataset.get("success")),
            "reason": dataset.get("reason"),
            "rows": row_count,
        })
        if dataset.get("success") and (dataset.get("rows") or []):
            rows = dataset.get("rows") or []
            print(
                f"[DATASET] fetch_transactions_dataset source={safe_name} "
                f"rows_returned={len(rows)}"
            )
            dataset.update({
                "source": "transactions",
                "table_name": safe_name,
                "transactions_table_rows": row_count,
            })
            return dataset

    tx_table_rows = int(_table_row_count("transactions"))
    reason = "no_transactions_in_db" if tx_table_rows <= 0 else "no_exploitable_transactions"
    message = (
        "Aucune transaction exploitable n'existe en base."
        if tx_table_rows <= 0
        else "Des transactions existent en base mais aucune ligne exploitable n'a pu être reconstruite."
    )
    print(
        f"[DATASET] fetch_transactions_dataset failed user_id={user_id} import_id={import_id} "
        f"source=transactions reason={reason} attempts={attempts}"
    )
    return {
        "success": False,
        "rows": [],
        "source": "transactions",
        "table_name": "transactions",
        "reason": reason,
        "message": message,
        "attempts": attempts,
        "transactions_table_rows": tx_table_rows,
    }


def _get_latest_user_import_metadata(user_id):
    if not user_id:
        return None
    return run_query(
        """
        SELECT id, user_id, nom_fichier, date_import, nb_lignes, statut, details, data, CHAR_LENGTH(data) AS data_length
        FROM historique_imports
        WHERE user_id=%s
        ORDER BY date_import DESC, id DESC
        LIMIT 1
        """,
        (user_id,),
        True,
    )


_ACTIVE_DATASET_CACHE = {}
_ACTIVE_DATASET_CACHE_TTL = 120  # seconds


def _get_cached_user_dataset(import_id):
    if not import_id:
        return None
    entry = _ACTIVE_DATASET_CACHE.get(import_id)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        _ACTIVE_DATASET_CACHE.pop(import_id, None)
        return None
    return entry["dataset"]


def _set_cached_user_dataset(import_id, dataset):
    if not import_id or not isinstance(dataset, dict):
        return
    _ACTIVE_DATASET_CACHE[import_id] = {
        "dataset": dataset,
        "expires_at": time.time() + _ACTIVE_DATASET_CACHE_TTL,
    }


def get_active_user_dataset(user_id):
    """
    Retourne le dataset actif depuis les transactions stockées en base.
    Les métadonnées d'import (id/fichier/date) sont ajoutées quand disponibles.
    """
    if not user_id:
        return {
            "success": False,
            "rows": [],
            "source": "none",
            "table_name": None,
            "reason": "missing_user",
            "message": "Utilisateur non authentifie.",
        }

    latest_success = get_last_successful_import(user_id)
    latest_any = _get_latest_user_import_metadata(user_id)
    import_meta = latest_success or latest_any or {}
    import_id = import_meta.get("id")

    cached_dataset = _get_cached_user_dataset(import_id)
    if cached_dataset is not None:
        cached_dataset = cached_dataset.copy()
        cached_dataset["import_id"] = import_id
        cached_dataset["filename"] = import_meta.get("nom_fichier")
        cached_dataset["date_import"] = import_meta.get("date_import")
        cached_dataset["status"] = import_meta.get("statut")
        cached_dataset["source"] = cached_dataset.get("source", "transactions")
        cached_dataset["table_name"] = cached_dataset.get("table_name", "transactions")
        return cached_dataset

    dataset = fetch_transactions_dataset(user_id=user_id, import_row=import_meta if import_meta else None)
    if not dataset.get("success"):
        reason = dataset.get("reason") or "dataset_unavailable"
        tx_rows = int(dataset.get("transactions_table_rows") or 0)
        if tx_rows <= 0 and not latest_success:
            reason = "no_successful_import_and_no_transactions"
            message = "Aucun import réussi ni transaction exploitable n'existe pour cet utilisateur."
        elif tx_rows <= 0:
            reason = "no_transactions_in_db"
            message = "Un import est présent mais aucune transaction exploitable n'est stockée en base."
        elif not latest_success:
            message = "Des transactions existent en base mais aucun import réussi n'est trouvé pour cet utilisateur."
        else:
            message = dataset.get("message") or "Aucune transaction exploitable n'a été trouvée."

        print(
            f"[DATASET] user_id={user_id} import_id={import_id} "
            f"transactions_rows={tx_rows} source=transactions reason={reason} message={message}"
        )
        return {
            "success": False,
            "rows": [],
            "import_id": import_id,
            "filename": import_meta.get("nom_fichier"),
            "date_import": import_meta.get("date_import"),
            "status": import_meta.get("statut"),
            "source": "transactions",
            "table_name": dataset.get("table_name") or "transactions",
            "transactions_table_rows": tx_rows,
            "reason": reason,
            "message": message,
        }

    rows = dataset.get("rows") or []
    tx_rows = int(dataset.get("transactions_table_rows") or _table_row_count("transactions"))
    print(
        f"[DATASET] user_id={user_id} import_id={import_id} source=transactions "
        f"transactions_rows={tx_rows} rows_returned={len(rows)} table={dataset.get('table_name')}"
    )
    success_payload = {
        "success": True,
        "rows": rows,
        "import_id": import_id,
        "filename": import_meta.get("nom_fichier"),
        "date_import": import_meta.get("date_import"),
        "status": import_meta.get("statut"),
        "source": "transactions",
        "table_name": dataset.get("table_name") or "transactions",
        "transactions_table_rows": tx_rows,
    }
    _set_cached_user_dataset(import_id, success_payload)
    return success_payload


def _get_last_successful_import(user_id):
    return get_last_successful_import(user_id)


def _get_latest_import_dataset(user_id):
    dataset = get_active_user_dataset(user_id)
    if not dataset.get("success"):
        return None, []

    imp = {
        "id": dataset.get("import_id"),
        "nom_fichier": dataset.get("filename"),
        "date_import": dataset.get("date_import"),
        "nb_lignes": len(dataset.get("rows") or []),
        "statut": dataset.get("status") or "succes",
        "table_name": dataset.get("table_name"),
        "source": dataset.get("source"),
    }
    return imp, dataset.get("rows") or []


def _build_kpis_from_rows(rows):
    if not rows:
        return []

    kpis = []
    df = pd.DataFrame(rows)
    if df.empty:
        return []

    df["Montant"] = pd.to_numeric(df.get("Montant"), errors="coerce").fillna(0.0).abs()
    df["Montant_Signe"] = pd.to_numeric(df.get("Montant_Signe"), errors="coerce").fillna(0.0)
    df["annee"] = pd.to_numeric(df.get("annee"), errors="coerce").fillna(0).astype(int)
    df["trimestre"] = pd.to_numeric(df.get("trimestre"), errors="coerce").fillna(0).astype(int)
    df["year_month"] = df.get("year_month", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    df["departement"] = df.get("departement", pd.Series(dtype=str)).fillna("Inconnu").astype(str)
    df["type_transaction"] = df.get("type_transaction", pd.Series(dtype=str)).fillna("Autre").astype(str)
    df["type_depense"] = df.get("type_depense", pd.Series(dtype=str)).fillna("Non classée (N/A)").astype(str)

    # Standardize placeholders to one explicit bucket so KPIs keep these rows.
    placeholder_values = {"", "n/a", "na", "null", "none", "inconnu", "unknown", "non renseigne", "non renseigné", "-"}
    df["type_depense"] = df["type_depense"].apply(
        lambda v: "Non classée (N/A)" if str(v).strip().lower() in placeholder_values else str(v).strip()
    )

    s_total = float(df["Montant"].sum())
    s_signe = float(df["Montant_Signe"].sum())
    revenus = float(df.loc[df["Montant_Signe"] > 0, "Montant_Signe"].sum())
    depenses = abs(float(df.loc[df["Montant_Signe"] < 0, "Montant_Signe"].sum()))
    n = int(len(df))

    kpis.extend([
        {"kpiNom": "CA_Total", "periode": "global", "valeur": round(s_total, 2), "stat_type": "sum"},
        {"kpiNom": "Solde_Net", "periode": "global", "valeur": round(s_signe, 2), "stat_type": "sum"},
        {"kpiNom": "Revenus", "periode": "global", "valeur": round(revenus, 2), "stat_type": "sum"},
        {"kpiNom": "Dépenses", "periode": "global", "valeur": round(depenses, 2), "stat_type": "sum"},
        {"kpiNom": "Nb_Transactions", "periode": "global", "valeur": n, "stat_type": "count"},
        {"kpiNom": "Valeur_Moyenne", "periode": "global", "valeur": round(s_total / n, 2) if n else 0, "stat_type": "avg"},
        {"kpiNom": "Ratio_Dep_Rev", "periode": "global", "valeur": round(depenses / revenus * 100, 2) if revenus else 0, "stat_type": "ratio"},
    ])

    q_data = defaultdict(list)
    dep_data = defaultdict(list)
    tt_data = defaultdict(list)
    td_data = defaultdict(list)
    ym_data = defaultdict(list)

    for _, r in df.iterrows():
        signed = float(r.get("Montant_Signe") or 0)
        amount = abs(float(r.get("Montant") or signed or 0))
        q_val = _to_int(r.get("trimestre"), 0)
        y_val = _to_int(r.get("annee"), 0)
        if q_val and y_val:
            q_data[f"Q{q_val}_{y_val}"].append(signed)
        dep_data[str(r.get("departement") or "Inconnu")].append(signed)
        tt_data[str(r.get("type_transaction") or "Autre")].append(signed)
        if signed < 0:
            td_data[str(r.get("type_depense") or "Non classée (N/A)")].append(abs(signed))
        elif str(r.get("type_transaction") or "").strip().lower().startswith("dep"):
            td_data[str(r.get("type_depense") or "Non classée (N/A)")].append(amount)
        ym = str(r.get("year_month") or "0000-00").strip() or "0000-00"
        ym_data[ym].append(signed)

    for key, vals in q_data.items():
        kpis.append({"kpiNom": f"CA_{key}", "periode": key, "valeur": round(sum(v for v in vals if v > 0), 2), "stat_type": "sum"})
        kpis.append({"kpiNom": f"Solde_{key}", "periode": key, "valeur": round(sum(vals), 2), "stat_type": "sum"})
    for dep, vals in dep_data.items():
        kpis.append({"kpiNom": f"CA_{dep}", "periode": "global", "valeur": round(sum(v for v in vals if v > 0), 2), "stat_type": "sum"})
        kpis.append({"kpiNom": f"Solde_{dep}", "periode": "global", "valeur": round(sum(vals), 2), "stat_type": "sum"})
    for tt, vals in tt_data.items():
        kpis.append({"kpiNom": f"Vol_{tt}", "periode": "global", "valeur": round(sum(abs(v) for v in vals), 2), "stat_type": "sum"})
    for td, vals in td_data.items():
        kpis.append({"kpiNom": f"Dep_{td}", "periode": "global", "valeur": round(sum(vals), 2), "stat_type": "sum"})

    sorted_ym = sorted(ym_data)
    for i, ym in enumerate(sorted_ym):
        val = round(sum(ym_data[ym]), 2)
        prev = round(sum(ym_data[sorted_ym[i - 1]]), 2) if i > 0 else val
        evo = round((val - prev) / prev * 100, 2) if prev else 0
        kpis.append({"kpiNom": "CA_Mensuel", "periode": ym, "valeur": val, "evolution": evo, "stat_type": "sum"})

    return kpis


# ═══════════════════════════════════════════════════════════════
# INIT TABLES au démarrage
# ═══════════════════════════════════════════════════════════════
def create_tables():
    import mysql.connector
    from config import DB_CONFIG
    cfg = DB_CONFIG.copy()
    db_name = cfg.pop("database", "pfe_bd")
    conn = None
    try:
        conn = mysql.connector.connect(**cfg)
        cur  = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        cur.execute(f"USE {db_name}")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                firstname  VARCHAR(100),
                lastname   VARCHAR(100),
                email      VARCHAR(150) UNIQUE NOT NULL,
                password   VARCHAR(255),
                role       VARCHAR(50)  DEFAULT 'user',
                login_type VARCHAR(50)  DEFAULT 'email',
                created_at DATETIME     DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS login_history (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                user_id        INT NULL,
                email          VARCHAR(255) NOT NULL,
                login_status   VARCHAR(50)  NOT NULL,
                login_type     VARCHAR(50)  NOT NULL DEFAULT 'email',
                ip_address     VARCHAR(100) NULL,
                user_agent     TEXT NULL,
                login_date     DATETIME     DEFAULT CURRENT_TIMESTAMP,
                failure_reason VARCHAR(255) NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS valeur_kpi (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                kpiNom        VARCHAR(255) NOT NULL,
                periode       VARCHAR(50)  NOT NULL,
                valeur        FLOAT        NOT NULL,
                evolution     FLOAT        DEFAULT 0,
                departementId INT          DEFAULT NULL,
                source        VARCHAR(255) DEFAULT 'etl',
                stat_type     VARCHAR(20)  DEFAULT 'sum',
                created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS previsions (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                type          VARCHAR(100) NOT NULL,
                dateDebut     DATE         NOT NULL,
                dateFin       DATE         NOT NULL,
                resultats     JSON         NOT NULL,
                departementId INT          DEFAULT NULL,
                created_by    INT          DEFAULT NULL,
                created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS historique_imports (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                user_id     INT DEFAULT NULL,
                nom_fichier VARCHAR(255)  NOT NULL,
                date_import DATETIME     DEFAULT CURRENT_TIMESTAMP,
                nb_lignes   INT          DEFAULT 0,
                nb_erreurs  INT          DEFAULT 0,
                statut      ENUM('succes','partiel','echec') NOT NULL DEFAULT 'succes',
                departement VARCHAR(255) DEFAULT NULL,
                importe_par VARCHAR(255) DEFAULT NULL,
                details     LONGTEXT     DEFAULT NULL,
                data        LONGTEXT     DEFAULT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        ensure_login_history_schema(cur)
        ensure_audit_logs_schema(cur)
        ensure_rapports_schema(cur)
        ensure_historique_imports_schema(cur)
        ensure_performance_indexes(cur)
        conn.commit()
        cur.close()
        print("[app] ✅ Tables OK")
    except Exception as e:
        print(f"[app] ⚠ create_tables : {e}")
    finally:
        if conn:
            conn.close()


app.register_blueprint(create_debug_blueprint(admin_required, create_tables))
app.register_blueprint(create_auth_blueprint({
    "run_query": run_query,
    "run_update": run_update,
    "save_login_history": save_login_history,
    "log_audit": log_audit,
    "create_access_token": create_access_token,
    "limiter": limiter,
    "AUTH_RATE_LIMIT": AUTH_RATE_LIMIT,
    "jwt_required": jwt_required,
    "get_current_user": get_current_user,
    "check_password_hash": check_password_hash,
    "generate_password_hash": generate_password_hash,
    "internal_error_response": _internal_error_response,
    "is_strong_password": _is_strong_password,
    "secrets": secrets,
}))
app.register_blueprint(create_admin_blueprint({
    "admin_required": admin_required,
    "get_current_user": get_current_user,
    "run_query": run_query,
    "run_update": run_update,
    "log_audit": log_audit,
    "generate_password_hash": generate_password_hash,
    "get_connection": get_connection,
    "ensure_login_history_schema": ensure_login_history_schema,
    "ensure_audit_logs_schema": ensure_audit_logs_schema,
    "ensure_rapports_schema": ensure_rapports_schema,
    "ensure_historique_imports_schema": ensure_historique_imports_schema,
    "is_strong_password": _is_strong_password,
    "_to_int": _to_int,
    "_normalize_audit_status": _normalize_audit_status,
    "_normalize_report_format": _normalize_report_format,
    "REPORTS_FOLDER": REPORTS_FOLDER,
    "_safe_report_stem": _safe_report_stem,
}))
app.register_blueprint(create_static_blueprint(FRONTEND))


# ═══════════════════════════════════════════════════════════════
# HELPERS lecture fichier ETL
# ═══════════════════════════════════════════════════════════════
def read_table_rows(file_path: Path) -> pd.DataFrame:
    ext = file_path.suffix.lower()
    if ext == ".csv":
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252", "utf-16", "utf-16-le", "utf-16-be"]:
            try:
                return pd.read_csv(str(file_path), encoding=enc, sep=None, engine="python")
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
        try:
            return pd.read_csv(str(file_path), encoding="latin-1", encoding_errors="replace", sep=None, engine="python")
        except Exception:
            pass
        raise ValueError("Impossible de lire le CSV")
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(str(file_path))
    raise ValueError("Format non supporté")


app.register_blueprint(create_etl_blueprint({
    "jwt_required": jwt_required,
    "admin_required": admin_required,
    "get_current_user": get_current_user,
    "run_query": run_query,
    "run_update": run_update,
    "log_audit": log_audit,
    "run_generic_etl": run_generic_etl,
    "save_import_history": save_import_history,
    "decompress_payload": decompress_payload,
    "decode_import_rows": decode_import_rows,
    "make_json_safe": make_json_safe,
    "safe_upload_path": _safe_upload_path,
    "read_table_rows": read_table_rows,
    "UPLOAD_FOLDER": UPLOAD_FOLDER,
    "limiter": limiter,
    "UPLOAD_RATE_LIMIT": UPLOAD_RATE_LIMIT,
    "internal_error_response": _internal_error_response,
    "pd": pd,
}))




def _build_dashboard_summary_from_rows(rows):
    df = pd.DataFrame(rows or [])
    if df.empty:
        return {
            "tx_count": 0,
            "revenus_totaux": 0.0,
            "depenses_totales": 0.0,
            "solde_net": 0.0,
            "marge_pct": 0.0,
            "period_stats": [],
            "departement_stats": [],
        }

    df["Montant_Signe"] = pd.to_numeric(df.get("Montant_Signe"), errors="coerce").fillna(0.0)
    df["year_month"] = df.get("year_month", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    df["departement"] = df.get("departement", pd.Series(dtype=str)).fillna("Non renseigne").astype(str).str.strip()
    df.loc[df["departement"] == "", "departement"] = "Non renseigne"

    tx_count = int(len(df))
    revenus = float(df.loc[df["Montant_Signe"] > 0, "Montant_Signe"].sum())
    depenses = abs(float(df.loc[df["Montant_Signe"] < 0, "Montant_Signe"].sum()))
    solde = float(df["Montant_Signe"].sum())
    marge = (solde / revenus * 100.0) if revenus else 0.0

    period_df = df[df["year_month"] != ""].copy()
    period_stats = []
    if not period_df.empty:
        grp = period_df.groupby("year_month")["Montant_Signe"].agg(list).reset_index(name="values")
        for _, row in grp.iterrows():
            vals = [float(v) for v in row["values"]]
            p_rev = sum(v for v in vals if v > 0)
            p_dep = abs(sum(v for v in vals if v < 0))
            p_solde = sum(vals)
            period_stats.append({
                "periode": str(row["year_month"]),
                "revenus": round(float(p_rev), 2),
                "depenses": round(float(p_dep), 2),
                "solde_net": round(float(p_solde), 2),
                "transactions": int(len(vals)),
            })
        period_stats.sort(key=lambda x: x["periode"])

    dep_stats = []
    dep_grp = df.groupby("departement")["Montant_Signe"].agg(list).reset_index(name="values")
    for _, row in dep_grp.iterrows():
        vals = [float(v) for v in row["values"]]
        d_rev = sum(v for v in vals if v > 0)
        d_dep = abs(sum(v for v in vals if v < 0))
        d_solde = sum(vals)
        dep_stats.append({
            "departement": str(row["departement"]),
            "revenus": round(float(d_rev), 2),
            "depenses": round(float(d_dep), 2),
            "solde_net": round(float(d_solde), 2),
            "transactions": int(len(vals)),
        })
    dep_stats.sort(key=lambda x: abs(x["solde_net"]), reverse=True)

    return {
        "tx_count": tx_count,
        "revenus_totaux": round(revenus, 2),
        "depenses_totales": round(depenses, 2),
        "solde_net": round(solde, 2),
        "marge_pct": round(marge, 2),
        "period_stats": period_stats,
        "departement_stats": dep_stats[:20],
        "decision_kpis": _build_decision_kpis(revenus, depenses, solde, marge, period_stats),
    }


def _build_decision_kpis(revenus, depenses, solde, marge, period_stats):
    period_count = len(period_stats)
    last_month = period_stats[-1] if period_stats else None
    prev_month = period_stats[-2] if period_count >= 2 else None

    if period_count >= 2 and prev_month is not None:
        prev_net = prev_month.get("solde_net", 0.0)
        last_net = last_month.get("solde_net", 0.0)
        if prev_net == 0:
            general_trend_pct = 100.0 if last_net > 0 else -100.0 if last_net < 0 else 0.0
        else:
            general_trend_pct = ((last_net - prev_net) / abs(prev_net)) * 100.0
    else:
        general_trend_pct = 0.0

    if marge >= 0.20:
        health_score = 95
    elif marge >= 0.10:
        health_score = 80
    elif marge >= 0.00:
        health_score = 65
    elif marge >= -0.10:
        health_score = 35
    else:
        health_score = 10

    if last_month and last_month.get("solde_net", 0.0) < 0:
        health_score = max(0, health_score - 5)
    elif last_month and last_month.get("solde_net", 0.0) > 0:
        health_score = min(100, health_score + 5)

    negative_months = sum(1 for p in period_stats if p.get("solde_net", 0.0) < 0)
    negative_ratio = negative_months / period_count if period_count else 0.0
    expense_growth = 0.0
    if last_month and prev_month and prev_month.get("depenses", 0.0) > 0:
        expense_growth = (last_month.get("depenses", 0.0) - prev_month.get("depenses", 0.0)) / prev_month.get("depenses", 1.0)

    if negative_ratio >= 0.5 or expense_growth > 0.25 or (last_month and last_month.get("solde_net", 0.0) < 0):
        budget_risk = "Élevé"
    elif negative_ratio >= 0.25 or expense_growth > 0.10:
        budget_risk = "Modéré"
    else:
        budget_risk = "Faible"

    if general_trend_pct > 5:
        trend_label = "En amélioration"
    elif general_trend_pct < -5:
        trend_label = "En dégradation"
    else:
        trend_label = "Stable"

    if period_stats:
        worst_month = min(period_stats, key=lambda x: x.get("solde_net", 0.0))
        critical_month = str(worst_month.get("periode", "N/A"))
        critical_net = round(float(worst_month.get("solde_net", 0.0)), 2)
    else:
        critical_month = "N/A"
        critical_net = 0.0

    return [
        {
            "key": "financial_health",
            "label": "Santé financière",
            "value": round(float(health_score), 0),
            "format": "number",
            "trend": round(float(marge), 1),
            "context": "Indice basé sur la marge et le résultat net.",
            "icon": "bi-heart-pulse",
            "line": "linear-gradient(90deg,#10b981,#059669)",
        },
        {
            "key": "budget_risk",
            "label": "Risque budgétaire",
            "value": budget_risk,
            "format": "string",
            "trend": round(float(expense_growth * 100.0), 1),
            "context": f"{negative_months}/{period_count} mois à solde net négatif.",
            "icon": "bi-exclamation-triangle-fill",
            "line": "linear-gradient(90deg,#f97316,#ef4444)",
        },
        {
            "key": "general_trend",
            "label": "Tendance générale",
            "value": trend_label,
            "format": "string",
            "trend": round(float(general_trend_pct), 1),
            "context": "Variation du solde net du dernier mois.",
            "icon": "bi-arrow-repeat",
            "line": "linear-gradient(90deg,#0ea5e9,#3b82f6)",
        },
        {
            "key": "critical_month",
            "label": "Mois critique",
            "value": critical_month,
            "format": "string",
            "trend": round(float(critical_net), 1),
            "context": "Mois avec le solde net le plus bas.",
            "icon": "bi-calendar-x",
            "line": "linear-gradient(90deg,#ef4444,#f97316)",
        },
    ]


def _query_dashboard_summary_sql():
    if not _table_exists("transactions"):
        return None

    tx_cols = _table_columns_runtime("transactions")
    if "Montant_Signe" in tx_cols:
        signed_expr = "COALESCE(t.Montant_Signe, 0)"
    elif "Montant" in tx_cols:
        signed_expr = "COALESCE(t.Montant, 0)"
    else:
        return None

    totals = run_query(
        f"""
        SELECT
            COUNT(*) AS tx_count,
            COALESCE(SUM(CASE WHEN {signed_expr} > 0 THEN {signed_expr} ELSE 0 END), 0) AS revenus_totaux,
            COALESCE(SUM(CASE WHEN {signed_expr} < 0 THEN ABS({signed_expr}) ELSE 0 END), 0) AS depenses_totales,
            COALESCE(SUM({signed_expr}), 0) AS solde_net
        FROM transactions t
        """,
        fetch_one=True,
    ) or {}

    tx_count = int(totals.get("tx_count") or 0)
    revenus = _to_float(totals.get("revenus_totaux"), 0.0)
    depenses = _to_float(totals.get("depenses_totales"), 0.0)
    solde = _to_float(totals.get("solde_net"), 0.0)
    marge = (solde / revenus * 100.0) if revenus else 0.0

    period_stats = []
    has_date_dim = _table_exists("date") and "Date_ID" in tx_cols and "Date" in _table_columns_runtime("date")
    if has_date_dim:
        period_stats = run_query(
            f"""
            SELECT
                DATE_FORMAT(d.`Date`, '%%Y-%%m') AS periode,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} > 0 THEN {signed_expr} ELSE 0 END), 0), 2) AS revenus,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} < 0 THEN ABS({signed_expr}) ELSE 0 END), 0), 2) AS depenses,
                ROUND(COALESCE(SUM({signed_expr}), 0), 2) AS solde_net,
                COUNT(*) AS transactions
            FROM transactions t
            JOIN `date` d ON d.Date_ID = t.Date_ID
            GROUP BY DATE_FORMAT(d.`Date`, '%%Y-%%m')
            ORDER BY periode ASC
            LIMIT 60
            """
        ) or []
    elif "year_month" in tx_cols:
        period_stats = run_query(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM(t.year_month), ''), 'Inconnu') AS periode,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} > 0 THEN {signed_expr} ELSE 0 END), 0), 2) AS revenus,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} < 0 THEN ABS({signed_expr}) ELSE 0 END), 0), 2) AS depenses,
                ROUND(COALESCE(SUM({signed_expr}), 0), 2) AS solde_net,
                COUNT(*) AS transactions
            FROM transactions t
            GROUP BY COALESCE(NULLIF(TRIM(t.year_month), ''), 'Inconnu')
            ORDER BY periode ASC
            LIMIT 60
            """
        ) or []

    dep_stats = []
    has_dep_dim = _table_exists("departement") and "Departement_ID" in tx_cols and "NomDepartement" in _table_columns_runtime("departement")
    if has_dep_dim:
        dep_stats = run_query(
            f"""
            SELECT
                COALESCE(dep.NomDepartement, CONCAT('Departement #', COALESCE(t.Departement_ID, 0))) AS departement,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} > 0 THEN {signed_expr} ELSE 0 END), 0), 2) AS revenus,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} < 0 THEN ABS({signed_expr}) ELSE 0 END), 0), 2) AS depenses,
                ROUND(COALESCE(SUM({signed_expr}), 0), 2) AS solde_net,
                COUNT(*) AS transactions
            FROM transactions t
            LEFT JOIN departement dep ON dep.Departement_ID = t.Departement_ID
            GROUP BY COALESCE(dep.NomDepartement, CONCAT('Departement #', COALESCE(t.Departement_ID, 0)))
            ORDER BY ABS(COALESCE(SUM({signed_expr}), 0)) DESC
            LIMIT 20
            """
        ) or []
    elif "departement" in tx_cols:
        dep_stats = run_query(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM(t.departement), ''), 'Non renseigne') AS departement,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} > 0 THEN {signed_expr} ELSE 0 END), 0), 2) AS revenus,
                ROUND(COALESCE(SUM(CASE WHEN {signed_expr} < 0 THEN ABS({signed_expr}) ELSE 0 END), 0), 2) AS depenses,
                ROUND(COALESCE(SUM({signed_expr}), 0), 2) AS solde_net,
                COUNT(*) AS transactions
            FROM transactions t
            GROUP BY COALESCE(NULLIF(TRIM(t.departement), ''), 'Non renseigne')
            ORDER BY ABS(COALESCE(SUM({signed_expr}), 0)) DESC
            LIMIT 20
            """
        ) or []

    return {
        "tx_count": tx_count,
        "revenus_totaux": round(revenus, 2),
        "depenses_totales": round(depenses, 2),
        "solde_net": round(solde, 2),
        "marge_pct": round(marge, 2),
        "period_stats": make_json_safe(period_stats),
        "departement_stats": make_json_safe(dep_stats),
    }


# ═══════════════════════════════════════════════════════════════
# ML DATA HELPERS
# ═══════════════════════════════════════════════════════════════
def _fetch_ml_dataframe(user_id, with_import=False):
    dataset = get_active_user_dataset(user_id)
    rows = dataset.get("rows") or []
    if not dataset.get("success") or not rows:
        print(
            f"[ML] user_id={user_id} source=transactions rows=0 "
            f"reason={dataset.get('reason')} message={dataset.get('message')}"
        )
        return (None, pd.DataFrame()) if with_import else pd.DataFrame()

    imp = {
        "id": dataset.get("import_id"),
        "nom_fichier": dataset.get("filename"),
        "date_import": dataset.get("date_import"),
        "nb_lignes": len(rows),
        "statut": dataset.get("status") or "succes",
        "source": dataset.get("source"),
    }

    df = pd.DataFrame(rows)
    if df.empty:
        return (imp, df) if with_import else df

    print(
        f"[ML] user_id={user_id} import_id={imp.get('id')} "
        f"table={dataset.get('table_name')} source={dataset.get('source')} "
        f"rows={len(df)} columns={list(df.columns)}"
    )

    if "date_val" not in df.columns and "Date" in df.columns:
        df["date_val"] = df["Date"]
    if "Date" not in df.columns and "date_val" in df.columns:
        df["Date"] = df["date_val"]

    if "Montant" not in df.columns:
        df["Montant"] = 0.0
    if "Montant_Signe" not in df.columns:
        df["Montant_Signe"] = df["Montant"]

    defaults_by_col = {
        "departement": "Non renseigné",
        "type_transaction": "Non renseigné",
        "type_depense": "N/A",
        "responsable": "Non renseigné",
        "client_fournisseur": "Non renseigné",
        "projet": "Sans projet",
        "year_month": "",
    }
    for col, default in defaults_by_col.items():
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default).astype(str).str.strip()
        df.loc[df[col] == "", col] = default

    if "client_fournisseur" in df.columns:
        df["fournisseur"] = df["client_fournisseur"]
    else:
        df["fournisseur"] = "Non renseigné"

    if "cf_type" in df.columns:
        df["partenaire_type"] = df["cf_type"]
    else:
        df["partenaire_type"] = "Inconnu"

    if "departement_id" not in df.columns:
        df["departement_id"] = df.get("DepartementID")

    if "transaction_id" not in df.columns:
        df["transaction_id"] = df.get("Transaction_ID")

    df["Montant"] = pd.to_numeric(df["Montant"], errors="coerce").fillna(0.0)
    df["Montant_Signe"] = pd.to_numeric(df["Montant_Signe"], errors="coerce").fillna(0.0)
    df["date_val"] = pd.to_datetime(df["date_val"], errors="coerce")

    # Remplace les date_val None par une date calculée depuis year_month
    if "date_val" in df.columns and "year_month" in df.columns:
        mask = df["date_val"].isna() & df["year_month"].notna()
        df.loc[mask, "date_val"] = pd.to_datetime(
            df.loc[mask, "year_month"].astype(str) + "-01", errors="coerce"
        )

    for col in ["fournisseur", "partenaire_type"]:
        if col in df.columns:
            df[col] = df[col].fillna("Non renseigné").astype(str).str.strip()
            df.loc[df[col] == "", col] = "Non renseigné"

    # Canonical aliases kept for compatibility with analytics/ML helpers.
    df["Date"] = df["date_val"]
    df["TypeTransaction"] = df["type_transaction"].apply(
        lambda v: "Dépense" if "depense" in _normalize_token(v) else ("Revenu" if "revenu" in _normalize_token(v) else str(v))
    )
    df["TypeDépense"] = df["type_depense"]
    df["Département"] = df["departement"]
    df["Client_Fournisseur"] = df["client_fournisseur"]
    df["Projet"] = df["projet"]
    df["Responsable"] = df["responsable"]
    df["DepartementID"] = df.get("departement_id")

    final_df = df.dropna(subset=["date_val"]).copy()
    print(
        f"[ML] user_id={user_id} import_id={imp.get('id')} usable_rows={len(final_df)} "
        f"normalized_columns={list(final_df.columns)}"
    )
    if with_import:
        return imp, final_df
    return final_df


# ═══════════════════════════════════════════════════════════════
# PRÉVISION SIMPLE DES TENDANCES (ML)
# ═══════════════════════════════════════════════════════════════
def _top_value(df, col, mode="expense"):
    if df.empty or col not in df.columns:
        return None

    work = df.copy()
    if mode == "expense":
        work = work[work["Montant_Signe"] < 0]
        grp = work.groupby(col)["Montant_Signe"].sum().abs()
    elif mode == "revenue":
        work = work[work["Montant_Signe"] > 0]
        grp = work.groupby(col)["Montant_Signe"].sum()
    else:
        grp = work.groupby(col)["Montant_Signe"].sum()

    if grp.empty:
        return None
    return str(grp.sort_values(ascending=False).index[0])


def _build_trend_series(df, trend_type):
    meta = {"entity": "Global", "title": "", "non_negative": True}
    work = df.copy()
    work["period_date"] = pd.to_datetime(work["year_month"].astype(str) + "-01", errors="coerce")
    work = work.dropna(subset=["period_date"]).copy()

    if trend_type == "seasonality_expenses":
        work = work[work["Montant_Signe"] < 0]
        grouped = work.groupby("period_date", as_index=False)["Montant_Signe"].sum()
        grouped["value"] = grouped["Montant_Signe"].abs()
        meta["title"] = "Prévision simple des dépenses"

    elif trend_type == "pilotage_global":
        grouped = work.groupby("period_date", as_index=False)["Montant_Signe"].sum()
        grouped["value"] = grouped["Montant_Signe"]
        meta["title"] = "Prévision du solde global"
        meta["non_negative"] = False

    elif trend_type == "departement_depenses":
        entity = _top_value(work, "departement", "expense")
        work = work[(work["departement"] == entity) & (work["Montant_Signe"] < 0)]
        grouped = work.groupby("period_date", as_index=False)["Montant_Signe"].sum()
        grouped["value"] = grouped["Montant_Signe"].abs()
        meta["entity"] = entity or "Inconnu"
        meta["title"] = f"Prévision du département le plus exposé : {meta['entity']}"

    elif trend_type == "fournisseur_depenses":
        entity = _top_value(work, "fournisseur", "expense")
        work = work[(work["fournisseur"] == entity) & (work["Montant_Signe"] < 0)]
        grouped = work.groupby("period_date", as_index=False)["Montant_Signe"].sum()
        grouped["value"] = grouped["Montant_Signe"].abs()
        meta["entity"] = entity or "Inconnu"
        meta["title"] = f"Prévision du fournisseur le plus coûteux : {meta['entity']}"

    elif trend_type == "poste_majeur":
        entity = _top_value(work, "type_depense", "expense")
        work = work[(work["type_depense"] == entity) & (work["Montant_Signe"] < 0)]
        grouped = work.groupby("period_date", as_index=False)["Montant_Signe"].sum()
        grouped["value"] = grouped["Montant_Signe"].abs()
        meta["entity"] = entity or "Inconnu"
        meta["title"] = f"Prévision du poste principal : {meta['entity']}"

    elif trend_type == "projet_net":
        project_scores = work.groupby("projet")["Montant_Signe"].sum().sort_values()
        entity = str(project_scores.index[0]) if not project_scores.empty else None
        work = work[work["projet"] == entity]
        grouped = work.groupby("period_date", as_index=False)["Montant_Signe"].sum()
        grouped["value"] = grouped["Montant_Signe"]
        meta["entity"] = entity or "Inconnu"
        meta["title"] = f"Prévision du projet le plus sensible : {meta['entity']}"
        meta["non_negative"] = False

    else:
        return pd.DataFrame(), meta

    if grouped.empty:
        return pd.DataFrame(), meta

    series_df = grouped[["period_date", "value"]].rename(columns={"period_date": "date"}).sort_values("date")
    series_df = series_df.groupby("date", as_index=False)["value"].sum()
    return series_df, meta


def _linear_forecast(series_df, horizon=3, non_negative=True):
    series_df = series_df.sort_values("date").reset_index(drop=True).copy()
    series_df["value"] = pd.to_numeric(series_df["value"], errors="coerce").fillna(0.0)

    if len(series_df) < 2:
        raise ValueError("Pas assez d'historique pour calculer une prévision")

    X = np.arange(len(series_df)).reshape(-1, 1)
    y = series_df["value"].values

    model = LinearRegression()
    model.fit(X, y)

    mae = None
    rmse = None
    r2 = None
    mape = None
    direction_acc = None
    eval_points = 0
    # Walk-forward validation to avoid unstable metrics based on only 1-2 holdout points.
    min_train_points = max(4, int(np.ceil(len(series_df) * 0.6)))
    if len(series_df) >= min_train_points + 2:
        wf_true = []
        wf_pred = []
        wf_prev = []

        for split in range(min_train_points, len(series_df)):
            train_X, test_X = X[:split], X[split : split + 1]
            train_y = y[:split]
            y_true = float(y[split])
            y_prev = float(y[split - 1])

            test_model = LinearRegression()
            test_model.fit(train_X, train_y)
            y_hat = float(test_model.predict(test_X)[0])

            wf_true.append(y_true)
            wf_pred.append(y_hat)
            wf_prev.append(y_prev)

        arr_true = np.array(wf_true, dtype=float)
        arr_pred = np.array(wf_pred, dtype=float)
        arr_prev = np.array(wf_prev, dtype=float)

        mae = float(mean_absolute_error(arr_true, arr_pred))
        rmse = float(np.sqrt(mean_squared_error(arr_true, arr_pred)))
        eval_points = int(len(arr_true))

        if eval_points >= 2:
            try:
                r2 = float(r2_score(arr_true, arr_pred))
            except Exception:
                r2 = None

        denom = np.where(np.abs(arr_true) < 1e-9, 1.0, np.abs(arr_true))
        mape = float(np.mean(np.abs((arr_true - arr_pred) / denom)) * 100.0)

        dir_true = np.sign(arr_true - arr_prev)
        dir_pred = np.sign(arr_pred - arr_prev)
        direction_acc = float(np.mean((dir_true == dir_pred).astype(float)) * 100.0)

    future_x = np.arange(len(series_df), len(series_df) + horizon).reshape(-1, 1)
    future_pred = model.predict(future_x)
    if non_negative:
        future_pred = np.maximum(future_pred, 0)

    history_labels = [pd.Timestamp(d).strftime("%Y-%m") for d in series_df["date"]]
    history_values = [round(float(v), 2) for v in series_df["value"].tolist()]

    last_date = pd.Timestamp(series_df["date"].iloc[-1])
    forecast_labels = [(last_date + pd.offsets.MonthBegin(i + 1)).strftime("%Y-%m") for i in range(horizon)]
    forecast_values = [round(float(v), 2) for v in future_pred.tolist()]

    last_actual = history_values[-1] if history_values else 0.0
    first_forecast = forecast_values[0] if forecast_values else 0.0
    delta = first_forecast - last_actual
    delta_pct = (delta / abs(last_actual) * 100) if last_actual else 0.0

    return {
        "model": {
            "name": "LinearRegression",
            "cv_mae": round(mae, 2) if mae is not None else None,
            "cv_rmse": round(rmse, 2) if rmse is not None else None,
            "cv_r2": round(r2, 4) if r2 is not None else None,
            "cv_mape": round(mape, 2) if mape is not None else None,
            "cv_direction_acc": round(direction_acc, 2) if direction_acc is not None else None,
            "eval_points": eval_points,
        },
        "history": {"labels": history_labels, "values": history_values},
        "forecast": {"labels": forecast_labels, "values": forecast_values},
        "summary": {
            "last_actual": round(last_actual, 2),
            "first_forecast": round(first_forecast, 2),
            "delta": round(delta, 2),
            "delta_pct": round(delta_pct, 2),
        },
    }


def _plain_summary(df, trend_type, entity, summary):
    expenses = df[df["Montant_Signe"] < 0].copy()

    top_dep = _top_value(expenses, "departement", "expense")
    top_type = _top_value(expenses, "type_depense", "expense")
    top_fourn = _top_value(expenses, "fournisseur", "expense")
    project_scores = df.groupby("projet")["Montant_Signe"].sum().sort_values()
    top_proj = str(project_scores.index[0]) if not project_scores.empty else None
    top_resp = _top_value(expenses, "responsable", "expense")

    delta_pct = float(summary.get("delta_pct", 0.0) or 0.0)
    if delta_pct > 8:
        what_happens = "La tendance suggère une hausse sur les prochains mois."
    elif delta_pct < -8:
        what_happens = "La tendance suggère une baisse sur les prochains mois."
    else:
        what_happens = "La tendance paraît relativement stable."

    if trend_type == "pilotage_global":
        action = "Surveillez d'abord les postes de coûts les plus lourds et les projets déficitaires."
    elif trend_type == "projet_net":
        action = "Vérifiez l'équilibre revenus / dépenses du projet concerné avant d'engager de nouveaux coûts."
    else:
        action = "Agissez d'abord sur le département, le poste et le fournisseur les plus exposés."

    why_parts = []
    if top_dep:
        why_parts.append(f"le département le plus exposé est {top_dep}")
    if top_type:
        why_parts.append(f"le poste principal est {top_type}")
    if top_fourn:
        why_parts.append(f"le fournisseur dominant est {top_fourn}")
    if top_proj:
        why_parts.append(f"le projet le plus sensible est {top_proj}")
    if top_resp:
        why_parts.append(f"le responsable le plus exposé est {top_resp}")

    why = "Les signaux historiques montrent que " + ", ".join(why_parts) + "." if why_parts else "La prévision se base sur l'historique global."

    return {
        "what_happens": what_happens,
        "why": why,
        "where_to_look": {
            "departement": top_dep,
            "type_depense": top_type,
            "fournisseur": top_fourn,
            "projet": top_proj,
            "responsable": top_resp,
        },
        "action": action,
    }


# ═══════════════════════════════════════════════════════════════
# SEGMENTATION MULTI-ENTITÉS (ML)
# ═══════════════════════════════════════════════════════════════
def _entity_column(entity):
    mapping = {
        "fournisseur": "fournisseur",
        "projet": "projet",
        "departement": "departement",
    }
    return mapping.get((entity or "").strip().lower())


def _mode_text(series, default="Inconnu"):
    if series is None or len(series) == 0:
        return default
    try:
        m = series.dropna().astype(str).mode()
        return str(m.iloc[0]).strip() if not m.empty else default
    except Exception:
        return default


def _build_entity_aggregate(df, entity):
    entity_col = _entity_column(entity)
    if not entity_col:
        raise ValueError("Entité non supportée")

    work = df.copy()
    work = work[work[entity_col].notna() & (work[entity_col].astype(str).str.strip() != "")].copy()
    work["entity_name"] = work[entity_col].astype(str).str.strip()
    work["abs_amount"] = work["Montant_Signe"].abs()
    work["expense_amount"] = np.where(work["Montant_Signe"] < 0, work["abs_amount"], 0.0)
    work["revenue_amount"] = np.where(work["Montant_Signe"] > 0, work["Montant_Signe"], 0.0)

    agg = work.groupby("entity_name").agg(
        tx_count=("Transaction_ID", "count"),
        total_volume=("abs_amount", "sum"),
        expense_total=("expense_amount", "sum"),
        revenue_total=("revenue_amount", "sum"),
        net_total=("Montant_Signe", "sum"),
        avg_amount=("abs_amount", "mean"),
        std_amount=("abs_amount", "std"),
        active_months=("year_month", pd.Series.nunique),
        dominant_depense=("type_depense", _mode_text),
        dominant_transaction=("type_transaction", _mode_text),
        dominant_responsable=("responsable", _mode_text),
    ).reset_index()

    if entity == "fournisseur":
        extra = work.groupby("entity_name").agg(
            partenaire_type=("partenaire_type", _mode_text),
        ).reset_index()
        agg = agg.merge(extra, on="entity_name", how="left")

    agg["std_amount"] = agg["std_amount"].fillna(0.0)
    agg["avg_amount"] = agg["avg_amount"].fillna(0.0)
    agg["monthly_frequency"] = agg["tx_count"] / agg["active_months"].replace(0, 1)
    agg["expense_ratio"] = agg["expense_total"] / agg["total_volume"].replace(0, 1)
    agg["revenue_ratio"] = agg["revenue_total"] / agg["total_volume"].replace(0, 1)
    agg["net_margin"] = agg["net_total"] / agg["total_volume"].replace(0, 1)

    agg = agg.replace([np.inf, -np.inf], 0).fillna(0)
    return agg


def _label_entity_segment(entity, row, metrics):
    volume = float(row.get("total_volume", 0) or 0)
    tx_count = float(row.get("tx_count", 0) or 0)

    if entity == "fournisseur":
        if volume >= metrics["vol_q75"] and tx_count >= metrics["tx_q75"]:
            return "Partenaires à fort volume et activité", "élevé"
        if volume >= metrics["vol_q75"]:
            return "Partenaires à fort volume", "moyen"
        if tx_count >= metrics["tx_q75"]:
            return "Partenaires fréquents", "moyen"
        return "Partenaires secondaires", "faible"

    if entity == "projet":
        if volume >= metrics["vol_q75"] and tx_count >= metrics["tx_q75"]:
            return "Projets majeurs", "moyen"
        if volume >= metrics["vol_q75"]:
            return "Projets à fort volume", "moyen"
        if tx_count >= metrics["tx_q75"]:
            return "Projets fréquents", "faible"
        return "Projets légers", "faible"

    if entity == "departement":
        if volume >= metrics["vol_q75"] and tx_count >= metrics["tx_q75"]:
            return "Départements à fort volume et activité", "élevé"
        if volume >= metrics["vol_q75"]:
            return "Départements à fort volume", "moyen"
        if tx_count >= metrics["tx_q75"]:
            return "Départements actifs", "faible"
        return "Départements secondaires", "faible"

    return "Profil standard", "moyen"


def _build_multi_segmentation_payload(df, entity, k):
    agg = _build_entity_aggregate(df, entity)
    if agg.empty or len(agg) < 3:
        raise ValueError("Pas assez d'éléments pour segmenter cette entité")

    # La segmentation utilise uniquement deux dimensions :
    # - tx_count : nombre de transactions
    # - total_volume : somme des montants absolus
    # Ces deux métriques sont normalisées puis passées à KMeans.
    numeric_cols = [
        "tx_count", "total_volume"
    ]
    X = agg[numeric_cols].replace([np.inf, -np.inf], 0).fillna(0.0)

    n_clusters = max(2, min(int(k or 4), 5, len(agg) - 1 if len(agg) > 2 else 2))

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    agg["segment_id"] = model.fit_predict(X_scaled)

    pca = PCA(n_components=2, random_state=42)
    pca_points = pca.fit_transform(X_scaled)
    agg["pca_x"] = pca_points[:, 0]
    agg["pca_y"] = pca_points[:, 1]

    # Calcul des seuils de volume et de nombre de transactions.
    # Ces quantiles servent à définir les libellés des segments.
    metrics = {
        "vol_q25": float(agg["total_volume"].quantile(0.25)),
        "vol_q75": float(agg["total_volume"].quantile(0.75)),
        "tx_q75": float(agg["tx_count"].quantile(0.75)),
    }

    summary_rows = []
    scatter_points = []

    for cluster_id, cluster_df in agg.groupby("segment_id"):
        center = cluster_df[numeric_cols].mean(numeric_only=True).to_dict()
        center["segment_id"] = int(cluster_id)
        if entity == "fournisseur" and "partenaire_type" in cluster_df.columns:
            center["partenaire_type"] = _mode_text(cluster_df["partenaire_type"])
        label, risk = _label_entity_segment(entity, center, metrics)

        count = int(len(cluster_df))
        avg_value = float(cluster_df["total_volume"].mean()) if count else 0.0
        top_entities = cluster_df.sort_values("total_volume", ascending=False)["entity_name"].head(3).tolist()

        explanation = {
            "fournisseur": f"Ce profil regroupe des partenaires avec un volume moyen de {avg_value:,.0f} DT. Les éléments les plus visibles sont {', '.join(top_entities)}.",
            "projet": f"Ce profil regroupe des projets présentant un volume moyen de {avg_value:,.0f} DT. Les projets marquants sont {', '.join(top_entities)}.",
            "departement": f"Ce profil regroupe des départements présentant un volume moyen de {avg_value:,.0f} DT. Les départements dominants sont {', '.join(top_entities)}.",
        }.get(entity, "Profil segmenté automatiquement.")

        summary_rows.append({
            "segment_id": int(cluster_id),
            "segment_label": label,
            "risk_level": risk,
            "count": count,
            "avg_value": round(avg_value, 2),
            "avg_net": round(float(cluster_df["net_total"].mean()), 2),
            "active_months_avg": round(float(cluster_df["active_months"].mean()), 1),
            "top_entities": top_entities,
            "dominant_depense": _mode_text(cluster_df["dominant_depense"]),
            "dominant_transaction": _mode_text(cluster_df["dominant_transaction"]),
            "dominant_responsable": _mode_text(cluster_df["dominant_responsable"]),
            "partenaire_type": _mode_text(cluster_df["partenaire_type"]) if "partenaire_type" in cluster_df.columns else None,
            "explanation": explanation,
        })

        for _, row in cluster_df.iterrows():
            scatter_points.append({
                "x": round(float(row["pca_x"]), 4),
                "y": round(float(row["pca_y"]), 4),
                "entity_name": row["entity_name"],
                "cluster_id": int(cluster_id),
                "segment_label": label,
                "value": round(float(row["total_volume"]), 2),
                "net_total": round(float(row["net_total"]), 2),
            })

    summary_rows = sorted(summary_rows, key=lambda x: x["count"], reverse=True)
    dominant = summary_rows[0] if summary_rows else None
    risky = next((s for s in summary_rows if s["risk_level"] == "élevé"), dominant)

    distribution = {
        "labels": [s["segment_label"] for s in summary_rows],
        "values": [s["count"] for s in summary_rows],
    }
    averages = {
        "labels": [s["segment_label"] for s in summary_rows],
        "values": [round(s["avg_value"], 2) for s in summary_rows],
    }

    return {
        "success": True,
        "entity": entity,
        "overview": {
            "total_entities": int(len(agg)),
            "segments_count": int(len(summary_rows)),
            "dominant_segment": dominant["segment_label"] if dominant else None,
            "risky_segment": risky["segment_label"] if risky else None,
            "k_used": int(n_clusters),
        },
        "segments": summary_rows,
        "charts": {
            "distribution": distribution,
            "average_by_segment": averages,
            "scatter": scatter_points,
        },
    }


def _table_exists(table_name: str) -> bool:
    try:
        row = run_query(
            """
            SELECT COUNT(*) AS n
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            (table_name,),
            True,
        )
        return bool((row or {}).get("n"))
    except Exception:
        return False


def _table_columns_runtime(table_name: str) -> set[str]:
    try:
        rows = run_query(
            """
            SELECT COLUMN_NAME AS col
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            (table_name,),
        ) or []
        return {str(r.get("col")) for r in rows if r.get("col")}
    except Exception:
        return set()


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except Exception:
        return "0"


def _fmt_money(value) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ")
    except Exception:
        return "0.00"


def _normalize_question_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = raw.replace("’", "'")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _token_like(tokens: list[str], keywords: list[str], threshold: float = 0.83) -> bool:
    for token in tokens:
        for kw in keywords:
            if not token or not kw:
                continue
            if token == kw or kw in token or token in kw:
                return True
            if difflib.SequenceMatcher(None, token, kw).ratio() >= threshold:
                return True
    return False


def get_financial_context(user_id: int | None = None) -> dict:
    context = {
        "total_transactions": 0,
        "revenus_totaux": 0.0,
        "depenses_totales": 0.0,
        "solde_net": 0.0,
        "departements_actifs": 0,
        "dernieres_transactions": [],
        "top_departements": [],
        "kpi_disponibles": [],
        "latest_import": None,
    }

    dataset = get_active_user_dataset(user_id) if user_id else {"success": False, "rows": []}
    rows = dataset.get("rows") or []

    if dataset.get("success") and rows:
        revenus = 0.0
        depenses = 0.0
        dept_stats = defaultdict(lambda: {"revenus": 0.0, "depenses": 0.0, "solde_net": 0.0, "nb_transactions": 0})
        latest_rows = []

        for row in rows:
            signed = _to_float(row.get("Montant_Signe"), 0.0)
            if signed >= 0:
                revenus += signed
            else:
                depenses += abs(signed)

            dep_name = str(row.get("departement") or "Non renseigne")
            dep = dept_stats[dep_name]
            if signed >= 0:
                dep["revenus"] += signed
            else:
                dep["depenses"] += abs(signed)
            dep["solde_net"] += signed
            dep["nb_transactions"] += 1

            latest_rows.append({
                "transaction_id": row.get("Transaction_ID"),
                "date_transaction": row.get("date_val"),
                "montant": round(_to_float(row.get("Montant"), abs(signed)), 3),
                "montant_signe": round(signed, 3),
                "departement": dep_name,
                "type_transaction": row.get("type_transaction"),
                "type_depense": row.get("type_depense"),
                "responsable": row.get("responsable"),
                "client_fournisseur": row.get("client_fournisseur"),
                "projet": row.get("projet"),
            })

        latest_rows = sorted(
            latest_rows,
            key=lambda r: (
                str(r.get("date_transaction") or ""),
                _to_int(r.get("transaction_id"), 0),
            ),
            reverse=True,
        )[:5]

        top_departements = []
        for dep_name, values in dept_stats.items():
            top_departements.append({
                "departement": dep_name,
                "revenus": round(values["revenus"], 3),
                "depenses": round(values["depenses"], 3),
                "solde_net": round(values["solde_net"], 3),
                "nb_transactions": int(values["nb_transactions"]),
            })
        top_departements = sorted(top_departements, key=lambda d: abs(_to_float(d.get("solde_net"), 0.0)), reverse=True)[:5]

        context["total_transactions"] = int(len(rows))
        context["revenus_totaux"] = round(revenus, 3)
        context["depenses_totales"] = round(depenses, 3)
        context["solde_net"] = round(revenus - depenses, 3)
        context["departements_actifs"] = int(len([d for d in dept_stats.keys() if str(d).strip()]))
        context["dernieres_transactions"] = latest_rows
        context["top_departements"] = top_departements
        context["latest_import"] = {
            "import_id": dataset.get("import_id"),
            "filename": dataset.get("filename"),
            "imported_at": dataset.get("date_import").strftime("%Y-%m-%d %H:%M:%S") if dataset.get("date_import") else None,
            "rows_count": len(rows),
            "revenus": round(revenus, 3),
            "depenses": round(depenses, 3),
            "solde_net": round(revenus - depenses, 3),
        }
    else:
        context["latest_import"] = None

    try:
        if _table_exists("valeur_kpi"):
            kpis = run_query(
                """
                SELECT kpiNom, periode, valeur, source
                FROM valeur_kpi
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 20
                """
            ) or []
            context["kpi_disponibles"] = kpis
    except Exception:
        context["kpi_disponibles"] = []

    return make_json_safe(context)


def _context_to_prompt_text(context: dict) -> str:
    lines = [
        f"Transactions totales (dataset actif utilisateur): {int(context.get('total_transactions') or 0)}",
        f"Revenus totaux: {float(context.get('revenus_totaux') or 0):.2f} DT",
        f"Dépenses totales: {float(context.get('depenses_totales') or 0):.2f} DT",
        f"Solde net: {float(context.get('solde_net') or 0):.2f} DT",
        f"Départements actifs: {int(context.get('departements_actifs') or 0)}",
    ]

    latest_import = context.get("latest_import") or {}
    if latest_import:
        lines.append(
            f"Dernier import utilisateur: #{latest_import.get('import_id')} ({latest_import.get('filename')}), "
            f"{latest_import.get('rows_count')} lignes, solde {float(latest_import.get('solde_net') or 0):.2f} DT."
        )
    else:
        lines.append("Dernier import utilisateur: aucun import détecté.")

    last_tx = context.get("dernieres_transactions") or []
    if last_tx:
        lines.append("Top 5 dernières transactions:")
        for tx in last_tx[:5]:
            lines.append(
                "- "
                f"#{tx.get('transaction_id')} {tx.get('date_transaction') or '--'} | "
                f"{tx.get('departement') or '--'} | {tx.get('type_transaction') or '--'} | "
                f"{float(tx.get('montant_signe') or 0):.2f} DT"
            )

    top_dept = context.get("top_departements") or []
    if top_dept:
        lines.append("Top départements par montant:")
        for dep in top_dept[:5]:
            lines.append(
                "- "
                f"{dep.get('departement') or '--'} | solde {float(dep.get('solde_net') or 0):.2f} DT | "
                f"{int(dep.get('nb_transactions') or 0)} transactions"
            )

    kpis = context.get("kpi_disponibles") or []
    if kpis:
        lines.append("KPI disponibles (échantillon):")
        for k in kpis[:5]:
            lines.append(
                "- "
                f"{k.get('kpiNom')} ({k.get('periode')}) = {float(k.get('valeur') or 0):.2f}"
            )
    else:
        lines.append("KPI disponibles: aucun enregistrement trouvé dans valeur_kpi.")

    return "\n".join(lines)


def _is_question_about(q_norm: str, tokens: list[str], intent: str) -> bool:
    tx_words = ["transaction", "transactions", "enregistrement", "enregistrements", "operation", "operations", "tx"]
    rev_words = ["revenu", "revenus", "ca", "chiffre", "entree", "entrees", "income"]
    dep_words = ["depense", "depenses", "cout", "couts", "charge", "charges", "sortie", "sorties", "expense"]
    solde_words = ["solde", "marge", "resultat", "benefice", "profit", "net"]
    users_words = ["utilisateur", "utilisateurs", "user", "users", "membre", "membres", "compte", "comptes"]
    dept_words = ["departement", "departements", "service", "services", "division", "divisions"]
    kpi_words = ["kpi", "indicateur", "indicateurs", "metrique", "metriques"]
    prev_words = ["prevision", "previsions", "forecast", "projection", "projections"]
    whatif_words = ["what", "if", "whatif", "scenario", "simulation", "simuler"]

    if intent == "transactions":
        phrases = [
            "quel sont transactions", "quelles sont les transactions", "combien de transactions",
            "combien transaction", "nb transaction", "nombre transaction",
            "liste transactions", "dernieres transactions", "derniere transaction",
            "derniers enregistrements", "dernier enregistrement",
        ]
        if any(p in q_norm for p in phrases):
            return True
        if _token_like(tokens, tx_words):
            return True
        if _token_like(tokens, ["combien", "nb", "nombre", "liste", "dernier", "dernieres"]) and _token_like(tokens, tx_words):
            return True
        return False
    if intent == "revenus":
        return _token_like(tokens, rev_words)
    if intent == "depenses":
        return _token_like(tokens, dep_words)
    if intent == "solde":
        return _token_like(tokens, solde_words)
    if intent == "utilisateurs":
        return _token_like(tokens, users_words)
    if intent == "departements":
        return _token_like(tokens, dept_words)
    if intent == "kpi":
        return _token_like(tokens, kpi_words)
    if intent == "previsions":
        return _token_like(tokens, prev_words)
    if intent == "whatif":
        return ("what if" in q_norm) or _token_like(tokens, whatif_words)
    return False


def _generate_classic_chatbot_answer(user_id: int | None, question: str, context: dict | None = None) -> str:
    q_norm = _normalize_question_text(question)
    if not q_norm:
        return "Posez-moi une question sur vos transactions, KPI, revenus, dépenses, solde ou simulations What-If."

    tokens = q_norm.split()
    ctx = context or get_financial_context(user_id)
    total_tx = int(ctx.get("total_transactions") or 0)
    revenus = _to_float(ctx.get("revenus_totaux"), 0.0)
    depenses = _to_float(ctx.get("depenses_totales"), 0.0)
    solde = _to_float(ctx.get("solde_net"), 0.0)
    dept_count = int(ctx.get("departements_actifs") or 0)
    latest = ctx.get("dernieres_transactions") or []
    top_depts = ctx.get("top_departements") or []

    if _is_question_about(q_norm, tokens, "transactions"):
        base = (
            f"Votre base contient {_fmt_int(total_tx)} transactions. "
            "Une transaction correspond à une opération financière importée (revenu ou dépense) "
            "liée à une date, un département, un responsable, un client/fournisseur et un projet."
        )
        if latest:
            short_list = []
            for tx in latest[:5]:
                short_list.append(
                    f"#{tx.get('transaction_id')} {tx.get('date_transaction') or '--'} "
                    f"{tx.get('departement') or '--'} {float(tx.get('montant_signe') or 0):.2f} DT"
                )
            base += " Dernières transactions: " + " | ".join(short_list) + "."
        return base

    if _is_question_about(q_norm, tokens, "revenus"):
        return f"Les revenus totaux actuels sont de {_fmt_money(revenus)} DT."

    if _is_question_about(q_norm, tokens, "depenses"):
        return f"Les dépenses totales actuelles sont de {_fmt_money(depenses)} DT."

    if _is_question_about(q_norm, tokens, "solde"):
        return f"Le solde net actuel est de {_fmt_money(solde)} DT."

    if _is_question_about(q_norm, tokens, "departements"):
        if top_depts:
            top = top_depts[0]
            return (
                f"Il y a {dept_count} départements actifs. "
                f"Le département le plus contributif est {top.get('departement')} "
                f"avec un solde de {_fmt_money(top.get('solde_net'))} DT."
            )
        return f"Il y a {dept_count} départements actifs dans les transactions."

    if _is_question_about(q_norm, tokens, "utilisateurs"):
        users = run_query("SELECT COUNT(*) AS n FROM users", fetch_one=True) or {"n": 0}
        return f"Il y a actuellement {_fmt_int(users.get('n') or 0)} utilisateurs enregistrés."

    if _is_question_about(q_norm, tokens, "kpi"):
        kpis = ctx.get("kpi_disponibles") or []
        if not kpis:
            return "Aucun KPI n'est disponible pour le moment. Lancez un import ETL puis un recalcul KPI."
        sample = ", ".join(f"{k.get('kpiNom')} ({k.get('periode')})" for k in kpis[:4])
        return f"KPI disponibles: {sample}. Utilisez la page Analyse pour le détail complet."

    if _is_question_about(q_norm, tokens, "previsions"):
        previsions_count = 0
        try:
            if _table_exists("previsions"):
                row = run_query("SELECT COUNT(*) AS n FROM previsions", fetch_one=True) or {"n": 0}
                previsions_count = int(row.get("n") or 0)
        except Exception:
            previsions_count = 0
        return (
            f"Le module prévisions contient {_fmt_int(previsions_count)} scénario(x) enregistré(x). "
            "Vous pouvez créer/consulter des prévisions depuis les routes /api/previsions."
        )

    if _is_question_about(q_norm, tokens, "whatif"):
        return (
            "Le What-If simule l'impact d'un scénario (hausse dépenses, transfert budget, commissions, etc.) "
            f"sur votre solde net actuel ({_fmt_money(solde)} DT). "
            "Utilisez la page Assistance pour lancer la simulation détaillée."
        )

    return (
        f"Résumé rapide: {_fmt_int(total_tx)} transactions, revenus {_fmt_money(revenus)} DT, "
        f"dépenses {_fmt_money(depenses)} DT, solde net {_fmt_money(solde)} DT, "
        f"{_fmt_int(dept_count)} départements actifs."
    )
def _impact_severity(impact_percent):
    impact_percent = abs(float(impact_percent or 0))
    if impact_percent >= 20:
        return "danger", "Impact élevé"
    if impact_percent >= 8:
        return "warning", "Impact modéré"
    return "success", "Impact faible"


def _fmt_dt_value(value):
    return f"{float(value):,.0f} DT".replace(",", " ")


app.register_blueprint(create_reports_blueprint({
    "jwt_required": jwt_required,
    "get_current_user": get_current_user,
    "normalize_report_format": _normalize_report_format,
    "log_audit": log_audit,
    "get_user_by_id": _get_user_by_id,
    "decode_export_file_payload": _decode_export_file_payload,
    "safe_report_stem": _safe_report_stem,
    "REPORTS_FOLDER": REPORTS_FOLDER,
    "get_connection": get_connection,
    "ensure_rapports_schema": ensure_rapports_schema,
    "run_update": run_update,
}))
app.register_blueprint(create_finance_blueprint({
    "jwt_required": jwt_required,
    "get_current_user": get_current_user,
    "get_connection": get_connection,
    "get_latest_import_dataset": _get_latest_import_dataset,
    "no_import_response": _no_import_response,
    "to_int": _to_int,
    "build_kpis_from_rows": _build_kpis_from_rows,
    "log_audit": log_audit,
    "make_json_safe": make_json_safe,
}))
app.register_blueprint(create_analytics_blueprint({
    "jwt_required": jwt_required,
    "get_current_user": get_current_user,
    "get_active_user_dataset": get_active_user_dataset,
    "build_dashboard_summary_from_rows": _build_dashboard_summary_from_rows,
    "to_float": _to_float,
    "make_json_safe": make_json_safe,
    "log_audit": log_audit,
    "no_import_response": _no_import_response,
    "internal_error_response": _internal_error_response,
    "build_kpis_from_rows": _build_kpis_from_rows,
    "run_query": run_query,
    "get_last_successful_import": get_last_successful_import,
    "decode_import_rows": decode_import_rows,
}))
app.register_blueprint(create_ml_assistance_blueprint({
    "jwt_required": jwt_required,
    "get_current_user": get_current_user,
    "fetch_ml_dataframe": _fetch_ml_dataframe,
    "build_trend_series": _build_trend_series,
    "linear_forecast": _linear_forecast,
    "plain_summary": _plain_summary,
    "log_audit": log_audit,
    "no_import_response": _no_import_response,
    "make_json_safe": make_json_safe,
    "internal_error_response": _internal_error_response,
    "build_multi_segmentation_payload": _build_multi_segmentation_payload,
    "top_value": _top_value,
    "impact_severity": _impact_severity,
    "fmt_dt_value": _fmt_dt_value,
}))
app.register_blueprint(create_chatbot_blueprint({
    "jwt_required": jwt_required,
    "get_current_user": get_current_user,
    "run_query": run_query,
    "generate_classic_chatbot_answer": _generate_classic_chatbot_answer,
    "get_financial_context": get_financial_context,
    "get_active_user_dataset": get_active_user_dataset,
    "context_to_prompt_text": _context_to_prompt_text,
    "SYSTEM_PROMPT": SYSTEM_PROMPT,
    "OLLAMA_MODEL": OLLAMA_MODEL,
    "OLLAMA_CHAT_URL": OLLAMA_CHAT_URL,
    "OLLAMA_BASE_URL": OLLAMA_BASE_URL,
}))


# ═══════════════════════════════════════════════════════════════
# DEBUG ENDPOINT - Token Validation
# ═══════════════════════════════════════════════════════════════
@app.route("/api/debug/token-status", methods=["GET"])
def debug_token_status():
    """Debug endpoint to check if token is valid (requires auth)"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "No Bearer token provided", "has_auth_header": False}), 401
    
    token = auth[7:].strip()
    if not token:
        return jsonify({"error": "Bearer token is empty", "has_token": False}), 401
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        exp_time = datetime.fromtimestamp(payload.get("exp", 0))
        now_time = datetime.utcnow()
        return jsonify({
            "success": True,
            "valid": True,
            "exp": payload.get("exp"),
            "exp_time": exp_time.isoformat(),
            "now_time": now_time.isoformat(),
            "expired": now_time > exp_time,
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role")
        })
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token has expired", "expired": True}), 401
    except jwt.InvalidTokenError as e:
        return jsonify({"error": f"Invalid token: {str(e)}", "valid": False}), 401
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    create_tables()
    print(app.url_map)
    app.run(host=APP_HOST, port=APP_PORT, debug=APP_DEBUG)





