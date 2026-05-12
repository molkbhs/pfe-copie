# -*- coding: utf-8 -*-
"""
ETL générique — charge les données dans pfe_bd (schéma en étoile)
Tables cibles : date, departement, responsable, typetransaction,
                typedepense, clientfournisseur, projet, transactions
"""

import gc
import math
import re
import traceback
import unicodedata
import warnings
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

from config import DB_CONFIG

warnings.filterwarnings("ignore")

CRITICAL_CANONICAL_COLUMNS = ("Date", "Montant")

DATE_FALLBACK_COLUMNS = ("YearMonth", "Année", "Mois")

REJECTED_ROWS_FILENAME = "rejected_rows.csv"

OPTIONAL_CANONICAL_DEFAULTS = {
    "DepartementID": None,
    "Département": "Non renseigné",
    "TypeTransaction": "",
    "TypeDépense": "N/A",
    "Responsable": "Non renseigné",
    "Client_Fournisseur": "Non renseigné",
    "Projet": "Sans projet",
}

HEADER_ALIASES = {
    "Date": [
        "date",
        "date_transaction",
        "transaction_date",
        "dateoperation",
        "date_op",
        "date_operation",
        "datecomptable",
        "booking_date",
        "posting_date",
    ],
    "DepartementID": [
        "departementid",
        "departmentid",
        "deptid",
        "iddepartement",
    ],
    "Département": [
        "departement",
        "department",
        "dept",
        "service",
        "business_unit",
        "businessunit",
        "direction",
    ],
    "TypeTransaction": [
        "typetransaction",
        "type_transaction",
        "transaction_type",
        "typeoperation",
        "operationtype",
        "naturetransaction",
        "sens",
        "flux",
    ],
    "TypeDépense": [
        "typedepense",
        "type_depense",
        "expense_type",
        "depense_type",
        "categoriedepense",
        "categorie_depense",
        "expense_category",
        "categorie",
        "poste",
    ],
    "Montant": [
        "montant",
        "amount",
        "valeur",
        "montantdt",
        "montant_tnd",
        "total",
        "montant_ht",
        "montant_ttc",
    ],
    "Montant_Signe": [
        "montant_signe",
        "montant_signé",
        "signed_amount",
        "amount_signed",
        "net_amount",
        "debit_credit_amount",
    ],
    "Responsable": [
        "responsable",
        "manager",
        "owner",
        "chef_projet",
        "gestionnaire",
    ],
    "Client_Fournisseur": [
        "client_fournisseur",
        "clientfournisseur",
        "tiers",
        "counterparty",
        "partenaire",
        "client",
        "fournisseur",
        "vendor",
        "supplier",
        "customer",
    ],
    "Projet": [
        "projet",
        "project",
        "nomprojet",
        "project_name",
        "code_projet",
    ],
}


def _connect():
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=int(DB_CONFIG.get("port", 3306)),
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _normalize_header(value) -> str:
    txt = unicodedata.normalize("NFKD", str(value or ""))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = txt.lower().strip()
    txt = re.sub(r"[^a-z0-9]+", "_", txt).strip("_")
    return txt


def _normalize_text(value):
    if pd.isna(value):
        return None
    text = " ".join(str(value).strip().split())
    return text or None


def _parse_amount(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        try:
            f = float(value)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"[^0-9,\.\-]", "", text)

    if text.count(",") and text.count("."):
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") and not text.count("."):
        text = text.replace(",", ".")

    try:
        f = float(text)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _parse_dates_series(series: pd.Series) -> pd.Series:
    """
    Parse dates with multiple strategies to support heterogeneous files.
    This avoids losing ISO dates when `dayfirst=True` is applied globally.
    """
    s = series.copy()
    parsed = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    if s.empty:
        return parsed

    text = s.astype(str).str.strip()
    ymd_mask = text.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", na=False)
    dmy_mask = text.str.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$", na=False)

    if ymd_mask.any():
        parsed.loc[ymd_mask] = pd.to_datetime(text.loc[ymd_mask], errors="coerce", yearfirst=True, dayfirst=False)
    if dmy_mask.any():
        parsed.loc[dmy_mask] = pd.to_datetime(text.loc[dmy_mask], errors="coerce", dayfirst=True, yearfirst=False)

    remaining = parsed.isna()
    if remaining.any():
        parsed.loc[remaining] = pd.to_datetime(s.loc[remaining], errors="coerce", dayfirst=True, yearfirst=False)

    remaining = parsed.isna()
    if remaining.any():
        parsed.loc[remaining] = pd.to_datetime(s.loc[remaining], errors="coerce", dayfirst=False, yearfirst=False)

    remaining = parsed.isna()
    if remaining.any():
        numeric = pd.to_numeric(s.loc[remaining], errors="coerce")
        numeric_mask = numeric.notna()
        if numeric_mask.any():
            parsed.loc[numeric.index[numeric_mask]] = pd.to_datetime(
                numeric.loc[numeric_mask], unit="D", origin="1899-12-30", errors="coerce"
            )

    return parsed


def _infer_date_from_fallback_columns(df: pd.DataFrame, log: list) -> pd.Series | None:
    """
    Try to build Date from YearMonth or (Année, Mois) when Date is missing.
    """
    if "YearMonth" in df.columns:
        ym = df["YearMonth"].astype(str).str.strip()
        parsed = pd.to_datetime(ym + "-01", errors="coerce")
        if parsed.notna().any():
            log.append("⚠ Colonne 'Date' absente: reconstruite depuis 'YearMonth' (jour=01)")
            return parsed

    if "Année" in df.columns and "Mois" in df.columns:
        y = pd.to_numeric(df["Année"], errors="coerce")
        m = pd.to_numeric(df["Mois"], errors="coerce")
        parsed = pd.to_datetime(
            pd.DataFrame({"year": y, "month": m, "day": 1}),
            errors="coerce",
        )
        if parsed.notna().any():
            log.append("⚠ Colonne 'Date' absente: reconstruite depuis 'Année' + 'Mois' (jour=01)")
            return parsed

    return None


def _normalize_type_transaction(value, amount_abs: float | None):
    label = _normalize_text(value)
    normalized = _normalize_header(label)
    if normalized in {"revenu", "income", "vente", "encaissement", "credit", "in"}:
        return "Revenu"
    if normalized in {"depense", "expense", "charge", "achat", "debit", "out", "cout", "couts"}:
        return "Depense"

    if amount_abs is not None and amount_abs < 0:
        return "Depense"
    return "Revenu"


def _read_file(path: Path, log: list) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext == ".csv":
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                df = pd.read_csv(path, encoding=enc, low_memory=False)
                log.append(f"✓ Fichier lu (CSV, {enc}) : {len(df)} lignes")
                return df
            except UnicodeDecodeError:
                continue
        raise ValueError("Impossible de lire le CSV : encodage non reconnu")

    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
        log.append(f"✓ Fichier lu (Excel) : {len(df)} lignes")
        return df

    raise ValueError(f"Format non supporté : {ext}")


def _json_safe_records(df: pd.DataFrame) -> list:
    out = []
    for _, row in df.iterrows():
        record = {}
        for col, val in row.items():
            try:
                if pd.isna(val):
                    record[col] = None
                    continue
            except Exception:
                pass

            if isinstance(val, np.integer):
                record[col] = int(val)
            elif isinstance(val, (np.floating, float)):
                f = float(val)
                record[col] = None if (math.isnan(f) or math.isinf(f)) else f
            elif isinstance(val, np.bool_):
                record[col] = bool(val)
            elif hasattr(val, "isoformat") and not isinstance(val, str):
                record[col] = val.isoformat()
            elif hasattr(val, "item"):
                try:
                    item = val.item()
                    record[col] = None if (isinstance(item, float) and (math.isnan(item) or math.isinf(item))) else item
                except Exception:
                    record[col] = str(val)
            elif isinstance(val, (int, str, bool)) or val is None:
                record[col] = val
            else:
                record[col] = str(val)
        out.append(record)
    return out


def _resolve_column_mapping(df: pd.DataFrame, log: list) -> tuple[pd.DataFrame, dict]:
    mapped = {}
    used_src = set()

    normalized_to_original = {}
    for col in df.columns:
        ncol = _normalize_header(col)
        if ncol and ncol not in normalized_to_original:
            normalized_to_original[ncol] = col

    alias_norms = {
        canonical: [_normalize_header(canonical), *[_normalize_header(a) for a in aliases]]
        for canonical, aliases in HEADER_ALIASES.items()
    }

    # Pass 1: exact normalized alias match
    for canonical, candidates in alias_norms.items():
        for n_alias in candidates:
            src = normalized_to_original.get(n_alias)
            if not src or src in used_src:
                continue
            mapped[src] = canonical
            used_src.add(src)
            break

    # Pass 2: fuzzy fallback for remaining columns
    for src_col in df.columns:
        if src_col in used_src:
            continue
        n_src = _normalize_header(src_col)
        if not n_src:
            continue

        best = (None, 0.0)
        for canonical, candidates in alias_norms.items():
            if canonical in mapped.values():
                continue
            for cand in candidates:
                if not cand:
                    continue
                score = SequenceMatcher(None, n_src, cand).ratio()
                if cand in n_src or n_src in cand:
                    score = max(score, 0.86)
                if score > best[1]:
                    best = (canonical, score)

        target, score = best
        if target and score >= 0.88:
            mapped[src_col] = target
            used_src.add(src_col)

    if mapped:
        df = df.rename(columns=mapped)
        log.append(f"✓ Colonnes reconnues automatiquement : {len(mapped)}")

    mapped_summary = {src: dst for src, dst in mapped.items() if src != dst}
    if mapped_summary:
        sample = ", ".join(f"{k}->{v}" for k, v in list(mapped_summary.items())[:6])
        more = " ..." if len(mapped_summary) > 6 else ""
        log.append(f"ℹ Mapping colonnes: {sample}{more}")

    return df, mapped_summary


def _clean_dataframe(df: pd.DataFrame):
    log = []
    clean_report = {
        "input_rows": int(len(df)),
        "mapped_columns_count": 0,
        "mapped_columns": {},
        "defaulted_optional_columns": [],
        "duplicates_removed": 0,
        "invalid_rows_removed": 0,
        "invalid_by_reason": {
            "invalid_date_only": 0,
            "invalid_montant_only": 0,
            "invalid_date_and_montant": 0,
        },
        "rows_after_cleaning": 0,
    }
    rejected_parts = []

    df = df.copy()
    df["_source_row"] = np.arange(2, len(df) + 2)
    df.columns = [str(c).strip() for c in df.columns]
    df, mapped_columns = _resolve_column_mapping(df, log)
    clean_report["mapped_columns"] = mapped_columns
    clean_report["mapped_columns_count"] = int(len(mapped_columns))

    if "Date" not in df.columns:
        inferred_date = _infer_date_from_fallback_columns(df, log)
        if inferred_date is not None:
            df["Date"] = inferred_date

    if "Montant" not in df.columns and "Montant_Signe" in df.columns:
        df["Montant"] = df["Montant_Signe"]
        log.append("⚠ Colonne 'Montant' absente: reconstruction depuis 'Montant_Signe' (valeur absolue)")

    missing_critical = [c for c in CRITICAL_CANONICAL_COLUMNS if c not in df.columns]
    if missing_critical:
        available = ", ".join(str(c) for c in df.columns)
        raise ValueError(
            "Colonnes critiques manquantes : "
            + ", ".join(missing_critical)
            + f". Colonnes detectees : {available}"
        )

    for col, default in OPTIONAL_CANONICAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
            clean_report["defaulted_optional_columns"].append(col)
            log.append(f"⚠ Colonne '{col}' absente : valeur par défaut appliquée")

    duplicate_mask = df.duplicated(keep="first")
    duplicate_count = int(duplicate_mask.sum())
    clean_report["duplicates_removed"] = duplicate_count
    log.append(f"ℹ Doublons exacts détectés: {duplicate_count}")
    if duplicate_count:
        dup_df = df.loc[duplicate_mask].copy()
        dup_df["reject_reason"] = "duplicate_exact"
        dup_df["reject_stage"] = "cleaning_dedup"
        rejected_parts.append(dup_df)
        log.append(f"⚠ {duplicate_count} doublon(s) exact(s) supprimé(s)")
    df = df.loc[~duplicate_mask].copy()

    raw_date = df["Date"].copy()
    df["Date"] = _parse_dates_series(raw_date)
    raw_amount = df["Montant"].apply(_parse_amount)
    raw_signed = df["Montant_Signe"].apply(_parse_amount) if "Montant_Signe" in df.columns else pd.Series([None] * len(df), index=df.index)
    df["Montant"] = raw_amount.abs()

    df["TypeTransaction"] = [
        _normalize_type_transaction(tt, signed if signed is not None else amt)
        for tt, signed, amt in zip(df.get("TypeTransaction"), raw_signed, raw_amount)
    ]

    for col in ["Département", "TypeDépense", "Responsable", "Client_Fournisseur", "Projet"]:
        df[col] = df[col].apply(_normalize_text)

    df["Département"] = df["Département"].fillna("Non renseigné")
    df["TypeDépense"] = df["TypeDépense"].fillna("N/A")
    df["Responsable"] = df["Responsable"].fillna("Non renseigné")
    df["Client_Fournisseur"] = df["Client_Fournisseur"].fillna("Non renseigné")
    df["Projet"] = df["Projet"].fillna("Sans projet")

    computed_signed = [
        (-abs(m) if t == "Depense" else abs(m)) if pd.notna(m) else None
        for m, t in zip(df["Montant"], df["TypeTransaction"])
    ]
    df["Montant_Signe"] = [
        float(signed) if signed is not None and not pd.isna(signed) else computed
        for signed, computed in zip(raw_signed, computed_signed)
    ]

    invalid_date_mask = df["Date"].isna()
    invalid_amount_mask = df["Montant"].isna()
    invalid_mask = invalid_date_mask | invalid_amount_mask
    invalid_count = int(invalid_mask.sum())

    invalid_date_only = int((invalid_date_mask & ~invalid_amount_mask).sum())
    invalid_amount_only = int((invalid_amount_mask & ~invalid_date_mask).sum())
    invalid_both = int((invalid_date_mask & invalid_amount_mask).sum())

    clean_report["invalid_rows_removed"] = invalid_count
    clean_report["invalid_by_reason"] = {
        "invalid_date_only": invalid_date_only,
        "invalid_montant_only": invalid_amount_only,
        "invalid_date_and_montant": invalid_both,
    }
    log.append(
        "ℹ Validation Date/Montant: "
        f"invalides={invalid_count}, date={invalid_date_only}, "
        f"montant={invalid_amount_only}, date+montant={invalid_both}"
    )

    if invalid_count:
        invalid_df = df.loc[invalid_mask].copy()
        reasons = []
        for d_bad, m_bad in zip(invalid_date_mask[invalid_mask], invalid_amount_mask[invalid_mask]):
            if d_bad and m_bad:
                reasons.append("invalid_date|invalid_montant")
            elif d_bad:
                reasons.append("invalid_date")
            else:
                reasons.append("invalid_montant")
        invalid_df["reject_reason"] = reasons
        invalid_df["reject_stage"] = "cleaning_validation"
        rejected_parts.append(invalid_df)

        log.append(f"⚠ {invalid_count} ligne(s) invalide(s) supprimée(s)")
        log.append(
            "ℹ Rejets validation: "
            f"date={invalid_date_only}, montant={invalid_amount_only}, date+montant={invalid_both}"
        )
        df = df.loc[~invalid_mask].copy()

    if df.empty:
        raise ValueError("Aucune ligne exploitable après nettoyage. Vérifiez Date et Montant dans le fichier importé.")

    df["Année"] = df["Date"].dt.year
    df["Mois"] = df["Date"].dt.month
    df["Trimestre"] = df["Date"].dt.quarter
    df["Semaine"] = df["Date"].dt.isocalendar().week.astype("Int64")
    df["JourSemaine"] = df["Date"].dt.day_name()
    df["JourAnnée"] = df["Date"].dt.dayofyear
    df["YearMonth"] = df["Date"].dt.strftime("%Y-%m")
    df["AnnéeFiscale"] = df["Date"].dt.year

    final_cols = [
        "_source_row",
        "Date",
        "DepartementID",
        "Département",
        "TypeTransaction",
        "TypeDépense",
        "Montant",
        "Montant_Signe",
        "Responsable",
        "Client_Fournisseur",
        "Projet",
        "Année",
        "Mois",
        "Trimestre",
        "AnnéeFiscale",
        "JourSemaine",
        "Semaine",
        "JourAnnée",
        "YearMonth",
    ]
    for col in final_cols:
        if col not in df.columns:
            df[col] = None

    df = df[final_cols].copy()
    clean_report["rows_after_cleaning"] = int(len(df))
    log.append(f"✓ Nettoyage terminé : {len(df)} lignes, {len(df.columns) - 1} colonnes utiles")

    if rejected_parts:
        rejected_df = pd.concat(rejected_parts, ignore_index=True, sort=False)
    else:
        rejected_df = pd.DataFrame(columns=list(df.columns) + ["reject_reason", "reject_stage"])

    return df.reset_index(drop=True), log, clean_report, rejected_df.reset_index(drop=True)


def _fetch_lookup(cursor, table, key_col, id_col) -> dict:
    cursor.execute(f"SELECT `{id_col}`, `{key_col}` FROM `{table}`")
    return {r[key_col]: r[id_col] for r in cursor.fetchall() if r[key_col] is not None}


def _fetch_combo_lookup(cursor, table, cols, id_col) -> dict:
    cols_sql = ", ".join(f"`{c}`" for c in [id_col] + cols)
    cursor.execute(f"SELECT {cols_sql} FROM `{table}`")
    return {tuple(r[c] for c in cols): r[id_col] for r in cursor.fetchall()}


def _next_id(cursor, table, id_col) -> int:
    cursor.execute(f"SELECT COALESCE(MAX(`{id_col}`), 0) + 1 AS n FROM `{table}`")
    return int(cursor.fetchone()["n"])


def _ensure_date_dimension(cursor, df, log) -> dict:
    existing = _fetch_lookup(cursor, "date", "Date", "Date_ID")
    next_id = _next_id(cursor, "date", "Date_ID")
    new_dates = sorted(set(df["Date"].dropna().dt.strftime("%Y-%m-%d").unique()) - set(existing))

    for value in new_dates:
        d = pd.to_datetime(value)
        cursor.execute(
            """INSERT INTO `date`
               (`Date_ID`,`Date`,`Année`,`Mois`,`Trimestre`,`AnnéeFiscale`,`JourSemaine`,`Semaine`,`JourAnnée`,`YearMonth`)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                next_id,
                value,
                int(d.year),
                int(d.month),
                int(d.quarter),
                int(d.year),
                d.day_name(),
                int(d.isocalendar().week),
                int(d.dayofyear),
                d.strftime("%Y-%m"),
            ),
        )
        existing[value] = next_id
        next_id += 1

    if new_dates:
        log.append(f"✓ {len(new_dates)} date(s) ajoutée(s)")
    return existing


def _ensure_departements(cursor, df, log) -> dict:
    lookup = _fetch_lookup(cursor, "departement", "NomDepartement", "Departement_ID")
    next_id = _next_id(cursor, "departement", "Departement_ID")
    inserted = 0

    for dep in df["Département"].dropna().drop_duplicates():
        if dep in lookup:
            continue
        cursor.execute(
            "INSERT INTO `departement` (`Departement_ID`,`NomDepartement`) VALUES (%s,%s)",
            (next_id, dep),
        )
        lookup[dep] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} département(s) ajouté(s)")
    return lookup


def _ensure_typetransactions(cursor, df, log) -> dict:
    lookup = _fetch_lookup(cursor, "typetransaction", "TypeTransaction", "TypeTransaction_ID")
    next_id = _next_id(cursor, "typetransaction", "TypeTransaction_ID")
    inserted = 0

    for ttx in df["TypeTransaction"].dropna().drop_duplicates():
        if ttx in lookup:
            continue
        cursor.execute(
            "INSERT INTO `typetransaction` (`TypeTransaction_ID`,`TypeTransaction`) VALUES (%s,%s)",
            (next_id, ttx),
        )
        lookup[ttx] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} type(s) transaction ajouté(s)")
    return lookup


def _ensure_typedepenses(cursor, df, log) -> dict:
    lookup = _fetch_lookup(cursor, "typedepense", "TypeDepense", "TypeDepense_ID")
    next_id = _next_id(cursor, "typedepense", "TypeDepense_ID")
    inserted = 0

    for tdep in df["TypeDépense"].dropna().drop_duplicates():
        if tdep in lookup:
            continue
        cursor.execute(
            "INSERT INTO `typedepense` (`TypeDepense_ID`,`TypeDepense`) VALUES (%s,%s)",
            (next_id, tdep),
        )
        lookup[tdep] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} type(s) dépense ajouté(s)")
    return lookup


def _ensure_responsables(cursor, df, log) -> dict:
    lookup = _fetch_combo_lookup(cursor, "responsable", ["NomResponsable", "Departement"], "Responsable_ID")
    next_id = _next_id(cursor, "responsable", "Responsable_ID")
    inserted = 0

    for nom, dept in df[["Responsable", "Département"]].drop_duplicates().dropna().itertuples(index=False, name=None):
        if (nom, dept) in lookup:
            continue
        cursor.execute(
            "INSERT INTO `responsable` (`Responsable_ID`,`NomResponsable`,`Departement`) VALUES (%s,%s,%s)",
            (next_id, nom, dept),
        )
        lookup[(nom, dept)] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} responsable(s) ajouté(s)")
    return lookup


def _ensure_clientfournisseur(cursor, df, log) -> dict:
    lookup = _fetch_lookup(cursor, "clientfournisseur", "NomClientFournisseur", "ClientFournisseur_ID")
    next_id = _next_id(cursor, "clientfournisseur", "ClientFournisseur_ID")
    inserted = 0

    for nom, ttype in df[["Client_Fournisseur", "TypeTransaction"]].drop_duplicates().dropna().itertuples(index=False, name=None):
        if nom in lookup:
            continue
        cf_type = "Client" if str(ttype).strip().lower() == "revenu" else "Fournisseur"
        cursor.execute(
            "INSERT INTO `clientfournisseur` (`ClientFournisseur_ID`,`NomClientFournisseur`,`Type`) VALUES (%s,%s,%s)",
            (next_id, nom, cf_type),
        )
        lookup[nom] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} client(s)/fournisseur(s) ajouté(s)")
    return lookup


def _ensure_projets(cursor, df, log) -> dict:
    lookup = _fetch_lookup(cursor, "projet", "NomProjet", "Projet_ID")
    next_id = _next_id(cursor, "projet", "Projet_ID")
    inserted = 0

    grouped = df.groupby("Projet")["Date"].agg(DateDebut="min", DateFin="max").reset_index()
    for row in grouped.itertuples(index=False):
        nom = row.Projet
        if nom in lookup or nom is None:
            continue

        d1 = pd.to_datetime(row.DateDebut).strftime("%Y-%m-%d") if pd.notna(row.DateDebut) else None
        d2 = pd.to_datetime(row.DateFin).strftime("%Y-%m-%d") if pd.notna(row.DateFin) else None
        cursor.execute(
            "INSERT INTO `projet` (`Projet_ID`,`NomProjet`,`DateDebut`,`DateFin`) VALUES (%s,%s,%s,%s)",
            (next_id, nom, d1, d2),
        )
        lookup[nom] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} projet(s) ajouté(s)")
    return lookup


def _load_transactions(cursor, df, maps, replace_existing, log) -> dict:
    if replace_existing:
        cursor.execute("DELETE FROM `transactions`")
        next_id = 1
        log.append("✓ Table transactions vidée avant rechargement")
    else:
        next_id = _next_id(cursor, "transactions", "Transaction_ID")

    rows = []
    skipped_by_reason = {
        "missing_dim_date": 0,
        "missing_dim_departement": 0,
        "missing_dim_typetransaction": 0,
        "missing_dim_typedepense": 0,
        "missing_dim_responsable": 0,
        "missing_dim_clientfournisseur": 0,
        "missing_dim_projet": 0,
    }
    rejected_records = []

    for _, row in df.iterrows():
        date_key = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
        dept = row["Département"]
        dim_map = {
            "missing_dim_date": maps["date"].get(date_key),
            "missing_dim_departement": maps["departement"].get(dept),
            "missing_dim_typetransaction": maps["typetransaction"].get(row["TypeTransaction"]),
            "missing_dim_typedepense": maps["typedepense"].get(row["TypeDépense"]),
            "missing_dim_responsable": maps["responsable"].get((row["Responsable"], dept)),
            "missing_dim_clientfournisseur": maps["clientfournisseur"].get(row["Client_Fournisseur"]),
            "missing_dim_projet": maps["projet"].get(row["Projet"]),
        }

        missing_reasons = [reason for reason, value in dim_map.items() if value is None]
        if missing_reasons:
            for reason in missing_reasons:
                skipped_by_reason[reason] += 1
            rejected = row.to_dict()
            rejected["reject_reason"] = "|".join(missing_reasons)
            rejected["reject_stage"] = "load_dimensions"
            rejected_records.append(rejected)
            continue

        ids = tuple(dim_map.values())
        rows.append(
            (
                next_id,
                *[int(i) for i in ids],
                round(float(row["Montant"]), 3),
                round(float(row["Montant_Signe"]), 3),
            )
        )
        next_id += 1

    if rows:
        cursor.executemany(
            """INSERT INTO `transactions`
               (`Transaction_ID`,`Date_ID`,`Departement_ID`,`TypeTransaction_ID`,
                `TypeDepense_ID`,`Responsable_ID`,`ClientFournisseur_ID`,
                `Projet_ID`,`Montant`,`Montant_Signe`)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            rows,
        )

    skipped_total = int(len(rejected_records))
    detail = ", ".join(f"{k}={v}" for k, v in skipped_by_reason.items())
    log.append(f"ℹ Rejets dimensions: {detail}")
    if skipped_total:
        log.append(f"⚠ {skipped_total} transaction(s) ignorée(s) au chargement")
    log.append(f"✓ {len(rows)} transaction(s) insérée(s)")

    rejected_df = pd.DataFrame(rejected_records) if rejected_records else pd.DataFrame(columns=list(df.columns) + ["reject_reason", "reject_stage"])
    return {
        "rows_inserted": int(len(rows)),
        "rows_skipped": skipped_total,
        "skipped_by_reason": skipped_by_reason,
        "rejected_rows": rejected_df,
    }


def run_generic_etl(file_path: str, replace_existing: bool = True) -> dict:
    path = Path(file_path)
    log = []

    try:
        df_raw = _read_file(path, log)
        df_clean, clean_log, clean_report, clean_rejected = _clean_dataframe(df_raw)
        log.extend(clean_log)

        out_path = path.parent / "donnees_nettoyees.csv"
        df_clean_export = df_clean.drop(columns=["_source_row"], errors="ignore")
        df_clean_export.to_csv(out_path, index=False, encoding="utf-8-sig")
        log.append(f"✓ CSV nettoyé exporté : {out_path.name}")

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                maps = {
                    "date": _ensure_date_dimension(cursor, df_clean, log),
                    "departement": _ensure_departements(cursor, df_clean, log),
                    "typetransaction": _ensure_typetransactions(cursor, df_clean, log),
                    "typedepense": _ensure_typedepenses(cursor, df_clean, log),
                    "responsable": _ensure_responsables(cursor, df_clean, log),
                    "clientfournisseur": _ensure_clientfournisseur(cursor, df_clean, log),
                    "projet": _ensure_projets(cursor, df_clean, log),
                }
                load_result = _load_transactions(cursor, df_clean, maps, replace_existing, log)
            conn.commit()
        finally:
            conn.close()

        load_rejected = load_result.get("rejected_rows")
        rejected_parts = []
        if clean_rejected is not None and not clean_rejected.empty:
            rejected_parts.append(clean_rejected)
        if load_rejected is not None and not load_rejected.empty:
            rejected_parts.append(load_rejected)

        if rejected_parts:
            rejected_df = pd.concat(rejected_parts, ignore_index=True, sort=False)
        else:
            rejected_df = pd.DataFrame(columns=list(df_raw.columns) + ["_source_row", "reject_reason", "reject_stage"])

        if "_source_row" in rejected_df.columns:
            rejected_df = rejected_df.sort_values(by="_source_row", kind="stable").reset_index(drop=True)

        rejected_path = path.parent / REJECTED_ROWS_FILENAME
        rejected_df.to_csv(rejected_path, index=False, encoding="utf-8-sig")
        log.append(f"✓ Lignes rejetées exportées : {rejected_path.name} ({len(rejected_df)} ligne(s))")

        rows_inserted = int(load_result.get("rows_inserted", 0))
        rows_skipped_load = int(load_result.get("rows_skipped", 0))
        total_rejected = (
            int(clean_report.get("duplicates_removed", 0))
            + int(clean_report.get("invalid_rows_removed", 0))
            + rows_skipped_load
        )
        rejection_summary = {
            "input_rows": int(clean_report.get("input_rows", len(df_raw))),
            "duplicates_removed": int(clean_report.get("duplicates_removed", 0)),
            "invalid_rows_removed": int(clean_report.get("invalid_rows_removed", 0)),
            "invalid_by_reason": clean_report.get("invalid_by_reason", {}),
            "rows_after_cleaning": int(clean_report.get("rows_after_cleaning", len(df_clean))),
            "rows_skipped_during_load": rows_skipped_load,
            "load_skipped_by_reason": load_result.get("skipped_by_reason", {}),
            "rows_inserted": rows_inserted,
            "total_rejected_rows": total_rejected,
        }

        log.append(
            "ℹ Résumé rejets: "
            f"doublons={rejection_summary['duplicates_removed']}, "
            f"invalides={rejection_summary['invalid_rows_removed']}, "
            f"chargement={rejection_summary['rows_skipped_during_load']}, "
            f"insérées={rejection_summary['rows_inserted']}"
        )

        before_rows = _json_safe_records(df_raw)
        after_rows = _json_safe_records(df_clean_export)

        return {
            "success": True,
            "log": log,
            "before_rows": before_rows,
            "after_rows": after_rows,
            "changed_rows": min(len(before_rows), len(after_rows)),
            "stats": {
                "lignes": int(len(df_clean_export)),
                "colonnes": int(len(df_clean_export.columns)),
                "nb_erreurs": int(total_rejected),
                "taux_correction": f"{(rows_inserted / max(1, int(clean_report.get('input_rows', len(df_raw)))) * 100):.1f}%",
                "total": round(float(df_clean_export["Montant"].sum()), 2),
                "solde": round(float(df_clean_export["Montant_Signe"].sum()), 2),
                "revenus": round(float(df_clean_export.loc[df_clean_export["Montant_Signe"] > 0, "Montant_Signe"].sum()), 2),
                "depenses": round(float(df_clean_export.loc[df_clean_export["Montant_Signe"] < 0, "Montant_Signe"].sum()), 2),
            },
            "rejections": rejection_summary,
            "db_result": {
                "db_name": DB_CONFIG["database"],
                "table_name": "transactions",
                "rows_inserted": rows_inserted,
                "rows_skipped": rows_skipped_load,
                "rows_rejected_total": total_rejected,
                "skipped_by_reason": load_result.get("skipped_by_reason", {}),
                "mode": "star_schema",
            },
            "artifacts": {
                "cleaned_csv": str(out_path),
                "rejected_rows_csv": str(rejected_path),
            },
            "column_mapping": {
                "mapped_columns_count": int(clean_report.get("mapped_columns_count", 0)),
                "mapped_columns": clean_report.get("mapped_columns", {}),
                "defaulted_optional_columns": clean_report.get("defaulted_optional_columns", []),
            },
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "detail": traceback.format_exc(),
            "log": log + [f"✗ Erreur ETL : {e}"],
        }
    finally:
        gc.collect()


if __name__ == "__main__":
    import pprint
    import sys

    if len(sys.argv) < 2:
        print("Usage: python etl_generic.py <fichier>")
        raise SystemExit(1)

    pprint.pprint(run_generic_etl(sys.argv[1]))
