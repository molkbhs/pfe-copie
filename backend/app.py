# -*- coding: utf-8 -*-
"""
BusinessApp — Flask Backend
Toutes les routes sont ici : auth, profil, admin, ETL, KPI, prévisions, analytics.
Aucun Blueprint, aucun dossier routes/ nécessaire.
"""

import base64
import gc
import gzip
import hashlib
import importlib.util as _ilu
import json
import math
import os
import re
import secrets
import shutil
import smtplib
import time
import traceback
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import numpy as np
import pandas as pd
from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, jsonify, redirect, request, send_from_directory, url_for
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = "super_secret_key"

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

# ETL engine — chargé une seule fois au démarrage
_spec = _ilu.spec_from_file_location("etl_generic", str(_base / "etl_generic.py"))
_etl_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_etl_mod)
run_generic_etl = _etl_mod.run_generic_etl

# ═══════════════════════════════════════════════════════════════
# GOOGLE OAUTH
# ═══════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════
# AUTH HELPER
# ═══════════════════════════════════════════════════════════════
def get_current_user() -> int | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token.isdigit():
            return int(token)
        try:
            return int(json.loads(token).get("id", 0)) or None
        except Exception:
            return None
    try:
        data = request.get_json(silent=True) or {}
        uid = data.get("user_id") or request.args.get("user_id")
        return int(uid) if uid else None
    except Exception:
        return None


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
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                user_id    INT NOT NULL,
                token_hash CHAR(64) NOT NULL UNIQUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                used_at    DATETIME NULL,
                INDEX idx_password_reset_user (user_id),
                INDEX idx_password_reset_exp (expires_at),
                CONSTRAINT fk_password_reset_user
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'valeur_kpi'
              AND index_name = 'idx_valeur_kpi_identity'
        """)
        if int((cur.fetchone() or (0,))[0] or 0) == 0:
            cur.execute("""
                CREATE INDEX idx_valeur_kpi_identity
                ON valeur_kpi (kpiNom, periode, departementId, source, stat_type)
            """)
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `departement` (
                `Departement_ID` INT PRIMARY KEY,
                `NomDepartement` VARCHAR(255) NOT NULL UNIQUE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `typetransaction` (
                `TypeTransaction_ID` INT PRIMARY KEY,
                `TypeTransaction` VARCHAR(255) NOT NULL UNIQUE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `typedepense` (
                `TypeDepense_ID` INT PRIMARY KEY,
                `TypeDepense` VARCHAR(255) NOT NULL UNIQUE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `date` (
                `Date_ID` INT PRIMARY KEY,
                `Date` DATE NOT NULL UNIQUE,
                `Année` INT NULL,
                `Mois` INT NULL,
                `Trimestre` INT NULL,
                `AnnéeFiscale` INT NULL,
                `JourSemaine` VARCHAR(32) NULL,
                `Semaine` INT NULL,
                `JourAnnée` INT NULL,
                `YearMonth` VARCHAR(16) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `responsable` (
                `Responsable_ID` INT PRIMARY KEY,
                `NomResponsable` VARCHAR(255) NOT NULL,
                `Departement` VARCHAR(255) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `clientfournisseur` (
                `ClientFournisseur_ID` INT PRIMARY KEY,
                `NomClientFournisseur` VARCHAR(255) NOT NULL UNIQUE,
                `Type` VARCHAR(64) NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `projet` (
                `Projet_ID` INT PRIMARY KEY,
                `NomProjet` VARCHAR(255) NOT NULL UNIQUE,
                `DateDebut` DATE NULL,
                `DateFin` DATE NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `transactions` (
                `Transaction_ID` INT PRIMARY KEY,
                `Date_ID` INT NULL,
                `Departement_ID` INT NULL,
                `TypeTransaction_ID` INT NULL,
                `TypeDepense_ID` INT NULL,
                `Responsable_ID` INT NULL,
                `ClientFournisseur_ID` INT NULL,
                `Projet_ID` INT NULL,
                `Montant` DECIMAL(18,3) NULL,
                `Montant_Signe` DECIMAL(18,3) NULL,
                INDEX `idx_tx_date` (`Date_ID`),
                INDEX `idx_tx_dep` (`Departement_ID`),
                INDEX `idx_tx_tt` (`TypeTransaction_ID`),
                INDEX `idx_tx_td` (`TypeDepense_ID`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        conn.commit()
        cur.close()
        print("[app] Tables OK")
    except Exception as e:
        print(f"[app] create_tables warning: {e}")
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
def _oauth_html(user_data: dict, redirect_url: str) -> str:
    return f"""<!DOCTYPE html><html><head><title>Connexion réussie</title></head>
<body><script>
  localStorage.setItem('user', JSON.stringify({json.dumps(user_data)}));
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
            return redirect(f"{base_url}/index.html")

        if existing:
            role = existing.get("role", "user")
            user_data = {"id": existing["id"], "username": email.split("@")[0],
                         "firstname": existing["firstname"], "lastname": existing["lastname"],
                         "email": email, "role": role}
            dest = f"{base_url}/admin.html" if role == "admin" else f"{base_url}/dash.html"
            return _oauth_html(user_data, dest)

        user_id = run_update(
            "INSERT INTO users (firstname,lastname,email,password,login_type,role,created_at) VALUES (%s,%s,%s,'','google','user',NOW())",
            (firstname, lastname, email),
        )
        return _oauth_html(
            {"id": user_id, "username": email.split("@")[0],
             "firstname": firstname, "lastname": lastname, "email": email, "role": "user"},
            f"{base_url}/dash.html",
        )
    except Exception as e:
        base_url = request.url_root.rstrip("/")
        return f"""<!DOCTYPE html><html><body><p style="color:red">Erreur : {e}</p>
<script>setTimeout(()=>window.location.href='{base_url}/index.html',3000)</script></body></html>"""


# ═══════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════
PASSWORD_RESET_TTL_MINUTES = 30


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _smtp_config() -> dict:
    tls_raw = (os.getenv("SMTP_TLS", "1") or "1").strip().lower()
    return {
        "host": (os.getenv("SMTP_HOST") or "").strip(),
        "port": int((os.getenv("SMTP_PORT") or "587").strip()),
        "user": (os.getenv("SMTP_USER") or "").strip(),
        "password": (os.getenv("SMTP_PASSWORD") or "").strip(),
        "from_addr": (os.getenv("MAIL_FROM") or os.getenv("SMTP_USER") or "").strip(),
        "use_tls": tls_raw not in ("0", "false", "no", "off"),
    }


def _send_password_reset_email(to_email: str, firstname: str, reset_url: str) -> tuple[bool, str | None]:
    cfg = _smtp_config()
    if not cfg["host"] or not cfg["from_addr"]:
        return False, "SMTP non configuré"

    safe_name = (firstname or "Utilisateur").strip()
    msg = EmailMessage()
    msg["Subject"] = "Réinitialisation de mot de passe - BusinessApp"
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_email
    msg.set_content(
        f"Bonjour {safe_name},\n\n"
        "Vous avez demandé une réinitialisation de mot de passe.\n"
        f"Utilisez ce lien (valide {PASSWORD_RESET_TTL_MINUTES} minutes) :\n{reset_url}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email."
    )

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            if cfg["use_tls"]:
                server.starttls()
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)


def _send_contact_email(payload: dict) -> tuple[bool, str | None]:
    cfg = _smtp_config()
    to_addr = (os.getenv("CONTACT_TO") or cfg["from_addr"]).strip()
    if not cfg["host"] or not cfg["from_addr"] or not to_addr:
        return False, "smtp_not_configured"

    msg = EmailMessage()
    msg["Subject"] = f"[BusinessApp Contact] {(payload.get('subject') or 'Sans sujet').strip()}"
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_addr
    if payload.get("email"):
        msg["Reply-To"] = payload["email"]

    safe_name = (payload.get("name") or "Utilisateur").strip()
    safe_email = (payload.get("email") or "").strip()
    safe_phone = (payload.get("phone") or "").strip() or "Non renseigne"
    safe_subject = (payload.get("subject") or "").strip()
    safe_newsletter = (payload.get("newsletter") or "Non").strip()
    safe_message = (payload.get("message") or "").strip()

    msg.set_content(
        "Nouveau message depuis le formulaire contact BusinessApp.\n\n"
        f"Nom: {safe_name}\n"
        f"Email: {safe_email}\n"
        f"Telephone: {safe_phone}\n"
        f"Sujet: {safe_subject}\n"
        f"Newsletter: {safe_newsletter}\n\n"
        "Message:\n"
        f"{safe_message}\n"
    )

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            if cfg["use_tls"]:
                server.starttls()
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)


@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    d = request.get_json() or {}
    email = (d.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email requis"}), 400

    generic_msg = "Si un compte existe pour cet email, un lien de réinitialisation a été envoyé."
    try:
        user = run_query(
            "SELECT id,firstname,email,COALESCE(login_type,'email') AS login_type FROM users WHERE LOWER(TRIM(email))=%s",
            (email,), True,
        )

        if not user or (user.get("login_type") or "email") != "email":
            return jsonify({"message": generic_msg})

        token = secrets.token_urlsafe(32)
        token_hash = _hash_reset_token(token)
        expires_at = datetime.now() + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)

        run_update("DELETE FROM password_reset_tokens WHERE user_id=%s OR expires_at < NOW()", (user["id"],))
        run_update(
            "INSERT INTO password_reset_tokens (user_id,token_hash,expires_at,created_at) VALUES (%s,%s,%s,NOW())",
            (user["id"], token_hash, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )

        reset_url = f"{request.url_root.rstrip('/')}/reset-password.html?token={token}"
        sent, mail_error = _send_password_reset_email(user["email"], user.get("firstname") or "", reset_url)

        if sent:
            return jsonify({"message": generic_msg, "email_sent": True})

        # Fallback dev: pas de SMTP => on renvoie le lien pour permettre le test local.
        print(f"[FORGOT_PASSWORD] SMTP indisponible ({mail_error}). Lien de reset: {reset_url}")
        return jsonify({
            "message": generic_msg,
            "email_sent": False,
            "dev_reset_url": reset_url,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password_from_token():
    d = request.get_json() or {}
    token = (d.get("token") or "").strip()
    new_password = d.get("new_password") or ""

    if not token or not new_password:
        return jsonify({"error": "Token et nouveau mot de passe requis"}), 400
    if len(new_password) < 8:
        return jsonify({"error": "Le nouveau mot de passe doit contenir au moins 8 caractères"}), 400

    try:
        row = run_query(
            """SELECT pr.id AS token_id, pr.user_id, pr.expires_at, pr.used_at,
                      COALESCE(u.login_type,'email') AS login_type
               FROM password_reset_tokens pr
               JOIN users u ON u.id = pr.user_id
               WHERE pr.token_hash=%s
               ORDER BY pr.id DESC
               LIMIT 1""",
            (_hash_reset_token(token),), True,
        )
        if not row:
            return jsonify({"error": "Token invalide"}), 400
        if row.get("used_at"):
            return jsonify({"error": "Ce lien a déjà été utilisé"}), 400
        if row.get("expires_at") and row["expires_at"] < datetime.now():
            return jsonify({"error": "Ce lien a expiré"}), 400
        if (row.get("login_type") or "email") != "email":
            return jsonify({"error": "Réinitialisation indisponible pour ce compte"}), 400

        run_update(
            "UPDATE users SET password=%s WHERE id=%s",
            (generate_password_hash(new_password), row["user_id"]),
        )
        run_update("UPDATE password_reset_tokens SET used_at=NOW() WHERE id=%s", (row["token_id"],))
        run_update("DELETE FROM password_reset_tokens WHERE user_id=%s AND id<>%s", (row["user_id"], row["token_id"]))
        return jsonify({"message": "Mot de passe réinitialisé avec succès"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": "Email et mot de passe requis"}), 400

    user = run_query(
        "SELECT id,firstname,lastname,email,password,COALESCE(role,'user') AS role FROM users WHERE email=%s",
        (email,), True,
    )
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    return jsonify({"message": "Connexion réussie", "user": {
        "id": user["id"], "username": email.split("@")[0],
        "firstname": user["firstname"], "lastname": user["lastname"],
        "email": user["email"], "role": user.get("role", "user"),
    }})


# ═══════════════════════════════════════════════════════════════
# PROFIL
# ═══════════════════════════════════════════════════════════════
@app.route("/api/profile", methods=["GET"])
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
def update_profile(user_id):
    d         = request.get_json() or {}
    firstname = (d.get("firstname") or "").strip()
    lastname  = (d.get("lastname")  or "").strip()
    email     = (d.get("email")     or "").strip().lower()

    if not all([firstname, lastname, email]):
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if not run_query("SELECT id FROM users WHERE id=%s", (user_id,), True):
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    owner = run_query("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True)
    if owner and int(owner["id"]) != user_id:
        return jsonify({"error": "Cet email est déjà utilisé"}), 409

    try:
        run_update("UPDATE users SET firstname=%s,lastname=%s,email=%s WHERE id=%s",
                   (firstname, lastname, email, user_id))
        updated = run_query(
            "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users WHERE id=%s",
            (user_id,), True,
        )
        if updated and updated.get("created_at"):
            updated["created_at"] = updated["created_at"].strftime("%Y-%m-%d %H:%M")
        return jsonify({"message": "Profil mis à jour", "user": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/<int:user_id>/password", methods=["PUT"])
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
def cancel_pending_action():
    token = (request.get_json() or {}).get("token")
    if not token:
        return jsonify({"error": "Token manquant"}), 400
    return jsonify({"message": "Demande annulée"})


@app.route("/api/profile/<int:user_id>", methods=["DELETE"])
def delete_account(user_id):
    try:
        run_update("DELETE FROM users WHERE id=%s", (user_id,))
        return jsonify({"message": "Compte supprimé"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# USERS / ADMIN
# ═══════════════════════════════════════════════════════════════
@app.route("/api/users", methods=["GET"])
def users_list():
    users = run_query(
        "SELECT id,firstname,lastname,email,created_at,COALESCE(login_type,'email') AS login_type,COALESCE(role,'user') AS role FROM users ORDER BY id DESC"
    )
    for u in users:
        if u.get("created_at"):
            u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify(users)


@app.route("/api/stats", methods=["GET"])
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


@app.route("/api/admin/users/<int:target_id>/role", methods=["PUT"])
def update_user_role(target_id):
    new_role = (request.get_json() or {}).get("role", "").strip()
    if new_role not in ("user", "admin"):
        return jsonify({"error": "Rôle invalide"}), 400
    target = run_query("SELECT id,firstname,lastname FROM users WHERE id=%s", (target_id,), True)
    if not target:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    try:
        run_update("UPDATE users SET role=%s WHERE id=%s", (new_role, target_id))
        return jsonify({"message": f"Rôle → {new_role}", "user_id": target_id, "role": new_role})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/users", methods=["POST"])
def create_user_admin():
    d         = request.get_json() or {}
    firstname = (d.get("firstname") or "").strip()
    lastname  = (d.get("lastname")  or "").strip()
    email     = (d.get("email")     or "").strip().lower()
    password  = d.get("password")   or ""
    role      = (d.get("role")      or "user").strip().lower()

    if not all([firstname, lastname, email, password]):
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400
    if role not in ("user", "admin"):
        return jsonify({"error": "Rôle invalide"}), 400
    if run_query("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True):
        return jsonify({"error": "Email déjà utilisé"}), 409

    user_id = run_update(
        "INSERT INTO users (firstname,lastname,email,password,role,login_type,created_at) VALUES (%s,%s,%s,%s,%s,'email',NOW())",
        (firstname, lastname, email, generate_password_hash(password), role),
    )
    return jsonify({"message": "Utilisateur créé", "user": {
        "id": user_id, "firstname": firstname, "lastname": lastname,
        "email": email, "role": role, "login_type": "email",
    }}), 201


@app.route("/api/users/export", methods=["GET"])
def export_users():
    users = run_query(
        "SELECT id,firstname,lastname,email,created_at,COALESCE(role,'user') AS role,COALESCE(login_type,'email') AS login_type FROM users ORDER BY id"
    )
    lines = ["id,firstname,lastname,email,role,login_type,created_at"]
    for u in users:
        created = u["created_at"]
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M")
        lines.append(f"{u['id']},{u['firstname']},{u['lastname']},{u['email']},{u.get('role','user')},{u.get('login_type','email')},{created}")
    return Response("\n".join(lines), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=users.csv"})


# ═══════════════════════════════════════════════════════════════
# HISTORIQUE IMPORTS
# ═══════════════════════════════════════════════════════════════
@app.route("/api/etl/history", methods=["GET"])
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

        data_raw = row.get("data")
        data_value = []
        if data_raw:
            try:
                d = json.loads(data_raw)
                if isinstance(d, dict) and d.get("compressed"):
                    data_value = {"total_rows": d.get("total_rows", 0),
                                  "rows": decompress_payload(d.get("content")) or []}
                else:
                    data_value = d
            except Exception:
                pass

        grouped[uname].append({
            "id": row["id"], "user_id": row["user_id"],
            "nom_fichier": row["nom_fichier"],
            "date_import": row["date_import"].isoformat() if row.get("date_import") else None,
            "date_import_label": row["date_import"].strftime("%Y-%m-%d %H:%M") if row.get("date_import") else "",
            "nb_lignes": row["nb_lignes"], "nb_erreurs": row["nb_erreurs"],
            "statut": row["statut"], "departement": row["departement"],
            "importe_par": row["importe_par"],
            "details": _decode("details"),
            "data": data_value,
        })

    return jsonify({"success": True, "groups": [{"user_name": k, "items": v} for k, v in grouped.items()]})


@app.route("/api/etl/history/<int:import_id>", methods=["DELETE"])
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
def etl_upload():
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    file  = request.files["file"]
    fname = file.filename or ""
    if not any(fname.lower().endswith(e) for e in (".csv", ".xlsx", ".xls")):
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
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/etl/process", methods=["POST"])
def etl_process():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Authentification requise"}), 401

    d        = request.get_json() or {}
    filename = d.get("filename")
    if not filename:
        return jsonify({"error": "Nom de fichier manquant"}), 400

    file_path = UPLOAD_FOLDER / filename
    if not file_path.exists():
        return jsonify({"error": "Fichier non trouvé"}), 404

    try:
        create_tables()
        result = run_generic_etl(str(file_path), replace_existing=True)
        safe   = lambda x: make_json_safe(x)

        if not result.get("success"):
            elog = safe(result.get("log", []))
            try:
                save_import_history(user_id, filename, {"lignes": 0, "nb_erreurs": 1}, elog, [], False)
            except Exception:
                pass
            return jsonify({"success": False, "error": result.get("error", "Erreur ETL"),
                            "detail": result.get("detail"), "log": elog}), 500

        stats       = safe(result.get("stats", {}))
        log         = safe(result.get("log", []))
        after_rows  = safe(result.get("after_rows", []))

        try:
            save_import_history(user_id, filename, stats, log, after_rows, True)
        except Exception as he:
            print(f"[ETL] History insert failed: {he}")

        rows_inserted = int((result.get("db_result") or {}).get("rows_inserted") or 0)
        # Force KPI refresh on next dashboard load after each ETL run,
        # and avoid showing stale persisted KPI values.
        _clear_kpis_by_source("etl_auto")
        status_message = (
            "ETL termine avec succes."
            if rows_inserted > 0
            else "ETL termine, mais aucune transaction n'a ete inseree. Verifiez le fichier importe."
        )

        return jsonify({
            "success": True, "log": log, "stats": stats,
            "before_rows": safe(result.get("before_rows", [])),
            "after_rows":  after_rows,
            "changed_rows": safe(result.get("changed_rows", 0)),
            "db_result":   safe(result.get("db_result", {})),
            "message": status_message,
        })
    except Exception as e:
        print("[ETL] Fatal:", traceback.format_exc())
        try:
            save_import_history(user_id, filename, {"lignes": 0, "nb_erreurs": 1},
                                [str(e), traceback.format_exc()], [], False)
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


@app.route("/api/etl/download", methods=["GET"])
def etl_download():
    cleaned = UPLOAD_FOLDER / "donnees_nettoyees.csv"
    if not cleaned.exists():
        return jsonify({"error": "Aucun fichier nettoyé disponible"}), 404
    return send_from_directory(str(UPLOAD_FOLDER), "donnees_nettoyees.csv",
                               as_attachment=True, mimetype="text/csv")


@app.route("/api/etl/table-data", methods=["GET"])
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
def save_kpis():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Authentification requise"}), 401

    d = request.get_json(force=True) or {}
    kpis = d.get("kpis", [])
    source = str(d.get("source", "etl") or "etl").strip() or "etl"
    do_replace = d.get("replace", False)
    if not kpis:
        return jsonify({"success": False, "error": "Aucun KPI fourni"}), 400

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        inserted = updated = replaced = 0

        for kpi in kpis:
            kpi_payload = dict(kpi or {})
            kpi_payload["source"] = str(kpi_payload.get("source", source) or source).strip() or source

            nom, per, dept, src, stype = _kpi_identity_from_payload(kpi_payload, source)
            if not nom:
                continue

            if do_replace:
                cur.execute(
                    """DELETE FROM valeur_kpi
                       WHERE kpiNom=%s AND periode=%s
                         AND departementId <=> %s
                         AND source=%s
                         AND stat_type=%s""",
                    (nom, per, dept, src, stype),
                )
                replaced += int(cur.rowcount or 0)

            action = _upsert_kpi_row(cur, kpi_payload, default_source=source)
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1

        conn.commit()
        cur.close()
        return jsonify({
            "success": True,
            "inserted": inserted,
            "updated": updated,
            "replaced": replaced,
            "message": f"{inserted} KPI(s) inseres, {updated} mis a jour"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/kpi", methods=["GET"])
def get_kpis():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"success": False, "error": "Authentification requise"}), 401

    kpi_nom = request.args.get("kpiNom")
    periode = request.args.get("periode")
    source = str(request.args.get("source", "etl_auto") or "etl_auto").strip()
    try:
        limit = int(request.args.get("limit", 100))
    except Exception:
        limit = 100
    limit = max(1, min(limit, 5000))

    readiness = _get_analytics_readiness()
    if not readiness.get("ready"):
        return jsonify({
            "success": True,
            "analytics_ready": False,
            "message": readiness.get("message"),
            "reason": readiness.get("reason"),
            "missing_tables": readiness.get("missing_tables", []),
            "kpis": [],
            "total": 0,
        })

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)

        q, p = "SELECT * FROM valeur_kpi WHERE 1=1", []
        if source and source.lower() != "all":
            q += " AND source=%s"
            p.append(source)
        if kpi_nom:
            q += " AND kpiNom=%s"
            p.append(kpi_nom)
        if periode:
            q += " AND periode=%s"
            p.append(periode)

        q += " ORDER BY created_at DESC LIMIT %s"
        p.append(limit)

        cur.execute(q, p)
        kpis = cur.fetchall()
        cur.close()

        for k in kpis:
            for f in ("created_at", "updated_at"):
                if k.get(f):
                    k[f] = k[f].isoformat()

        return jsonify({
            "success": True,
            "analytics_ready": True,
            "kpis": kpis,
            "total": len(kpis)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════
# PRÉVISIONS
# ═══════════════════════════════════════════════════════════════
@app.route("/api/previsions", methods=["POST"])
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


# ═══════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════
ANALYTICS_REQUIRED_TABLES = (
    "transactions", "date", "departement", "typetransaction",
    "typedepense", "responsable", "clientfournisseur", "projet",
)


def _get_analytics_readiness():
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)

        placeholders = ",".join(["%s"] * len(ANALYTICS_REQUIRED_TABLES))
        cur.execute(
            f"""SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ({placeholders})""",
            ANALYTICS_REQUIRED_TABLES,
        )
        existing = {row["TABLE_NAME"] for row in cur.fetchall()}
        missing = [t for t in ANALYTICS_REQUIRED_TABLES if t not in existing]
        if missing:
            cur.close()
            return {
                "ready": False,
                "reason": "missing_tables",
                "missing_tables": missing,
                "message": "Les tables analytiques ne sont pas encore pretes. Lancez le traitement ETL depuis Data Import."
            }

        cur.execute("SELECT COUNT(*) AS c FROM transactions")
        tx_count = int((cur.fetchone() or {}).get("c", 0) or 0)
        cur.close()
        if tx_count <= 0:
            return {
                "ready": False,
                "reason": "no_transactions",
                "missing_tables": [],
                "message": "Aucune transaction traitee n'est disponible. Importez un fichier puis lancez le nettoyage ETL."
            }

        return {
            "ready": True,
            "reason": "ok",
            "missing_tables": [],
            "tx_count": tx_count,
            "message": "Analytics pret"
        }
    finally:
        if conn:
            conn.close()


def _safe_to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float, np.integer, np.floating)):
        try:
            number = float(value)
            if math.isnan(number) or math.isinf(number):
                return 0.0
            return number
        except Exception:
            return 0.0

    text = str(value).strip()
    if not text:
        return 0.0

    text = text.replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"[^0-9,.\-]", "", text)
    if text.count(",") and text.count("."):
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") and not text.count("."):
        text = text.replace(",", ".")

    try:
        number = float(text)
        if math.isnan(number) or math.isinf(number):
            return 0.0
        return number
    except Exception:
        return 0.0


def _safe_to_int(value):
    try:
        if value in ("", None):
            return None
        return int(float(value))
    except Exception:
        return None


def _kpi_identity_from_payload(kpi: dict, default_source: str):
    nom = str(kpi.get("kpiNom", "")).strip()
    per = str(kpi.get("periode", "global")).strip() or "global"
    dept = _safe_to_int(kpi.get("departementId"))
    source = str(kpi.get("source", default_source) or default_source).strip() or default_source
    stat_type = str(kpi.get("stat_type", "sum") or "sum").strip() or "sum"
    return nom, per, dept, source, stat_type


def _upsert_kpi_row(cur, kpi: dict, default_source: str = "etl_auto"):
    nom, per, dept, source, stat_type = _kpi_identity_from_payload(kpi, default_source)
    if not nom:
        return None

    valeur = round(_safe_to_float(kpi.get("valeur", 0)), 2)
    evolution = round(_safe_to_float(kpi.get("evolution", 0)), 2)

    cur.execute(
        """SELECT 1
           FROM valeur_kpi
           WHERE kpiNom=%s AND periode=%s
             AND departementId <=> %s
             AND source=%s
             AND stat_type=%s
           LIMIT 1""",
        (nom, per, dept, source, stat_type),
    )
    exists = cur.fetchone() is not None

    if exists:
        cur.execute(
            """UPDATE valeur_kpi
               SET valeur=%s, evolution=%s, updated_at=NOW()
               WHERE kpiNom=%s AND periode=%s
                 AND departementId <=> %s
                 AND source=%s
                 AND stat_type=%s""",
            (valeur, evolution, nom, per, dept, source, stat_type),
        )
        return "updated"

    cur.execute(
        """INSERT INTO valeur_kpi (kpiNom,periode,valeur,evolution,departementId,source,stat_type)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (nom, per, valeur, evolution, dept, source, stat_type),
    )
    return "inserted"


def _clear_kpis_by_source(source: str) -> int:
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM valeur_kpi WHERE source=%s", (source,))
        deleted = int(cur.rowcount or 0)
        conn.commit()
        cur.close()
        return deleted
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return 0
    finally:
        if conn:
            conn.close()


@app.route("/api/analytics/data", methods=["GET"])
def analytics_data():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401
    try:
        readiness = _get_analytics_readiness()
        if not readiness.get("ready"):
            return jsonify({
                "success": True,
                "analytics_ready": False,
                "message": readiness.get("message"),
                "reason": readiness.get("reason"),
                "missing_tables": readiness.get("missing_tables", []),
                "total": 0,
                "kpis": [],
                "data": [],
            })

        rows = run_query("""
            SELECT t.Transaction_ID, t.Montant, t.Montant_Signe,
                   d.Date AS date_val,
                   YEAR(d.Date) AS annee,
                   MONTH(d.Date) AS mois,
                   QUARTER(d.Date) AS trimestre,
                   CONCAT(YEAR(d.Date), '-', LPAD(MONTH(d.Date), 2, '0')) AS `year_month`,
                   dep.NomDepartement AS departement,
                   tt.TypeTransaction AS type_transaction,
                   td.TypeDepense AS type_depense,
                   r.NomResponsable AS responsable,
                   cf.NomClientFournisseur AS client_fournisseur, cf.Type AS cf_type,
                   p.NomProjet AS projet
            FROM transactions t
            LEFT JOIN `date`            d   ON d.Date_ID              = t.Date_ID
            LEFT JOIN departement       dep ON dep.Departement_ID      = t.Departement_ID
            LEFT JOIN typetransaction   tt  ON tt.TypeTransaction_ID   = t.TypeTransaction_ID
            LEFT JOIN typedepense       td  ON td.TypeDepense_ID       = t.TypeDepense_ID
            LEFT JOIN responsable       r   ON r.Responsable_ID        = t.Responsable_ID
            LEFT JOIN clientfournisseur cf  ON cf.ClientFournisseur_ID = t.ClientFournisseur_ID
            LEFT JOIN projet            p   ON p.Projet_ID             = t.Projet_ID
            ORDER BY d.Date DESC LIMIT 50000""")
        if not rows:
            return jsonify({
                "success": True,
                "analytics_ready": False,
                "message": "Aucune donnee analytics disponible apres traitement ETL.",
                "reason": "empty_result",
                "total": 0,
                "kpis": [],
                "data": [],
            })
        return jsonify({
            "success": True,
            "analytics_ready": True,
            "message": "Donnees analytics chargees",
            "total": len(rows),
            "data": make_json_safe(rows),
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "analytics_ready": False,
            "error": str(e),
            "message": "Erreur lors de la recuperation des donnees analytics.",
            "detail": traceback.format_exc(),
        }), 500


@app.route("/api/analytics/kpi-refresh", methods=["POST"])
def kpi_refresh():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401

    payload = request.get_json(silent=True) or {}
    kpi_source = str(payload.get("source", "etl_auto") or "etl_auto").strip() or "etl_auto"

    try:
        readiness = _get_analytics_readiness()
        if not readiness.get("ready"):
            cleared = _clear_kpis_by_source(kpi_source)
            return jsonify({
                "success": True,
                "analytics_ready": False,
                "inserted": 0,
                "updated": 0,
                "cleared": cleared,
                "kpis": [],
                "message": readiness.get("message"),
                "reason": readiness.get("reason"),
                "missing_tables": readiness.get("missing_tables", []),
            })

        rows = run_query("""
            SELECT t.Montant, t.Montant_Signe,
                   YEAR(d.Date) AS annee,
                   QUARTER(d.Date) AS trimestre,
                   CONCAT(YEAR(d.Date), '-', LPAD(MONTH(d.Date), 2, '0')) AS `year_month`,
                   dep.NomDepartement AS departement, dep.Departement_ID,
                   tt.TypeTransaction AS type_transaction, td.TypeDepense AS type_depense
            FROM transactions t
            LEFT JOIN `date`          d   ON d.Date_ID             = t.Date_ID
            LEFT JOIN departement     dep ON dep.Departement_ID    = t.Departement_ID
            LEFT JOIN typetransaction tt  ON tt.TypeTransaction_ID = t.TypeTransaction_ID
            LEFT JOIN typedepense     td  ON td.TypeDepense_ID     = t.TypeDepense_ID""")

        if not rows:
            cleared = _clear_kpis_by_source(kpi_source)
            return jsonify({
                "success": True,
                "analytics_ready": False,
                "inserted": 0,
                "updated": 0,
                "cleared": cleared,
                "kpis": [],
                "message": "Aucune transaction traitee disponible pour recalculer les KPI.",
                "reason": "empty_result",
            })

        normalized_rows = []
        for row in rows:
            normalized_rows.append({
                **row,
                "Montant": _safe_to_float(row.get("Montant")),
                "Montant_Signe": _safe_to_float(row.get("Montant_Signe")),
                "Departement_ID": _safe_to_int(row.get("Departement_ID")),
            })

        s_total = sum(r.get("Montant", 0.0) for r in normalized_rows)
        s_signe = sum(r.get("Montant_Signe", 0.0) for r in normalized_rows)
        revenus = sum(r.get("Montant_Signe", 0.0) for r in normalized_rows if r.get("Montant_Signe", 0.0) > 0)
        depenses = abs(sum(r.get("Montant_Signe", 0.0) for r in normalized_rows if r.get("Montant_Signe", 0.0) < 0))
        tx_count = len(normalized_rows)

        kpis = [
            {"kpiNom": "Revenus Totaux", "periode": "global", "valeur": round(revenus, 2), "stat_type": "sum", "source": kpi_source},
            {"kpiNom": "Depenses Totales", "periode": "global", "valeur": round(depenses, 2), "stat_type": "sum", "source": kpi_source},
            {"kpiNom": "Solde Net", "periode": "global", "valeur": round(s_signe, 2), "stat_type": "sum", "source": kpi_source},
            {"kpiNom": "Nombre de transactions", "periode": "global", "valeur": tx_count, "stat_type": "count", "source": kpi_source},
            {"kpiNom": "Valeur moyenne", "periode": "global", "valeur": round(s_total / tx_count, 2) if tx_count else 0, "stat_type": "avg", "source": kpi_source},
            {"kpiNom": "CA_Total", "periode": "global", "valeur": round(s_total, 2), "stat_type": "sum", "source": kpi_source},
            {"kpiNom": "Nb_Transactions", "periode": "global", "valeur": tx_count, "stat_type": "count", "source": kpi_source},
            {"kpiNom": "Valeur_Moyenne", "periode": "global", "valeur": round(s_total / tx_count, 2) if tx_count else 0, "stat_type": "avg", "source": kpi_source},
            {"kpiNom": "Ratio_Dep_Rev", "periode": "global", "valeur": round(depenses / revenus * 100, 2) if revenus else 0, "stat_type": "ratio", "source": kpi_source},
        ]

        q_data = defaultdict(list)
        dep_data = defaultdict(lambda: {"montant": [], "id": None})
        tt_data = defaultdict(list)
        td_data = defaultdict(list)
        ym_data = defaultdict(list)

        for row in normalized_rows:
            amount = _safe_to_float(row.get("Montant"))
            quarter = f"Q{row.get('trimestre') or 0}_{row.get('annee') or 0}"
            q_data[quarter].append(amount)

            dep_name = row.get("departement") or "Inconnu"
            dep_data[dep_name]["montant"].append(amount)
            dep_data[dep_name]["id"] = _safe_to_int(row.get("Departement_ID"))

            tt_data[row.get("type_transaction") or "Autre"].append(amount)
            td_data[row.get("type_depense") or "Autre"].append(amount)
            ym_data[row.get("year_month") or "0000-00"].append(amount)

        for key, vals in q_data.items():
            kpis.append({
                "kpiNom": f"CA_{key}",
                "periode": key,
                "valeur": round(sum(vals), 2),
                "stat_type": "sum",
                "source": kpi_source,
            })
        for dep_name, dep_meta in dep_data.items():
            kpis.append({
                "kpiNom": f"CA_{dep_name}",
                "periode": "global",
                "valeur": round(sum(dep_meta["montant"]), 2),
                "departementId": dep_meta["id"],
                "stat_type": "sum",
                "source": kpi_source,
            })
        for tt_name, vals in tt_data.items():
            kpis.append({
                "kpiNom": f"Vol_{tt_name}",
                "periode": "global",
                "valeur": round(sum(vals), 2),
                "stat_type": "sum",
                "source": kpi_source,
            })
        for td_name, vals in td_data.items():
            kpis.append({
                "kpiNom": f"Dep_{td_name}",
                "periode": "global",
                "valeur": round(sum(vals), 2),
                "stat_type": "sum",
                "source": kpi_source,
            })

        sorted_ym = sorted(ym_data)
        for idx, year_month in enumerate(sorted_ym):
            val = round(sum(ym_data[year_month]), 2)
            prev = round(sum(ym_data[sorted_ym[idx - 1]]), 2) if idx > 0 else val
            evolution = round((val - prev) / prev * 100, 2) if prev else 0
            kpis.append({
                "kpiNom": "CA_Mensuel",
                "periode": year_month,
                "valeur": val,
                "evolution": evolution,
                "stat_type": "sum",
                "source": kpi_source,
            })

        conn = None
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM valeur_kpi WHERE source=%s", (kpi_source,))
            cleared = int(cur.rowcount or 0)

            inserted = 0
            updated = 0
            for kpi in kpis:
                action = _upsert_kpi_row(cur, kpi, default_source=kpi_source)
                if action == "inserted":
                    inserted += 1
                elif action == "updated":
                    updated += 1

            conn.commit()
            cur.close()
        finally:
            if conn:
                conn.close()

        return jsonify({
            "success": True,
            "analytics_ready": True,
            "source": kpi_source,
            "inserted": inserted,
            "updated": updated,
            "cleared": cleared,
            "kpi_count": inserted + updated,
            "message": "KPIs recalcules et persistes dans valeur_kpi",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


# ═══════════════════════════════════════════════════════════════
# CHATBOT
# ═══════════════════════════════════════════════════════════════
@app.route("/api/chatbot/ask", methods=["POST"])
def chatbot_ask():
    try:
        msg = (request.get_json() or {}).get("message", "").strip().lower()
        if not msg:
            return jsonify({"answer": "Posez-moi une question sur vos données !"})
        if any(w in msg for w in ["revenu", "argent", "gagné", "ca"]):
            res = run_query("SELECT SUM(valeur) AS t FROM valeur_kpi WHERE kpiNom LIKE '%revenu%'", fetch_one=True)
            return jsonify({"answer": f"Le revenu total détecté est de {res['t'] or 0:,.2f} €."})
        if any(w in msg for w in ["dépense", "depense", "coût", "perdu"]):
            res = run_query("SELECT SUM(valeur) AS t FROM valeur_kpi WHERE kpiNom LIKE '%depense%'", fetch_one=True)
            return jsonify({"answer": f"Les dépenses s'élèvent à {res['t'] or 0:,.2f} €."})
        if any(w in msg for w in ["utilisateur", "client", "membre"]):
            res = run_query("SELECT COUNT(*) AS n FROM users", fetch_one=True)
            return jsonify({"answer": f"Il y a actuellement {res['n']} utilisateurs enregistrés."})
        return jsonify({"answer": "Je peux vous aider sur les revenus, dépenses ou utilisateurs."})
    except Exception as e:
        return jsonify({"answer": f"Erreur : {e}"}), 500

# ═══════════════════════════════════════════════════════════════
# simulation what-if
# ═══════════════════════════════════════════════════════════════
@app.route("/api/assistance/simulate", methods=["POST"])
def simulate_what_if():
    try:
        data = request.get_json()
        amount = float(data.get("amount", 0))
        
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        
        # On récupère le montant total des revenus pour comparer
        cursor.execute("SELECT SUM(Montant_Signe) as solde FROM transactions")
        row = cursor.fetchone()
        solde_actuel = float(row['solde'] or 0)
        
        # Calcul de l'impact (Exemple: quel % du solde cette transaction représente)
        if solde_actuel != 0:
            impact_percent = (amount / abs(solde_actuel)) * 100
        else:
            impact_percent = 0

        # Génération d'une recommandation dynamique
        if impact_percent > 20:
            rec = "⚠️ Risque élevé : Cette transaction pèse lourdement sur vos réserves actuelles."
        elif impact_percent > 5:
            rec = "🧐 Vigilance : Impact modéré sur la trésorerie. Vérifiez vos priorités."
        else:
            rec = "✅ Risque faible : Votre structure financière peut absorber cette transaction sans difficulté."

        return jsonify({
            "impact": round(impact_percent, 2),
            "recommendation": rec
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals(): conn.close()

# ═══════════════════════════════════════════════════════════════
# CONTACT
# ═══════════════════════════════════════════════════════════════
@app.route("/api/contact", methods=["POST"])
def contact():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name") or "").strip()
    email = str(d.get("email") or "").strip().lower()
    phone = str(d.get("phone") or "").strip()
    subject = str(d.get("subject") or "").strip()
    message = str(d.get("message") or "").strip()
    newsletter = str(d.get("newsletter") or "Non").strip()

    if not all((name, email, subject, message)):
        return jsonify({"success": False, "error": "Tous les champs sont requis"}), 400
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"success": False, "error": "Email invalide"}), 400

    sent, error = _send_contact_email({
        "name": name,
        "email": email,
        "phone": phone,
        "subject": subject,
        "message": message,
        "newsletter": newsletter,
    })

    if sent:
        return jsonify({
            "success": True,
            "channel": "smtp",
            "message": "Message envoye avec succes"
        })

    if error == "smtp_not_configured":
        return jsonify({
            "success": False,
            "error": "smtp_not_configured",
            "message": "Le service email serveur n'est pas configure."
        }), 503

    return jsonify({
        "success": False,
        "error": "smtp_send_failed",
        "message": "Echec de l'envoi email via le serveur.",
        "detail": error
    }), 502


# ═══════════════════════════════════════════════════════════════
# INIT TABLES (debug)
# ═══════════════════════════════════════════════════════════════
@app.route("/api/init-tables", methods=["POST"])
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
    app.run(host="0.0.0.0", port=5000, debug=True)
