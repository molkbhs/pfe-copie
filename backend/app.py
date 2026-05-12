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
from sklearn.metrics import mean_absolute_error
from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, url_for
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = "super_secret_key"
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY") or app.secret_key
JWT_SECRET = app.config["JWT_SECRET_KEY"]
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_HOURS = 2
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:latest")
SYSTEM_PROMPT = """Tu es l’assistant BI du projet Finova.
Réponds en français, clairement et sans jargon inutile.
Utilise uniquement le contexte financier fourni.
N’invente jamais de chiffres si les données ne sont pas disponibles.
Si l’utilisateur demande les transactions, réponds avec le nombre réel de transactions et une explication liée à la base pfe_bd.
Tu peux aider sur les revenus, dépenses, solde net, KPI, import CSV, traitement ETL, prévisions et simulations What-If."""

CORS(app,
     resources={r"/api/*": {"origins": "*"}},
     supports_credentials=False,
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

_base = Path(__file__).resolve().parent
FRONTEND = next(
    (p for p in [_base.parent / "frontend", _base / "frontend", _base]
     if p.exists() and (p / "dash.html").exists()),
    _base.parent / "frontend",
)

UPLOAD_FOLDER = _base / "uploads_etl"
UPLOAD_FOLDER.mkdir(exist_ok=True)
REPORTS_FOLDER = _base / "reports_exports"
REPORTS_FOLDER.mkdir(exist_ok=True)

# ETL engine — chargé une seule fois au démarrage
_spec = _ilu.spec_from_file_location("etl_generic", str(_base / "etl_generic.py"))
_etl_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_etl_mod)
run_generic_etl = _etl_mod.run_generic_etl

# ═══════════════════════════════════════════════════════════════
# GOOGLE OAUTH
# ═══════════════════════════════════════════════════════════════
oauth = OAuth(app)
_google_client_id = os.getenv("GOOGLE_CLIENT_ID", "")
_google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
if not _google_client_id or not _google_client_secret:
    warnings.warn("Google OAuth credentials are not set in environment variables.")

google = oauth.register(
    name="google",
    client_id=_google_client_id,
    client_secret=_google_client_secret,
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    api_base_url="https://www.googleapis.com/oauth2/v2/",
    client_kwargs={"scope": "openid email profile"},
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
)


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
    nb_lignes   = int(stats.get("lignes", 0) or 0)
    nb_erreurs  = int(stats.get("nb_erreurs", 0) or 0)
    departement = stats.get("departement")
    importe_par = get_user_display_name(user_id) if user_id else None
    statut      = "succes" if success else "echec"

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
    tx_type = str(row.get("type_transaction") or row.get("TypeTransaction") or "").strip().lower()
    if "depense" in tx_type or "charge" in tx_type or "expense" in tx_type:
        signed = -abs(montant) if signed == 0 else signed
    elif "revenu" in tx_type or "income" in tx_type:
        signed = abs(montant) if signed == 0 else signed
    elif signed == 0:
        signed = montant

    y_int = _to_int(year, 0)
    m_int = _to_int(month, 0)
    q_int = _to_int(quarter, 0)
    if pd.notna(date_obj):
        if y_int == 0:
            y_int = int(date_obj.year)
        if m_int == 0:
            m_int = int(date_obj.month)
        if q_int == 0:
            q_int = int(date_obj.quarter)
        if not year_month:
            year_month = date_obj.strftime("%Y-%m")

    departement = row.get("departement") or row.get("Département") or "Non renseigne"
    type_depense = row.get("type_depense") or row.get("TypeDépense") or "N/A"
    responsable = row.get("responsable") or row.get("Responsable") or "Non renseigne"
    client_fournisseur = row.get("client_fournisseur") or row.get("Client_Fournisseur") or "Non renseigne"
    projet = row.get("projet") or row.get("Projet") or "Sans projet"

    cf_type = row.get("cf_type")
    if not cf_type:
        cf_type = "Client" if signed >= 0 else "Fournisseur"

    return {
        "Transaction_ID": _to_int(row.get("Transaction_ID"), idx),
        "Montant": round(float(montant), 3),
        "Montant_Signe": round(float(signed), 3),
        "date_val": date_obj.strftime("%Y-%m-%d") if pd.notna(date_obj) else None,
        "annee": y_int if y_int > 0 else None,
        "mois": m_int if m_int > 0 else None,
        "trimestre": q_int if q_int > 0 else None,
        "year_month": year_month or "",
        "departement": str(departement),
        "type_transaction": str(row.get("type_transaction") or row.get("TypeTransaction") or "Non renseigne"),
        "type_depense": str(type_depense),
        "responsable": str(responsable),
        "client_fournisseur": str(client_fournisseur),
        "cf_type": str(cf_type),
        "projet": str(projet),
    }


def _normalize_import_row(row, idx=1):
    return normalize_import_row(row, idx=1)


def get_last_successful_import(user_id):
    return run_query(
        """
        SELECT id, user_id, nom_fichier, date_import, nb_lignes, statut, data
        FROM historique_imports
        WHERE user_id=%s
          AND LOWER(COALESCE(statut, '')) IN ('succes', 'success')
          AND data IS NOT NULL
          AND CHAR_LENGTH(data) > 0
        ORDER BY date_import DESC, id DESC
        LIMIT 1
        """,
        (user_id,),
        True,
    )


def _get_last_successful_import(user_id):
    return get_last_successful_import(user_id)


def _get_latest_import_dataset(user_id):
    imp = get_last_successful_import(user_id)
    if not imp:
        return None, []

    raw_rows = decode_import_rows(imp.get("data"))
    if not raw_rows:
        return imp, []

    normalized = []
    for i, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict):
            continue
        normalized.append(normalize_import_row(row, idx=i))
    return imp, normalized


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
    df["type_depense"] = df.get("type_depense", pd.Series(dtype=str)).fillna("Autre").astype(str)

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
        m = float(r.get("Montant") or 0)
        q_val = _to_int(r.get("trimestre"), 0)
        y_val = _to_int(r.get("annee"), 0)
        if q_val and y_val:
            q_data[f"Q{q_val}_{y_val}"].append(m)
        dep_data[str(r.get("departement") or "Inconnu")].append(m)
        tt_data[str(r.get("type_transaction") or "Autre")].append(m)
        td_data[str(r.get("type_depense") or "Autre")].append(m)
        ym = str(r.get("year_month") or "0000-00").strip() or "0000-00"
        ym_data[ym].append(m)

    for key, vals in q_data.items():
        kpis.append({"kpiNom": f"CA_{key}", "periode": key, "valeur": round(sum(vals), 2), "stat_type": "sum"})
    for dep, vals in dep_data.items():
        kpis.append({"kpiNom": f"CA_{dep}", "periode": "global", "valeur": round(sum(vals), 2), "stat_type": "sum"})
    for tt, vals in tt_data.items():
        kpis.append({"kpiNom": f"Vol_{tt}", "periode": "global", "valeur": round(sum(vals), 2), "stat_type": "sum"})
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


# ═══════════════════════════════════════════════════════════════
# HELPERS lecture fichier ETL
# ═══════════════════════════════════════════════════════════════
def read_table_rows(file_path: Path) -> pd.DataFrame:
    ext = file_path.suffix.lower()
    if ext == ".csv":
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                return pd.read_csv(str(file_path), encoding=enc, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise ValueError("Impossible de lire le CSV")
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(str(file_path))
    raise ValueError("Format non supporté")


# ═══════════════════════════════════════════════════════════════
# GOOGLE OAUTH
# ═══════════════════════════════════════════════════════════════
def _oauth_html(user_data: dict, redirect_url: str, access_token: str) -> str:
    auth_user = {**user_data, "token": access_token}
    return f"""<!DOCTYPE html><html><head><title>Connexion réussie</title></head>
<body><script>
  const authUser = {json.dumps(auth_user)};
  sessionStorage.setItem('user', JSON.stringify(authUser));
  localStorage.setItem('user', JSON.stringify(authUser));
  setTimeout(()=>{{ window.location.href='{redirect_url}'; }}, 800);
</script></body></html>"""


@app.route("/api/auth/google")
def google_login():
    return google.authorize_redirect(url_for("google_callback", _external=True))


@app.route("/api/auth/google/callback")
def google_callback():
    try:
        token     = google.authorize_access_token()
        user_info = token.get("userinfo") or {}
        if not user_info and token.get("access_token"):
            resp = google.get("userinfo", token=token)
            user_info = resp.json() if resp else {}

        email     = (user_info.get("email") or "").strip().lower()
        firstname = user_info.get("given_name", "")
        lastname  = user_info.get("family_name", "")
        if not email:
            raise ValueError("Google n'a pas fourni d'email")

        base_url  = request.url_root.rstrip("/")
        existing  = run_query(
            "SELECT id,firstname,lastname,email,COALESCE(login_type,'email') AS login_type,COALESCE(role,'user') AS role FROM users WHERE LOWER(TRIM(email))=%s",
            (email,), True,
        )

        if existing and existing.get("login_type") != "google":
            save_login_history(existing.get("id"), email, "failed", "google", "Compte configure en connexion email")
            log_audit(
                existing.get("id"),
                "connexion",
                "auth_google",
                "failed",
                {"email": email, "reason": "Compte configure en connexion email"},
            )
            return redirect(f"{base_url}/index.html")

        if existing:
            role = existing.get("role", "user")
            user_data = {"id": existing["id"], "username": email.split("@")[0],
                         "firstname": existing["firstname"], "lastname": existing["lastname"],
                         "email": email, "role": role}
            access_token = create_access_token(user_data)
            save_login_history(existing["id"], email, "success", "google")
            log_audit(existing["id"], "connexion", "auth_google", "success", {"email": email})
            dest = f"{base_url}/admin.html" if role == "admin" else f"{base_url}/dash.html"
            return _oauth_html(user_data, dest, access_token)

        user_id = run_update(
            "INSERT INTO users (firstname,lastname,email,password,login_type,role,created_at) VALUES (%s,%s,%s,'','google','user',NOW())",
            (firstname, lastname, email),
        )
        new_user = {"id": user_id, "username": email.split("@")[0],
                    "firstname": firstname, "lastname": lastname, "email": email, "role": "user"}
        access_token = create_access_token(new_user)
        save_login_history(user_id, email, "success", "google")
        log_audit(user_id, "connexion", "auth_google", "success", {"email": email, "new_user": True})
        return _oauth_html(new_user, f"{base_url}/dash.html", access_token)
    except Exception as e:
        try:
            failed_email = locals().get("email", "")
            save_login_history(None, failed_email, "failed", "google", str(e)[:255])
            log_audit(None, "connexion", "auth_google", "failed", {"email": failed_email, "error": str(e)[:255]})
        except Exception:
            pass
        base_url = request.url_root.rstrip("/")
        return f"""<!DOCTYPE html><html><body><p style="color:red">Erreur : {e}</p>
<script>setTimeout(()=>window.location.href='{base_url}/index.html',3000)</script></body></html>"""


# ═══════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════
@app.route("/api/register", methods=["POST"])
def register():
    d         = request.get_json() or {}
    firstname = d.get("firstname", "").strip()
    lastname  = d.get("lastname", "").strip()
    email     = d.get("email", "").strip().lower()
    password  = d.get("password", "")

    if not all([firstname, lastname, email, password]):
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400
    if run_query("SELECT id FROM users WHERE email=%s", (email,), True):
        return jsonify({"error": "Email déjà utilisé"}), 409

    user_id = run_update(
        "INSERT INTO users (firstname,lastname,email,password,role,created_at) VALUES (%s,%s,%s,%s,'user',NOW())",
        (firstname, lastname, email, generate_password_hash(password)),
    )
    return jsonify({"message": "Inscription réussie",
                    "user": {"id": user_id, "firstname": firstname, "lastname": lastname, "email": email}}), 201


@app.route("/api/login", methods=["POST"])
def login():
    d        = request.get_json() or {}
    email    = d.get("email", "").strip().lower()
    password = d.get("password", "")

    if not email or not password:
        save_login_history(None, email, "failed", "email", "Email ou mot de passe manquant")
        log_audit(None, "connexion", "auth_email", "failed", {"email": email, "reason": "Email ou mot de passe manquant"})
        return jsonify({"error": "Email et mot de passe requis"}), 400

    user = run_query(
        "SELECT id,firstname,lastname,email,password,COALESCE(role,'user') AS role FROM users WHERE email=%s",
        (email,), True,
    )
    if not user or not check_password_hash(user["password"], password):
        save_login_history(None, email, "failed", "email", "Identifiants invalides")
        log_audit(None, "connexion", "auth_email", "failed", {"email": email, "reason": "Identifiants invalides"})
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    user_data = {
        "id": user["id"], "username": email.split("@")[0],
        "firstname": user["firstname"], "lastname": user["lastname"],
        "email": user["email"], "role": user.get("role", "user"),
    }
    access_token = create_access_token(user_data)
    user_data["token"] = access_token
    save_login_history(user["id"], user["email"], "success", "email")
    log_audit(user["id"], "connexion", "auth_email", "success", {"email": user["email"]})
    return jsonify({"message": "Connexion réussie", "user": user_data, "token": access_token})


# ═══════════════════════════════════════════════════════════════
# PROFIL
# ═══════════════════════════════════════════════════════════════
@app.route("/api/profile", methods=["GET"])
@jwt_required
def get_my_profile():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Non autorisé"}), 401
    user = run_query("SELECT id,firstname,email,role FROM users WHERE id=%s", (user_id,), True)
    if not user:
        return jsonify({"error": "User non trouvé"}), 404
    return jsonify({"status": "success", "data": {
        "id": user["id"], "username": user["firstname"],
        "email": user["email"], "role": user["role"],
    }})


@app.route("/api/profile/<int:user_id>", methods=["GET"])
@jwt_required
def get_profile(user_id):
    user = run_query(
        "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users WHERE id=%s",
        (user_id,), True,
    )
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    if user.get("created_at"):
        user["created_at"] = user["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify(user)


@app.route("/api/profile/<int:user_id>", methods=["PUT"])
@jwt_required
def update_profile(user_id):
    actor_id = get_current_user()
    d         = request.get_json() or {}
    firstname = (d.get("firstname") or "").strip()
    lastname  = (d.get("lastname")  or "").strip()
    email     = (d.get("email")     or "").strip().lower()

    if not all([firstname, lastname, email]):
        log_audit(actor_id, "modification_utilisateur", f"user:{user_id}", "failed", "Champs requis manquants")
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if not run_query("SELECT id FROM users WHERE id=%s", (user_id,), True):
        log_audit(actor_id, "modification_utilisateur", f"user:{user_id}", "failed", "Utilisateur non trouvé")
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    owner = run_query("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True)
    if owner and int(owner["id"]) != user_id:
        log_audit(actor_id, "modification_utilisateur", f"user:{user_id}", "failed", {"email": email, "reason": "Email déjà utilisé"})
        return jsonify({"error": "Cet email est déjà utilisé"}), 409

    try:
        run_update("UPDATE users SET firstname=%s,lastname=%s,email=%s WHERE id=%s",
                   (firstname, lastname, email, user_id))
        updated = run_query(
            "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users WHERE id=%s",
            (user_id,), True,
        )
        log_audit(actor_id, "modification_utilisateur", f"user:{user_id}", "success", {"email": email})
        if updated and updated.get("created_at"):
            updated["created_at"] = updated["created_at"].strftime("%Y-%m-%d %H:%M")
        return jsonify({"message": "Profil mis à jour", "user": updated})
    except Exception as e:
        log_audit(actor_id, "modification_utilisateur", f"user:{user_id}", "failed", str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/<int:user_id>/password", methods=["PUT"])
@jwt_required
def update_password(user_id):
    d            = request.get_json() or {}
    current_pwd  = d.get("current_password") or ""
    new_pwd      = d.get("new_password") or ""

    if not current_pwd or not new_pwd:
        return jsonify({"error": "Les deux mots de passe sont requis"}), 400
    if len(new_pwd) < 8:
        return jsonify({"error": "Le nouveau mot de passe doit contenir au moins 8 caractères"}), 400

    user = run_query("SELECT id,password FROM users WHERE id=%s", (user_id,), True)
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    if not check_password_hash(user["password"], current_pwd):
        return jsonify({"error": "Mot de passe actuel incorrect"}), 401

    try:
        run_update("UPDATE users SET password=%s WHERE id=%s",
                   (generate_password_hash(new_pwd), user_id))
        return jsonify({"message": "Mot de passe mis à jour"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/pending", methods=["POST"])
@jwt_required
def pending_profile_action():
    d            = request.get_json() or {}
    user_id      = d.get("user_id")
    current_pwd  = d.get("current_password") or ""
    new_pwd      = d.get("new_password") or ""

    if (d.get("type") or "").strip().lower() != "password":
        return jsonify({"error": "Type d'action invalide"}), 400
    if not all([user_id, current_pwd, new_pwd]):
        return jsonify({"error": "Données manquantes"}), 400

    user = run_query("SELECT id,email,password FROM users WHERE id=%s", (user_id,), True)
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    if not check_password_hash(user["password"], current_pwd):
        return jsonify({"error": "Mot de passe actuel incorrect"}), 401

    return jsonify({"message": "Demande créée", "token": secrets.token_urlsafe(16)})


@app.route("/api/profile/pending/cancel", methods=["POST"])
@jwt_required
def cancel_pending_action():
    token = (request.get_json() or {}).get("token")
    if not token:
        return jsonify({"error": "Token manquant"}), 400
    return jsonify({"message": "Demande annulée"})


@app.route("/api/profile/<int:user_id>", methods=["DELETE"])
@jwt_required
def delete_account(user_id):
    actor_id = get_current_user()
    try:
        target = run_query("SELECT id,email,firstname,lastname FROM users WHERE id=%s", (user_id,), True)
        run_update("DELETE FROM users WHERE id=%s", (user_id,))
        log_audit(
            actor_id,
            "suppression_utilisateur",
            f"user:{user_id}",
            "success",
            {"email": (target or {}).get("email"), "name": f"{(target or {}).get('firstname','')} {(target or {}).get('lastname','')}".strip()},
        )
        return jsonify({"message": "Compte supprimé"})
    except Exception as e:
        log_audit(actor_id, "suppression_utilisateur", f"user:{user_id}", "failed", str(e))
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# USERS / ADMIN
# ═══════════════════════════════════════════════════════════════
@app.route("/api/users", methods=["GET"])
@admin_required
def users_list():
    actor_id = get_current_user()
    users = run_query(
        "SELECT id,firstname,lastname,email,created_at,COALESCE(login_type,'email') AS login_type,COALESCE(role,'user') AS role FROM users ORDER BY id DESC"
    )
    for u in users:
        if u.get("created_at"):
            u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M")
    log_audit(actor_id, "api_access", "users_list", "success", {"rows": len(users)})
    return jsonify(users)


@app.route("/api/stats", methods=["GET"])
@admin_required
def stats():
    try:
        total      = run_query("SELECT COUNT(*) AS n FROM users", fetch_one=True)["n"]
        today      = datetime.now().strftime("%Y-%m-%d")
        today_cnt  = run_query("SELECT COUNT(*) AS n FROM users WHERE DATE(created_at)=%s", (today,), True)["n"]
        week_ago   = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_cnt   = run_query("SELECT COUNT(*) AS n FROM users WHERE created_at>=%s", (week_ago,), True)["n"]
        google_cnt = run_query("SELECT COUNT(*) AS n FROM users WHERE login_type='google'", fetch_one=True)["n"]
        email_cnt  = run_query("SELECT COUNT(*) AS n FROM users WHERE login_type='email' OR login_type IS NULL OR login_type=''", fetch_one=True)["n"]
        admin_cnt  = run_query("SELECT COUNT(*) AS n FROM users WHERE role='admin'", fetch_one=True)["n"]
        return jsonify({"total_users": total, "new_today": today_cnt, "active_week": week_cnt,
                        "google_users": google_cnt, "email_users": email_cnt, "admin_count": admin_cnt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/login-history", methods=["GET"])
@admin_required
def admin_login_history():
    actor_id = get_current_user()
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        ensure_login_history_schema(cur)
        conn.commit()
        cur.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

    search = (request.args.get("search") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    page = max(1, _to_int(request.args.get("page"), 1))
    limit = _to_int(request.args.get("limit"), 10)
    if limit <= 0:
        limit = 10
    limit = min(limit, 100)
    offset = (page - 1) * limit

    where = []
    params = []
    if search:
        where.append("LOWER(TRIM(lh.email)) LIKE %s")
        params.append(f"%{search}%")
    if status in ("success", "failed"):
        where.append("lh.login_status = %s")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total_row = run_query(
        f"SELECT COUNT(*) AS n FROM login_history lh {where_sql}",
        tuple(params),
        True,
    )
    total = int((total_row or {}).get("n") or 0)

    items = run_query(
        f"""
        SELECT
            lh.id,
            lh.user_id,
            COALESCE(u.firstname, '') AS firstname,
            COALESCE(u.lastname, '') AS lastname,
            lh.email,
            lh.login_status,
            COALESCE(lh.login_type, 'email') AS login_type,
            COALESCE(lh.ip_address, '') AS ip_address,
            lh.login_date,
            lh.failure_reason
        FROM login_history lh
        LEFT JOIN users u ON u.id = lh.user_id
        {where_sql}
        ORDER BY lh.login_date DESC, lh.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )

    for row in items:
        dt = row.get("login_date")
        if dt and hasattr(dt, "strftime"):
            row["login_date"] = dt.strftime("%Y-%m-%d %H:%M:%S")

    log_audit(
        actor_id,
        "api_access",
        "admin_login_history",
        "success",
        {"page": page, "limit": limit, "search": search, "status": status, "total": total},
    )
    return jsonify({
        "success": True,
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
    })


@app.route("/api/admin/audit-logs", methods=["GET"])
@admin_required
def admin_audit_logs():
    actor_id = get_current_user()
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        ensure_audit_logs_schema(cur)
        conn.commit()
        cur.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

    page = max(1, _to_int(request.args.get("page"), 1))
    limit = _to_int(request.args.get("limit"), 10)
    if limit <= 0:
        limit = 10
    limit = min(limit, 100)
    offset = (page - 1) * limit

    search = (request.args.get("search") or "").strip().lower()
    action = (request.args.get("action") or "").strip().lower()
    raw_status = (request.args.get("status") or request.args.get("statut") or "").strip().lower()
    status = _normalize_audit_status(raw_status)

    allowed_actions = {
        "connexion",
        "creation_utilisateur",
        "modification_utilisateur",
        "suppression_utilisateur",
        "changement_role",
        "import_donnees",
        "generation_rapport",
        "export_rapport",
        "api_access",
        "erreur_systeme",
    }

    where = []
    params = []

    if search:
        where.append(
            """
            (
                LOWER(COALESCE(al.action, '')) LIKE %s
                OR LOWER(COALESCE(al.ressource, '')) LIKE %s
                OR LOWER(COALESCE(al.ip_address, '')) LIKE %s
                OR LOWER(COALESCE(al.details, '')) LIKE %s
                OR LOWER(CONCAT(COALESCE(u.firstname, ''), ' ', COALESCE(u.lastname, ''))) LIKE %s
                OR LOWER(COALESCE(u.email, '')) LIKE %s
            )
            """
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like, like])

    if action and action in allowed_actions:
        where.append("al.action = %s")
        params.append(action)

    if raw_status in {"success", "succes", "ok", "failed", "failure", "echec", "error"}:
        where.append("al.statut = %s")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total_row = run_query(
        f"""
        SELECT COUNT(*) AS n
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        {where_sql}
        """,
        tuple(params),
        True,
    )
    total = int((total_row or {}).get("n") or 0)

    items = run_query(
        f"""
        SELECT
            al.id_log,
            al.action,
            al.ressource,
            al.user_id,
            COALESCE(u.firstname, '') AS firstname,
            COALESCE(u.lastname, '') AS lastname,
            COALESCE(u.email, '') AS email,
            COALESCE(al.statut, 'success') AS statut,
            COALESCE(al.ip_address, '') AS ip_address,
            COALESCE(al.details, '') AS details,
            al.date_action
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        {where_sql}
        ORDER BY al.date_action DESC, al.id_log DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    ) or []

    for row in items:
        dt = row.get("date_action")
        if dt and hasattr(dt, "strftime"):
            row["date_action"] = dt.strftime("%Y-%m-%d %H:%M:%S")

    log_audit(
        actor_id,
        "api_access",
        "admin_audit_logs",
        "success",
        {"page": page, "limit": limit, "search": search, "action": action, "status": raw_status, "total": total},
    )
    return jsonify({
        "success": True,
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
    })


@app.route("/api/admin/reports-history", methods=["GET"])
@admin_required
def admin_reports_history():
    actor_id = get_current_user()
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        ensure_rapports_schema(cur)
        conn.commit()
        cur.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()

    page = max(1, _to_int(request.args.get("page"), 1))
    limit = _to_int(request.args.get("limit"), 10)
    if limit <= 0:
        limit = 10
    limit = min(limit, 100)
    offset = (page - 1) * limit

    search = (request.args.get("search") or "").strip().lower()
    fmt = _normalize_report_format(request.args.get("format") or "")

    where = []
    params = []
    if search:
        where.append(
            """
            (
                LOWER(COALESCE(r.nom_rapport, '')) LIKE %s
                OR LOWER(COALESCE(u.email, '')) LIKE %s
                OR LOWER(CONCAT(COALESCE(u.firstname, ''), ' ', COALESCE(u.lastname, ''))) LIKE %s
            )
            """
        )
        like = f"%{search}%"
        params.extend([like, like, like])
    if fmt:
        where.append("LOWER(COALESCE(r.format, '')) = %s")
        params.append(fmt)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total_row = run_query(
        f"""
        SELECT COUNT(*) AS n
        FROM rapports r
        LEFT JOIN users u ON u.id = r.created_by
        {where_sql}
        """,
        tuple(params),
        True,
    ) or {"n": 0}

    items = run_query(
        f"""
        SELECT
            r.id_rapport,
            r.nom_rapport,
            COALESCE(r.type_rapport, 'financier') AS type_rapport,
            LOWER(COALESCE(r.format, 'pdf')) AS format,
            r.chemin_fichier,
            r.created_by,
            COALESCE(u.firstname, '') AS firstname,
            COALESCE(u.lastname, '') AS lastname,
            COALESCE(u.email, '') AS email,
            r.date_generation
        FROM rapports r
        LEFT JOIN users u ON u.id = r.created_by
        {where_sql}
        ORDER BY r.date_generation DESC, r.id_rapport DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    ) or []

    for row in items:
        dt = row.get("date_generation")
        if dt and hasattr(dt, "strftime"):
            row["date_generation"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        row["download_url"] = f"/api/admin/reports/{row.get('id_rapport')}/download"

    total = int(total_row.get("n") or 0)
    log_audit(
        actor_id,
        "api_access",
        "admin_reports_history",
        "success",
        {"page": page, "limit": limit, "search": search, "format": fmt, "total": total},
    )
    return jsonify({"success": True, "items": items, "total": total, "page": page, "limit": limit})


@app.route("/api/admin/reports/<int:report_id>/download", methods=["GET"])
@admin_required
def admin_download_report(report_id):
    actor_id = get_current_user()
    report = run_query(
        """
        SELECT id_rapport, nom_rapport, format, chemin_fichier, created_by
        FROM rapports
        WHERE id_rapport=%s
        """,
        (report_id,),
        True,
    )
    if not report:
        log_audit(actor_id, "export_rapport", f"report:{report_id}", "failed", "Rapport introuvable")
        return jsonify({"success": False, "error": "Rapport introuvable"}), 404

    rel_path = (report.get("chemin_fichier") or "").strip()
    if not rel_path:
        log_audit(actor_id, "export_rapport", f"report:{report_id}", "failed", "Chemin du fichier manquant")
        return jsonify({"success": False, "error": "Fichier du rapport manquant"}), 404

    file_path = (REPORTS_FOLDER / rel_path).resolve()
    base_path = REPORTS_FOLDER.resolve()
    if str(file_path).startswith(str(base_path)) is False or not file_path.exists():
        log_audit(actor_id, "export_rapport", f"report:{report_id}", "failed", "Fichier indisponible")
        return jsonify({"success": False, "error": "Fichier du rapport introuvable"}), 404

    filename = f"{_safe_report_stem(report.get('nom_rapport') or f'report_{report_id}')}.{_normalize_report_format(report.get('format') or '') or 'pdf'}"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    log_audit(actor_id, "export_rapport", f"report:{report_id}", "success", {"filename": filename})
    return send_from_directory(str(file_path.parent), file_path.name, as_attachment=True, download_name=filename, mimetype=mime)


@app.route("/api/admin/users/<int:target_id>/role", methods=["PUT"])
@admin_required
def update_user_role(target_id):
    actor_id = get_current_user()
    allowed_roles = {"user", "admin"}
    new_role = (request.get_json() or {}).get("role", "").strip().lower()
    if new_role not in allowed_roles:
        log_audit(actor_id, "changement_role", f"user:{target_id}", "failed", {"new_role": new_role, "reason": "Rôle invalide"})
        return jsonify({"error": "Rôle invalide"}), 400
    target = run_query("SELECT id,firstname,lastname,email,COALESCE(role,'user') AS role FROM users WHERE id=%s", (target_id,), True)
    if not target:
        log_audit(actor_id, "changement_role", f"user:{target_id}", "failed", "Utilisateur non trouvé")
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    try:
        run_update("UPDATE users SET role=%s WHERE id=%s", (new_role, target_id))
        log_audit(
            actor_id,
            "changement_role",
            f"user:{target_id}",
            "success",
            {
                "email": target.get("email"),
                "old_role": target.get("role"),
                "new_role": new_role,
            },
        )
        return jsonify({
            "success": True,
            "message": "Rôle mis à jour",
            "user": {
                "id": target_id,
                "email": target.get("email"),
                "role": new_role,
            },
        })
    except Exception as e:
        log_audit(actor_id, "changement_role", f"user:{target_id}", "failed", str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def create_user_admin():
    actor_id = get_current_user()
    d         = request.get_json() or {}
    firstname = (d.get("firstname") or "").strip()
    lastname  = (d.get("lastname")  or "").strip()
    email     = (d.get("email")     or "").strip().lower()
    password  = d.get("password")   or ""
    role      = (d.get("role")      or "user").strip().lower()
    allowed_roles = {"user", "admin"}

    if not all([firstname, lastname, email, password]):
        log_audit(actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Champs requis manquants"})
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if len(password) < 6:
        log_audit(actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Mot de passe trop court"})
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400
    if role not in allowed_roles:
        log_audit(actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Rôle invalide"})
        return jsonify({"error": "Rôle invalide"}), 400
    if run_query("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True):
        log_audit(actor_id, "creation_utilisateur", "users", "failed", {"email": email, "reason": "Email déjà utilisé"})
        return jsonify({"error": "Email déjà utilisé"}), 409

    user_id = run_update(
        "INSERT INTO users (firstname,lastname,email,password,role,login_type,created_at) VALUES (%s,%s,%s,%s,%s,'email',NOW())",
        (firstname, lastname, email, generate_password_hash(password), role),
    )
    log_audit(actor_id, "creation_utilisateur", f"user:{user_id}", "success", {"email": email, "role": role})
    return jsonify({"message": "Utilisateur créé", "user": {
        "id": user_id, "firstname": firstname, "lastname": lastname,
        "email": email, "role": role, "login_type": "email",
    }}), 201


@app.route("/api/users/export", methods=["GET"])
@admin_required
def export_users():
    actor_id = get_current_user()
    users = run_query(
        "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users ORDER BY id"
    )
    lines = ["id,firstname,lastname,email,role,login_type,created_at"]
    for u in users:
        created = u["created_at"]
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M")
        lines.append(f"{u['id']},{u['firstname']},{u['lastname']},{u['email']},{u.get('role','user')},{u.get('login_type','email')},{created}")
    log_audit(actor_id, "export_rapport", "users_csv", "success", {"rows": len(users)})
    return Response("\n".join(lines), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=users.csv"})


@app.route("/api/reports/export", methods=["POST"])
@jwt_required
def reports_export():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Auth requis"}), 401

    data = request.get_json(silent=True) or {}
    report_name = (data.get("nom_rapport") or data.get("report_name") or "Rapport Finova").strip()
    report_type = (data.get("type_rapport") or data.get("report_type") or "financier").strip().lower() or "financier"
    report_format = _normalize_report_format(data.get("format") or data.get("report_format") or "")
    file_base64 = data.get("file_base64") or data.get("file")

    if not report_format:
        log_audit(user_id, "generation_rapport", "reports_export", "failed", {"reason": "Format invalide"})
        return jsonify({"success": False, "error": "Format non supporté (pdf/png/jpg/csv/docx)"}), 400

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    summary_obj = data.get("summary") or data.get("kpis") or {}
    fallback_text = json.dumps(
        {
            "title": report_name,
            "type": report_type,
            "generated_at": generated_at,
            "generated_by": _get_user_by_id(user_id) or {"id": user_id},
            "summary": summary_obj,
        },
        ensure_ascii=False,
        indent=2,
    )
    file_bytes = _decode_export_file_payload(file_base64, fallback_text=fallback_text)
    if not file_bytes:
        file_bytes = fallback_text.encode("utf-8")

    safe_stem = _safe_report_stem(report_name, fallback="rapport_finova")
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{user_id}_{secrets.token_hex(4)}_{safe_stem}.{report_format}"
    out_path = REPORTS_FOLDER / unique_name

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        ensure_rapports_schema(cur)
        conn.commit()
        cur.close()
        with open(out_path, "wb") as fh:
            fh.write(file_bytes)
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        log_audit(user_id, "generation_rapport", "reports_export", "failed", {"error": str(e)})
        return jsonify({"success": False, "error": "Impossible de sauvegarder le fichier de rapport"}), 500
    finally:
        if conn:
            conn.close()

    try:
        report_id = run_update(
            """
            INSERT INTO rapports (nom_rapport, type_rapport, format, chemin_fichier, created_by, date_generation)
            VALUES (%s, %s, %s, %s, %s, NOW())
            """,
            (report_name, report_type, report_format, unique_name, user_id),
        )
    except Exception as e:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        log_audit(user_id, "generation_rapport", "reports_export", "failed", {"error": str(e)})
        return jsonify({"success": False, "error": "Impossible d'enregistrer l'historique du rapport"}), 500

    log_audit(
        user_id,
        "generation_rapport",
        f"report:{report_id}",
        "success",
        {"nom_rapport": report_name, "type_rapport": report_type, "format": report_format, "size_bytes": len(file_bytes)},
    )
    log_audit(
        user_id,
        "export_rapport",
        f"report:{report_id}",
        "success",
        {"nom_rapport": report_name, "format": report_format},
    )
    return jsonify({
        "success": True,
        "id_rapport": report_id,
        "nom_rapport": report_name,
        "type_rapport": report_type,
        "format": report_format,
        "download_url": f"/api/admin/reports/{report_id}/download",
    }), 201


# ═══════════════════════════════════════════════════════════════
# HISTORIQUE IMPORTS
# ═══════════════════════════════════════════════════════════════
@app.route("/api/etl/history", methods=["GET"])
@jwt_required
def get_etl_history():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401

    current = run_query(
        "SELECT id,COALESCE(role,'user') AS role FROM users WHERE id=%s", (user_id,), True
    )
    if not current:
        return jsonify({"error": "Utilisateur introuvable"}), 404

    base_sql = """
        SELECT h.id,h.user_id,h.nom_fichier,h.date_import,h.nb_lignes,h.nb_erreurs,
               h.statut,h.departement,h.importe_par,h.details,h.data,
               u.firstname,u.lastname,u.email
        FROM historique_imports h
        LEFT JOIN users u ON u.id=h.user_id
    """
    if current["role"] == "admin":
        rows = run_query(base_sql + " ORDER BY COALESCE(u.firstname,u.email) ASC, h.date_import DESC")
    else:
        rows = run_query(base_sql + " WHERE h.user_id=%s ORDER BY h.date_import DESC", (user_id,))

    grouped: dict = {}
    for row in rows:
        uname = (
            f"{row.get('firstname') or ''} {row.get('lastname') or ''}".strip()
            or row.get("email") or row.get("importe_par") or f"User {row.get('user_id')}"
        )
        if uname not in grouped:
            grouped[uname] = []

        def _decode(field):
            raw = row.get(field)
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("compressed"):
                    return decompress_payload(parsed.get("content")) or []
                return parsed
            except Exception:
                return []

        data_rows = decode_import_rows(row.get("data"))
        data_value = {
            "total_rows": len(data_rows),
            "rows": data_rows,
        }

        grouped[uname].append({
            "id": row["id"], "user_id": row["user_id"],
            "nom_fichier": row["nom_fichier"],
            "filename": row["nom_fichier"],
            "date_import": row["date_import"].isoformat() if row.get("date_import") else None,
            "imported_at": row["date_import"].isoformat() if row.get("date_import") else None,
            "date_import_label": row["date_import"].strftime("%Y-%m-%d %H:%M") if row.get("date_import") else "",
            "nb_lignes": row["nb_lignes"], "nb_erreurs": row["nb_erreurs"],
            "rows_count": row["nb_lignes"],
            "statut": row["statut"], "departement": row["departement"],
            "status": row["statut"],
            "importe_par": row["importe_par"],
            "details": _decode("details"),
            "data": data_value,
        })

    return jsonify({"success": True, "groups": [{"user_name": k, "items": v} for k, v in grouped.items()]})


@app.route("/api/etl/history/<int:import_id>", methods=["DELETE"])
@jwt_required
def delete_etl_history(import_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401
    try:
        run_update("DELETE FROM historique_imports WHERE id=%s AND user_id=%s", (import_id, user_id))
        return jsonify({"success": True, "message": "Import supprimé"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# ETL
# ═══════════════════════════════════════════════════════════════
@app.route("/api/etl/ping", methods=["GET"])
def etl_ping():
    return jsonify({"status": "ok", "message": "Backend Flask opérationnel"})


@app.route("/api/etl/upload", methods=["POST"])
@app.route("/api/upload", methods=["POST"])
@jwt_required
def etl_upload():
    user_id = get_current_user()
    if "file" not in request.files:
        log_audit(user_id, "api_access", "etl_upload", "failed", "Aucun fichier reçu")
        return jsonify({"error": "Aucun fichier reçu"}), 400
    file  = request.files["file"]
    fname = file.filename or ""
    if not any(fname.lower().endswith(e) for e in (".csv", ".xlsx", ".xls")):
        log_audit(user_id, "api_access", "etl_upload", "failed", {"filename": fname, "reason": "Format non supporte"})
        return jsonify({"error": "Format accepté : CSV, XLSX, XLS"}), 400

    safe_fname = re.sub(r"[^\w._-]", "_", fname)
    save_path  = UPLOAD_FOLDER / safe_fname
    file.save(str(save_path))
    try:
        df = read_table_rows(save_path)
        return jsonify({
            "success": True, "filename": safe_fname,
            "imported_rows": make_json_safe(df.to_dict(orient="records")),
            "stats": {"columns": make_json_safe(list(df.columns)), "rows_preview": len(df)},
        })
    except Exception as e:
        log_audit(user_id, "api_access", "etl_upload", "failed", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/etl/process", methods=["POST"])
@jwt_required
def etl_process():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Authentification requise"}), 401

    d        = request.get_json() or {}
    filename = d.get("filename")
    if not filename:
        log_audit(user_id, "import_donnees", "etl_process", "failed", "Nom de fichier manquant")
        return jsonify({"error": "Nom de fichier manquant"}), 400

    file_path = UPLOAD_FOLDER / filename
    if not file_path.exists():
        log_audit(user_id, "import_donnees", f"etl_file:{filename}", "failed", "Fichier non trouvé")
        return jsonify({"error": "Fichier non trouvé"}), 404

    try:
        result = run_generic_etl(str(file_path), replace_existing=True)
        safe   = lambda x: make_json_safe(x)

        if not result.get("success"):
            elog = safe(result.get("log", []))
            try:
                save_import_history(user_id, filename, {"lignes": 0, "nb_erreurs": 1}, elog, [], False)
            except Exception:
                pass
            log_audit(
                user_id,
                "import_donnees",
                f"etl_file:{filename}",
                "failed",
                {"error": result.get("error", "Erreur ETL"), "detail": result.get("detail")},
            )
            return jsonify({"success": False, "error": result.get("error", "Erreur ETL"),
                            "detail": result.get("detail"), "log": elog}), 500

        stats       = safe(result.get("stats", {}))
        log         = safe(result.get("log", []))
        after_rows  = safe(result.get("after_rows", []))

        try:
            import_id = save_import_history(user_id, filename, stats, log, after_rows, True)
        except Exception as he:
            print(f"[ETL] History insert failed: {he}")
            log_audit(
                user_id,
                "import_donnees",
                f"etl_file:{filename}",
                "failed",
                {"error": "Impossible d'enregistrer l'historique", "detail": str(he)},
            )
            return jsonify({
                "success": False,
                "error": "Nettoyage termine mais impossible d'enregistrer l'historique d'import.",
                "detail": str(he),
            }), 500

        log_audit(
            user_id,
            "import_donnees",
            f"import:{import_id}",
            "success",
            {
                "filename": filename,
                "rows_inserted": (result.get("db_result") or {}).get("rows_inserted", 0),
                "changed_rows": result.get("changed_rows", 0),
            },
        )

        return jsonify({
            "success": True, "log": log, "stats": stats,
            "before_rows": safe(result.get("before_rows", [])),
            "after_rows":  after_rows,
            "changed_rows": safe(result.get("changed_rows", 0)),
            "db_result":   safe(result.get("db_result", {})),
            "import_id": import_id,
        })
    except Exception as e:
        print("[ETL] Fatal:", traceback.format_exc())
        try:
            save_import_history(user_id, filename, {"lignes": 0, "nb_erreurs": 1},
                                [str(e), traceback.format_exc()], [], False)
        except Exception:
            pass
        log_audit(user_id, "import_donnees", f"etl_file:{filename or ''}", "failed", str(e))
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


@app.route("/api/etl/download", methods=["GET"])
@jwt_required
def etl_download():
    user_id = get_current_user()
    cleaned = UPLOAD_FOLDER / "donnees_nettoyees.csv"
    if not cleaned.exists():
        log_audit(user_id, "export_rapport", "etl_download", "failed", "Aucun fichier nettoyé disponible")
        return jsonify({"error": "Aucun fichier nettoyé disponible"}), 404
    log_audit(user_id, "export_rapport", "etl_download", "success", "Export du fichier nettoyé")
    return send_from_directory(str(UPLOAD_FOLDER), "donnees_nettoyees.csv",
                               as_attachment=True, mimetype="text/csv")


@app.route("/api/etl/table-data", methods=["GET"])
@jwt_required
def etl_table_data():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Authentification requise"}), 401
    filename = request.args.get("filename", "").strip()
    stage    = request.args.get("stage", "before").strip().lower()
    if not filename:
        return jsonify({"error": "Nom de fichier manquant"}), 400

    try:
        if stage == "before":
            src = UPLOAD_FOLDER / filename
            if not src.exists():
                return jsonify({"error": "Fichier source introuvable"}), 404
            df = read_table_rows(src)
        elif stage == "after":
            cleaned = UPLOAD_FOLDER / "donnees_nettoyees.csv"
            if not cleaned.exists():
                return jsonify({"error": "Fichier nettoyé introuvable"}), 404
            df = pd.read_csv(str(cleaned), encoding="utf-8-sig", low_memory=False)
        else:
            return jsonify({"error": "Stage invalide (before|after)"}), 400
        rows = make_json_safe(df.to_dict(orient="records"))
        return jsonify({"success": True, "stage": stage, "rows": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/etl/schema", methods=["GET"])
@admin_required
def etl_schema():
    try:
        from sqlalchemy import create_engine, inspect as sa_inspect
        from config import DB_CONFIG as dbc
        engine = create_engine(f"mysql+pymysql://{dbc['user']}:{dbc['password']}@{dbc.get('host','localhost')}/{dbc['database']}")
        insp   = sa_inspect(engine)
        return jsonify({"success": True, "tables": {
            t: [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(t)]
            for t in insp.get_table_names()
        }})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# KPI
# ═══════════════════════════════════════════════════════════════
@app.route("/api/kpi/save", methods=["POST"])
@jwt_required
def save_kpis():
    d          = request.get_json(force=True) or {}
    kpis       = d.get("kpis", [])
    source     = d.get("source", "etl")
    do_replace = d.get("replace", False)
    if not kpis:
        return jsonify({"success": False, "error": "Aucun KPI fourni"}), 400

    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        inserted = replaced = 0
        for kpi in kpis:
            nom    = str(kpi.get("kpiNom", "")).strip()
            per    = str(kpi.get("periode", "global")).strip()
            val    = float(kpi.get("valeur", 0))
            evo    = float(kpi.get("evolution", 0))
            dept   = kpi.get("departementId")
            stype  = str(kpi.get("stat_type", "sum")).strip()
            if not nom:
                continue
            if do_replace:
                cur.execute("DELETE FROM valeur_kpi WHERE kpiNom=%s AND periode=%s", (nom, per))
                replaced += cur.rowcount
            cur.execute(
                "INSERT INTO valeur_kpi (kpiNom,periode,valeur,evolution,departementId,source,stat_type) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (nom, per, val, evo, dept, source, stype),
            )
            inserted += 1
        conn.commit()
        cur.close()
        return jsonify({"success": True, "inserted": inserted, "replaced": replaced,
                        "message": f"{inserted} KPI(s) sauvegardés"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/kpi", methods=["GET"])
@app.route("/api/kpis", methods=["GET"])
@jwt_required
def get_kpis():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Authentification requise"}), 401

    imp, rows = _get_latest_import_dataset(user_id)
    if not imp or not rows:
        return _no_import_response()

    kpi_nom = (request.args.get("kpiNom") or "").strip().lower()
    periode = (request.args.get("periode") or "").strip().lower()
    limit = max(1, _to_int(request.args.get("limit", 100), 100))

    kpis = _build_kpis_from_rows(rows)
    if kpi_nom:
        kpis = [k for k in kpis if kpi_nom in str(k.get("kpiNom", "")).strip().lower()]
    if periode:
        kpis = [k for k in kpis if str(k.get("periode", "")).strip().lower() == periode]

    kpis = kpis[:limit]
    log_audit(user_id, "api_access", "kpi_list", "success", {"import_id": imp["id"], "rows": len(kpis)})
    return jsonify({
        "success": True,
        "import_id": imp["id"],
        "kpis": make_json_safe(kpis),
        "total": len(kpis),
    })


# ═══════════════════════════════════════════════════════════════
# PRÉVISIONS
# ═══════════════════════════════════════════════════════════════
@app.route("/api/previsions", methods=["POST"])
@jwt_required
def create_prevision():
    d         = request.get_json(force=True) or {}
    user_id   = get_current_user()
    type_prev = (d.get("type") or "").strip()
    date_deb  = d.get("dateDebut")
    date_fin  = d.get("dateFin")
    resultats = d.get("resultats", {})
    dept_id   = d.get("departementId")

    if not all([type_prev, date_deb, date_fin]):
        return jsonify({"success": False, "error": "type, dateDebut et dateFin sont requis"}), 400
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO previsions (type,dateDebut,dateFin,resultats,departementId,created_by) VALUES (%s,%s,%s,%s,%s,%s)",
            (type_prev, date_deb, date_fin, json.dumps(resultats, ensure_ascii=False), dept_id, user_id),
        )
        conn.commit()
        new_id = cur.lastrowid
        cur.close()
        return jsonify({"success": True, "id": new_id, "message": f"Prévision #{new_id} créée"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/previsions", methods=["GET"])
@jwt_required
def get_previsions():
    dept_id = request.args.get("departementId")
    type_p  = request.args.get("type")
    limit   = int(request.args.get("limit", 50))
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor(dictionary=True)
        q, p = "SELECT * FROM previsions WHERE 1=1", []
        if dept_id: q += " AND departementId=%s"; p.append(dept_id)
        if type_p:  q += " AND type=%s";          p.append(type_p)
        q += " ORDER BY created_at DESC LIMIT %s"; p.append(limit)
        cur.execute(q, p)
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for f in ("created_at", "updated_at"):
                if r.get(f): r[f] = r[f].isoformat()
            for f in ("dateDebut", "dateFin"):
                if r.get(f): r[f] = str(r[f])
            if isinstance(r.get("resultats"), str):
                try: r["resultats"] = json.loads(r["resultats"])
                except Exception: pass
        return jsonify({"success": True, "previsions": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/previsions/<int:prev_id>", methods=["DELETE"])
@jwt_required
def delete_prevision(prev_id):
    conn = None
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("DELETE FROM previsions WHERE id=%s", (prev_id,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        if deleted:
            return jsonify({"success": True, "message": f"Prévision #{prev_id} supprimée"})
        return jsonify({"success": False, "error": "Prévision non trouvée"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


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
    }


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


@app.route("/api/dashboard/summary", methods=["GET"])
@jwt_required
def dashboard_summary():
    started = time.perf_counter()
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Auth requis"}), 401

    try:
        source = "transactions_sql"
        summary = _query_dashboard_summary_sql()

        if not summary or int(summary.get("tx_count") or 0) == 0:
            imp, rows = _get_latest_import_dataset(user_id)
            if not imp or not rows:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_audit(user_id, "api_access", "dashboard_summary", "failed", {"response_ms": elapsed_ms, "reason": "Aucun import disponible"})
                return _no_import_response()
            summary = _build_dashboard_summary_from_rows(rows)
            summary["import_id"] = imp.get("id")
            summary["filename"] = imp.get("nom_fichier")
            source = "historique_imports"

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        payload = {
            "success": True,
            "source": source,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "response_ms": elapsed_ms,
            "kpis": {
                "transactions": int(summary.get("tx_count") or 0),
                "revenus_totaux": round(_to_float(summary.get("revenus_totaux"), 0.0), 2),
                "depenses_totales": round(_to_float(summary.get("depenses_totales"), 0.0), 2),
                "solde_net": round(_to_float(summary.get("solde_net"), 0.0), 2),
                "marge_pct": round(_to_float(summary.get("marge_pct"), 0.0), 2),
            },
            "period_stats": make_json_safe(summary.get("period_stats") or []),
            "departement_stats": make_json_safe(summary.get("departement_stats") or []),
        }
        if summary.get("import_id"):
            payload["import_id"] = summary.get("import_id")
            payload["filename"] = summary.get("filename")

        log_audit(user_id, "api_access", "dashboard_summary", "success", {"response_ms": elapsed_ms, "source": source})
        if elapsed_ms > 3000:
            log_audit(user_id, "erreur_systeme", "dashboard_summary", "failed", {"response_ms": elapsed_ms, "threshold_ms": 3000})

        return jsonify(payload)
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        log_audit(user_id, "erreur_systeme", "dashboard_summary", "failed", {"response_ms": elapsed_ms, "error": str(e)})
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════
@app.route("/api/analytics/data", methods=["GET"])
@app.route("/api/charts", methods=["GET"])
@jwt_required
def analytics_data():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Auth requis"}), 401
    imp, rows = _get_latest_import_dataset(user_id)
    if not imp or not rows:
        log_audit(user_id, "api_access", "analytics_data", "failed", "Aucun import disponible")
        return _no_import_response()

    log_audit(user_id, "api_access", "analytics_data", "success", {"import_id": imp["id"], "rows": len(rows)})
    return jsonify({
        "success": True,
        "analytics_ready": True,
        "message": "",
        "import_id": imp["id"],
        "filename": imp.get("nom_fichier"),
        "status": imp.get("statut"),
        "imported_at": imp.get("date_import").isoformat() if imp.get("date_import") else None,
        "import": {
            "id": imp["id"],
            "filename": imp.get("nom_fichier"),
            "status": imp.get("statut"),
            "imported_at": imp.get("date_import").isoformat() if imp.get("date_import") else None,
            "rows_count": imp.get("nb_lignes"),
        },
        "total": len(rows),
        "data": make_json_safe(rows),
    })


@app.route("/api/analytics/kpi-refresh", methods=["POST"])
@jwt_required
def kpi_refresh():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401
    imp, rows = _get_latest_import_dataset(user_id)
    if not imp or not rows:
        log_audit(user_id, "generation_rapport", "analytics_kpi_refresh", "failed", "Aucun import disponible")
        return _no_import_response()

    kpis = _build_kpis_from_rows(rows)
    log_audit(
        user_id,
        "generation_rapport",
        "analytics_kpi_refresh",
        "success",
        {"import_id": imp["id"], "kpi_count": len(kpis)},
    )
    return jsonify({
        "success": True,
        "import_id": imp["id"],
        "inserted": len(kpis),
        "message": "KPI recalcules a partir du dernier import",
    })



# ═══════════════════════════════════════════════════════════════
# ML DATA HELPERS
# ═══════════════════════════════════════════════════════════════
def _fetch_ml_dataframe(user_id, with_import=False):
    imp, rows = _get_latest_import_dataset(user_id)
    if not rows:
        return (imp, pd.DataFrame()) if with_import else pd.DataFrame()

    df = pd.DataFrame(rows)
    if df.empty:
        return (imp, df) if with_import else df

    if "client_fournisseur" in df.columns:
        df["fournisseur"] = df["client_fournisseur"]
    else:
        df["fournisseur"] = "Inconnu"

    if "cf_type" in df.columns:
        df["partenaire_type"] = df["cf_type"]
    else:
        df["partenaire_type"] = "Inconnu"

    if "departement_id" not in df.columns:
        df["departement_id"] = None

    if "transaction_id" not in df.columns:
        df["transaction_id"] = df.get("Transaction_ID")

    df["Montant"] = pd.to_numeric(df["Montant"], errors="coerce").fillna(0.0)
    df["Montant_Signe"] = pd.to_numeric(df["Montant_Signe"], errors="coerce").fillna(0.0)
    df["date_val"] = pd.to_datetime(df["date_val"], errors="coerce")

    for col in ["departement", "type_transaction", "type_depense", "responsable", "fournisseur", "partenaire_type", "projet", "year_month"]:
        if col in df.columns:
            df[col] = df[col].fillna("Inconnu").astype(str).str.strip()
            df.loc[df[col] == "", col] = "Inconnu"

    final_df = df.dropna(subset=["date_val"]).copy()
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
    if len(series_df) >= 4:
        split = max(2, len(series_df) - 2)
        train_X, test_X = X[:split], X[split:]
        train_y, test_y = y[:split], y[split:]
        test_model = LinearRegression()
        test_model.fit(train_X, train_y)
        pred_test = test_model.predict(test_X)
        mae = float(mean_absolute_error(test_y, pred_test))

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
        "model": {"name": "LinearRegression", "cv_mae": round(mae, 2) if mae is not None else None},
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


@app.route("/api/assistance/ml-forecast", methods=["GET"])
@jwt_required
def assistance_ml_forecast():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Auth requis"}), 401

    trend_type = (request.args.get("type") or "seasonality_expenses").strip()
    try:
        horizon = int(request.args.get("horizon", 3))
    except Exception:
        horizon = 3
    horizon = max(1, min(horizon, 12))

    try:
        df = _fetch_ml_dataframe(user_id)
        if df.empty:
            log_audit(user_id, "generation_rapport", "assistance_ml_forecast", "failed", "Aucun import disponible")
            return _no_import_response()

        series_df, meta = _build_trend_series(df, trend_type)
        if series_df.empty or len(series_df) < 2:
            log_audit(user_id, "generation_rapport", "assistance_ml_forecast", "failed", "Pas assez d'historique")
            return jsonify({"success": False, "error": "Pas assez d'historique pour cette vue"}), 400

        result = _linear_forecast(series_df, horizon=horizon, non_negative=meta["non_negative"])
        plain_language = _plain_summary(df, trend_type, meta["entity"], result["summary"])
        log_audit(
            user_id,
            "generation_rapport",
            "assistance_ml_forecast",
            "success",
            {"type": trend_type, "horizon": horizon, "entity": meta.get("entity")},
        )

        return jsonify(make_json_safe({
            "success": True,
            "type": trend_type,
            "title": meta["title"],
            "selection": {"entity": meta["entity"]},
            "history": result["history"],
            "forecast": result["forecast"],
            "model": result["model"],
            "summary": result["summary"],
            "plain_language": plain_language,
        }))
    except Exception as e:
        log_audit(user_id, "generation_rapport", "assistance_ml_forecast", "failed", str(e))
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


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
    expense_total = float(row.get("expense_total", 0) or 0)
    revenue_total = float(row.get("revenue_total", 0) or 0)
    net_total = float(row.get("net_total", 0) or 0)
    std_amount = float(row.get("std_amount", 0) or 0)
    active_months = float(row.get("active_months", 0) or 0)

    if entity == "fournisseur":
        partner_type = str(row.get("partenaire_type", "")).lower()
        if "client" in partner_type and revenue_total >= metrics["rev_q75"] and net_total > 0:
            return "Partenaires générateurs de revenus", "faible"
        if expense_total >= metrics["exp_q75"] and std_amount >= metrics["std_q60"]:
            return "Partenaires coûteux et volatils", "élevé"
        if expense_total >= metrics["exp_q75"]:
            return "Partenaires coûteux réguliers", "moyen"
        if active_months <= metrics["months_q25"] and volume <= metrics["vol_q25"]:
            return "Partenaires secondaires", "faible"
        return "Partenaires équilibrés", "moyen"

    if entity == "projet":
        if net_total >= metrics["net_q75"] and active_months >= metrics["months_q60"]:
            return "Projets rentables stables", "faible"
        if net_total <= metrics["net_q25"]:
            return "Projets à risque", "élevé"
        if std_amount >= metrics["std_q60"]:
            return "Projets volatils", "moyen"
        return "Projets intermédiaires", "moyen"

    if entity == "departement":
        if expense_total >= metrics["exp_q75"] and net_total <= metrics["net_q40"]:
            return "Départements à forte consommation", "élevé"
        if abs(net_total) <= metrics["abs_net_q40"]:
            return "Départements équilibrés", "faible"
        if std_amount >= metrics["std_q60"]:
            return "Départements volatils", "moyen"
        return "Départements stables", "faible"

    return "Profil standard", "moyen"


def _build_multi_segmentation_payload(df, entity, k):
    agg = _build_entity_aggregate(df, entity)
    if agg.empty or len(agg) < 3:
        raise ValueError("Pas assez d'éléments pour segmenter cette entité")

    numeric_cols = [
        "tx_count", "total_volume", "expense_total", "revenue_total",
        "net_total", "avg_amount", "std_amount", "active_months",
        "monthly_frequency", "expense_ratio", "revenue_ratio", "net_margin"
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

    metrics = {
        "vol_q25": float(agg["total_volume"].quantile(0.25)),
        "exp_q75": float(agg["expense_total"].quantile(0.75)),
        "rev_q75": float(agg["revenue_total"].quantile(0.75)),
        "net_q75": float(agg["net_total"].quantile(0.75)),
        "net_q25": float(agg["net_total"].quantile(0.25)),
        "net_q40": float(agg["net_total"].quantile(0.40)),
        "abs_net_q40": float(agg["net_total"].abs().quantile(0.40)),
        "std_q60": float(agg["std_amount"].quantile(0.60)),
        "months_q25": float(agg["active_months"].quantile(0.25)),
        "months_q60": float(agg["active_months"].quantile(0.60)),
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


@app.route("/api/ml/segmentation/run", methods=["POST"])
@app.route("/api/ml/segmentation-multi", methods=["POST"])
@jwt_required
def ml_segmentation_multi():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401

    try:
        payload = request.get_json(silent=True) or {}
        entity = (payload.get("entity") or "fournisseur").strip().lower()
        k = int(payload.get("k", 4) or 4)

        if entity not in {"fournisseur", "projet", "departement"}:
            return jsonify({"success": False, "error": "Entité non supportée"}), 400

        df = _fetch_ml_dataframe(user_id)
        if df.empty:
            return _no_import_response()
        if len(df) < 20:
            return jsonify({
                "success": False,
                "error": "Pas assez de données pour lancer la segmentation."
            }), 400

        return jsonify(make_json_safe(_build_multi_segmentation_payload(df, entity, k)))
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "detail": traceback.format_exc()
        }), 500


# ═══════════════════════════════════════════════════════════════
# CHATBOT
# ═══════════════════════════════════════════════════════════════
@app.route("/api/chatbot/ask", methods=["POST"])
@jwt_required
def chatbot_ask():
    try:
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question") or payload.get("message") or "").strip()
        user_id = get_current_user()
        answer = _generate_classic_chatbot_answer(user_id, question)
        return jsonify({"success": True, "answer": answer})
    except Exception:
        return jsonify({
            "success": True,
            "answer": "Je n'ai pas pu répondre pour le moment. Vérifiez vos données puis réessayez.",
        })


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

    try:
        if _table_exists("transactions"):
            totals = run_query(
                """
                SELECT
                    COUNT(*) AS total_transactions,
                    COALESCE(SUM(CASE WHEN Montant_Signe > 0 THEN Montant_Signe ELSE 0 END), 0) AS revenus_totaux,
                    COALESCE(SUM(CASE WHEN Montant_Signe < 0 THEN ABS(Montant_Signe) ELSE 0 END), 0) AS depenses_totales,
                    COALESCE(SUM(Montant_Signe), 0) AS solde_net
                FROM transactions
                """,
                fetch_one=True,
            ) or {}
            context["total_transactions"] = int(totals.get("total_transactions") or 0)
            context["revenus_totaux"] = _to_float(totals.get("revenus_totaux"), 0.0)
            context["depenses_totales"] = _to_float(totals.get("depenses_totales"), 0.0)
            context["solde_net"] = _to_float(totals.get("solde_net"), 0.0)

            dept_row = run_query("SELECT COUNT(DISTINCT Departement_ID) AS n FROM transactions", fetch_one=True) or {}
            context["departements_actifs"] = int(dept_row.get("n") or 0)

            has_date = _table_exists("date")
            has_dep = _table_exists("departement")
            has_tt = _table_exists("typetransaction")
            has_td = _table_exists("typedepense")
            has_resp = _table_exists("responsable")
            has_cf = _table_exists("clientfournisseur")
            has_proj = _table_exists("projet")

            latest_sql = [
                "SELECT",
                "t.Transaction_ID AS transaction_id,",
                ("d.`Date` AS date_transaction," if has_date else "NULL AS date_transaction,"),
                "ROUND(COALESCE(t.Montant, 0), 3) AS montant,",
                "ROUND(COALESCE(t.Montant_Signe, 0), 3) AS montant_signe,",
                ("COALESCE(dep.NomDepartement, 'Non renseigne') AS departement," if has_dep else "CONCAT('Departement #', COALESCE(t.Departement_ID, 0)) AS departement,"),
                ("COALESCE(tt.TypeTransaction, 'N/A') AS type_transaction," if has_tt else "CONCAT('Type #', COALESCE(t.TypeTransaction_ID, 0)) AS type_transaction,"),
                ("COALESCE(td.TypeDepense, 'N/A') AS type_depense," if has_td else "CONCAT('Depense #', COALESCE(t.TypeDepense_ID, 0)) AS type_depense,"),
                ("COALESCE(r.NomResponsable, 'Non renseigne') AS responsable," if has_resp else "CONCAT('Responsable #', COALESCE(t.Responsable_ID, 0)) AS responsable,"),
                ("COALESCE(cf.NomClientFournisseur, 'Non renseigne') AS client_fournisseur," if has_cf else "CONCAT('Tiers #', COALESCE(t.ClientFournisseur_ID, 0)) AS client_fournisseur,"),
                ("COALESCE(p.NomProjet, 'Sans projet') AS projet" if has_proj else "CONCAT('Projet #', COALESCE(t.Projet_ID, 0)) AS projet"),
                "FROM transactions t",
            ]
            if has_date:
                latest_sql.append("LEFT JOIN `date` d ON d.Date_ID = t.Date_ID")
            if has_dep:
                latest_sql.append("LEFT JOIN departement dep ON dep.Departement_ID = t.Departement_ID")
            if has_tt:
                latest_sql.append("LEFT JOIN typetransaction tt ON tt.TypeTransaction_ID = t.TypeTransaction_ID")
            if has_td:
                latest_sql.append("LEFT JOIN typedepense td ON td.TypeDepense_ID = t.TypeDepense_ID")
            if has_resp:
                latest_sql.append("LEFT JOIN responsable r ON r.Responsable_ID = t.Responsable_ID")
            if has_cf:
                latest_sql.append("LEFT JOIN clientfournisseur cf ON cf.ClientFournisseur_ID = t.ClientFournisseur_ID")
            if has_proj:
                latest_sql.append("LEFT JOIN projet p ON p.Projet_ID = t.Projet_ID")
            latest_sql.append("ORDER BY")
            latest_sql.append(("d.`Date` DESC," if has_date else "t.Transaction_ID DESC,"))
            latest_sql.append("t.Transaction_ID DESC")
            latest_sql.append("LIMIT 5")

            latest_rows = run_query("\n".join(latest_sql)) or []
            for row in latest_rows:
                dt = row.get("date_transaction")
                if dt and hasattr(dt, "strftime"):
                    row["date_transaction"] = dt.strftime("%Y-%m-%d")
            context["dernieres_transactions"] = latest_rows

            top_sql = [
                "SELECT",
                ("COALESCE(dep.NomDepartement, CONCAT('Departement #', COALESCE(t.Departement_ID, 0))) AS departement," if has_dep else "CONCAT('Departement #', COALESCE(t.Departement_ID, 0)) AS departement,"),
                "ROUND(COALESCE(SUM(CASE WHEN t.Montant_Signe > 0 THEN t.Montant_Signe ELSE 0 END), 0), 3) AS revenus,",
                "ROUND(COALESCE(SUM(CASE WHEN t.Montant_Signe < 0 THEN ABS(t.Montant_Signe) ELSE 0 END), 0), 3) AS depenses,",
                "ROUND(COALESCE(SUM(t.Montant_Signe), 0), 3) AS solde_net,",
                "COUNT(*) AS nb_transactions",
                "FROM transactions t",
            ]
            if has_dep:
                top_sql.append("LEFT JOIN departement dep ON dep.Departement_ID = t.Departement_ID")
            top_sql.append("GROUP BY t.Departement_ID" + (", dep.NomDepartement" if has_dep else ""))
            top_sql.append("ORDER BY ABS(COALESCE(SUM(t.Montant_Signe), 0)) DESC")
            top_sql.append("LIMIT 5")
            context["top_departements"] = run_query("\n".join(top_sql)) or []
    except Exception as e:
        context["context_error"] = str(e)

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

    try:
        if user_id:
            imp, rows = _get_latest_import_dataset(user_id)
            if imp and rows:
                import_revenus = sum(_to_float(r.get("Montant_Signe"), 0.0) for r in rows if _to_float(r.get("Montant_Signe"), 0.0) > 0)
                import_depenses = abs(sum(_to_float(r.get("Montant_Signe"), 0.0) for r in rows if _to_float(r.get("Montant_Signe"), 0.0) < 0))
                context["latest_import"] = {
                    "import_id": imp.get("id"),
                    "filename": imp.get("nom_fichier"),
                    "imported_at": imp.get("date_import").strftime("%Y-%m-%d %H:%M:%S") if imp.get("date_import") else None,
                    "rows_count": len(rows),
                    "revenus": round(import_revenus, 3),
                    "depenses": round(import_depenses, 3),
                    "solde_net": round(import_revenus - import_depenses, 3),
                }
    except Exception:
        context["latest_import"] = None

    return make_json_safe(context)


def _context_to_prompt_text(context: dict) -> str:
    lines = [
        f"Transactions totales (table transactions): {int(context.get('total_transactions') or 0)}",
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
@app.route("/api/chatbot/ask-ai", methods=["POST"])
@jwt_required
def chatbot_ask_ai():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or payload.get("message") or "").strip()
    print("ASK AI QUESTION:", question)
    if not question:
        return jsonify({"success": False, "error": "Question vide"}), 400

    user_id = get_current_user()
    context = get_financial_context(user_id)
    context_text = _context_to_prompt_text(context)
    system_prompt = (
        SYSTEM_PROMPT
        + "\n\n"
        + "Contrainte de réponse: utilise les chiffres du contexte si disponibles. "
        + "Si les données sont absentes, indique clairement qu'aucune donnée exploitable n'est disponible."
    )

    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Question utilisateur: {question}\n\n"
                    f"Contexte financier réel:\n{context_text}\n\n"
                    "Réponds de manière précise pour Finova et pfe_bd."
                ),
            },
        ],
        "options": {
            "temperature": 0.2,
            "num_predict": 300,
        },
    }

    try:
        resp = requests.post(OLLAMA_CHAT_URL, json=body, timeout=45)
    except requests.exceptions.Timeout:
        return jsonify({
            "success": False,
            "error": "Délai dépassé: Ollama met trop de temps à répondre.",
        }), 504
    except requests.exceptions.ConnectionError:
        return jsonify({
            "success": False,
            "error": "Ollama est indisponible. Vérifiez que le service est démarré.",
        }), 503
    except Exception:
        return jsonify({
            "success": False,
            "error": "Erreur interne lors de l'appel à Ollama.",
        }), 500

    if resp.status_code >= 400:
        err_msg = "Erreur Ollama"
        try:
            err_json = resp.json() or {}
            err_msg = str(err_json.get("error") or err_json.get("message") or err_msg)
        except Exception:
            err_msg = f"Erreur Ollama (HTTP {resp.status_code})"
        return jsonify({
            "success": False,
            "error": err_msg,
        }), 502

    try:
        data = resp.json() or {}
    except Exception:
        return jsonify({
            "success": False,
            "error": "Réponse invalide reçue depuis Ollama.",
        }), 502

    answer = str((data.get("message") or {}).get("content") or data.get("response") or "").strip()
    if not answer:
        return jsonify({
            "success": False,
            "error": "Ollama n'a pas retourné de réponse exploitable.",
        }), 502

    return jsonify({
        "success": True,
        "answer": answer,
        "model": OLLAMA_MODEL,
    })


@app.route("/api/ollama/status", methods=["GET"])
def ollama_status():
    available_models = []
    running = False

    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=8)
        running = resp.status_code < 500
        if resp.ok:
            payload = resp.json() or {}
            models = payload.get("models") or []
            available_models = [str(m.get("name")) for m in models if m.get("name")]
    except Exception:
        running = False
        available_models = []

    configured = OLLAMA_MODEL
    selected = configured if configured in available_models else None
    if not selected and available_models:
        configured_family = configured.split(":")[0]
        selected = next((m for m in available_models if m.split(":")[0] == configured_family), available_models[0])

    return jsonify({
        "running": running,
        "available_models": available_models,
        "selected_model": selected,
        "configured_model": configured,
    })

def _impact_severity(impact_percent):
    impact_percent = abs(float(impact_percent or 0))
    if impact_percent >= 20:
        return "danger", "Impact élevé"
    if impact_percent >= 8:
        return "warning", "Impact modéré"
    return "success", "Impact faible"


def _fmt_dt_value(value):
    return f"{float(value):,.0f} DT".replace(",", " ")


@app.route("/api/assistance/simulate", methods=["POST"])
@jwt_required
def simulate_what_if():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Auth requis"}), 401

    try:
        payload = request.get_json(silent=True) or {}
        scenario = (payload.get("scenario") or "matieres_premieres").strip()
        imp, df = _fetch_ml_dataframe(user_id, with_import=True)
        if not imp or df.empty:
            return _no_import_response()

        baseline_solde = float(df["Montant_Signe"].sum())
        baseline_ref = max(abs(baseline_solde), float(df["Montant_Signe"].abs().sum()) / 4, 1.0)

        delta_solde = 0.0
        headline = ""
        recommendation = ""
        details = []

        if scenario == "matieres_premieres":
            percent = float(payload.get("percent", 10) or 10)
            target = df[(df["Montant_Signe"] < 0) & (df["type_depense"].str.contains("mati", case=False, na=False))]
            if target.empty:
                top_type = _top_value(df, "type_depense", "expense")
                target = df[(df["Montant_Signe"] < 0) & (df["type_depense"] == top_type)]
            current_cost = float(target["Montant_Signe"].abs().sum())
            delta_solde = -(current_cost * (percent / 100.0))
            headline = "Hausse du coût des matières premières"
            recommendation = "Surveillez l'achat des intrants, renégociez les contrats ou lissez les commandes."
            details = [
                {"label": "Poste simulé", "value": target["type_depense"].mode().iloc[0] if not target.empty else "Matières premières"},
                {"label": "Coût actuel", "value": _fmt_dt_value(current_cost)},
                {"label": "Variation", "value": f"+{percent:.0f}%"},
            ]

        elif scenario == "transfert_budget":
            percent = float(payload.get("percent", 5) or 5)
            source_dep = str(payload.get("source_department") or "Inconnu")
            target_dep = str(payload.get("target_department") or "Inconnu")
            expense_type = str(payload.get("expense_type") or "Inconnu")
            source_rows = df[(df["departement"] == source_dep) & (df["type_depense"] == expense_type) & (df["Montant_Signe"] < 0)]
            current_cost = float(source_rows["Montant_Signe"].abs().sum())
            transferred = current_cost * (percent / 100.0)
            delta_solde = 0.0
            headline = "Transfert budgétaire sans effet sur le solde global"
            recommendation = "Le solde global ne change pas, mais la pression budgétaire se déplace entre deux départements."
            details = [
                {"label": "Département source", "value": source_dep},
                {"label": "Département cible", "value": target_dep},
                {"label": "Montant transféré", "value": _fmt_dt_value(transferred)},
            ]

        elif scenario == "baisse_commissions":
            percent = float(payload.get("percent", 15) or 15)
            department = str(payload.get("department") or "Inconnu")
            target = df[(df["departement"] == department) & (df["Montant_Signe"] < 0) & (df["type_depense"].str.contains("commission", case=False, na=False))]
            current_cost = float(target["Montant_Signe"].abs().sum())
            delta_solde = current_cost * (percent / 100.0)
            headline = "Réduction des commissions"
            recommendation = "Révisez la politique de commission si la marge brute est trop faible."
            details = [
                {"label": "Département", "value": department},
                {"label": "Commissions actuelles", "value": _fmt_dt_value(current_cost)},
                {"label": "Réduction simulée", "value": f"-{percent:.0f}%"},
            ]

        elif scenario == "hausse_revenu_client":
            percent = float(payload.get("percent", 20) or 20)
            client_name = str(payload.get("client_fournisseur") or "Inconnu")
            target = df[(df["fournisseur"] == client_name) & (df["Montant_Signe"] > 0)]
            current_revenue = float(target["Montant_Signe"].sum())
            delta_solde = current_revenue * (percent / 100.0)
            headline = "Hausse des revenus d'un client"
            recommendation = "Concentrez les actions commerciales sur les comptes qui génèrent déjà du revenu."
            details = [
                {"label": "Client / partenaire", "value": client_name},
                {"label": "Revenus actuels", "value": _fmt_dt_value(current_revenue)},
                {"label": "Hausse simulée", "value": f"+{percent:.0f}%"},
            ]

        elif scenario == "depense_to_revenu":
            expense_type = str(payload.get("expense_type") or "Inconnu")
            amount = float(payload.get("amount", 0) or 0)
            delta_solde = amount * 2.0
            headline = "Conversion d'une dépense en revenu"
            recommendation = "Utilisez ce scénario pour simuler une subvention, un remboursement ou une reclassification."
            details = [
                {"label": "Type concerné", "value": expense_type},
                {"label": "Montant converti", "value": _fmt_dt_value(amount)},
                {"label": "Effet comptable", "value": f"+{_fmt_dt_value(delta_solde)} sur le solde"},
            ]

        else:
            return jsonify({"error": "Scénario non supporté"}), 400

        new_solde = baseline_solde + delta_solde
        impact_percent = (abs(delta_solde) / baseline_ref) * 100.0
        severity, severity_label = _impact_severity(impact_percent)

        return jsonify({
            "success": True,
            "import": {
                "id": imp["id"],
                "filename": imp.get("nom_fichier"),
                "status": imp.get("statut"),
                "imported_at": imp.get("date_import").isoformat() if imp.get("date_import") else None,
                "rows_count": imp.get("nb_lignes"),
            },
            "baseline_solde": round(baseline_solde, 2),
            "delta_solde": round(delta_solde, 2),
            "new_solde": round(new_solde, 2),
            "impact_percent": round(impact_percent, 2),
            "severity": severity,
            "headline": headline,
            "recommendation": recommendation,
            "details": details + [
                {"label": "Niveau d'impact", "value": severity_label},
                {"label": "Nouveau solde", "value": _fmt_dt_value(new_solde)},
            ],
        })
    except Exception as e:
        return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500

# ═══════════════════════════════════════════════════════════════
# CONTACT
# ═══════════════════════════════════════════════════════════════
@app.route("/api/contact", methods=["POST"])
def contact():
    d = request.get_json() or {}
    if not all((d.get("name") or "").strip(), (d.get("email") or "").strip(),
               (d.get("subject") or "").strip(), (d.get("message") or "").strip()):
        return jsonify({"success": False, "error": "Tous les champs sont requis"}), 400
    return jsonify({"success": True, "message": "Message envoyé avec succès"}), 201


# ═══════════════════════════════════════════════════════════════
# INIT TABLES (debug)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/init-tables", methods=["POST"])
@admin_required
def init_tables():
    try:
        create_tables()
        return jsonify({"success": True, "message": "Tables créées ou déjà existantes"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# STATIC
# ═══════════════════════════════════════════════════════════════
@app.route("/")
def home():
    return send_from_directory(FRONTEND, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(FRONTEND, filename)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    create_tables()
    print(app.url_map)
    app.run(host="0.0.0.0", port=5000, debug=True)





