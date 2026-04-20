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
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

from config import DB_CONFIG

warnings.filterwarnings("ignore")

CRITICAL_CANONICAL_COLUMNS = ("Date", "Montant")

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


def _resolve_column_mapping(df: pd.DataFrame, log: list) -> pd.DataFrame:
    mapped = {}
    used_src = set()

    normalized_to_original = {}
    for col in df.columns:
        ncol = _normalize_header(col)
        if ncol and ncol not in normalized_to_original:
            normalized_to_original[ncol] = col

    for canonical, aliases in HEADER_ALIASES.items():
        candidates = [canonical, *aliases]
        for alias in candidates:
            n_alias = _normalize_header(alias)
            src = normalized_to_original.get(n_alias)
            if not src or src in used_src:
                continue
            mapped[src] = canonical
            used_src.add(src)
            break

    if mapped:
        df = df.rename(columns=mapped)
        log.append(f"✓ Colonnes reconnues automatiquement : {len(mapped)}")

    return df


def _clean_dataframe(df: pd.DataFrame):
    log = []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = _resolve_column_mapping(df, log)

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
            log.append(f"⚠ Colonne '{col}' absente : valeur par défaut appliquée")

    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)
    if removed:
        log.append(f"✓ {removed} doublon(s) supprimé(s)")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)

    raw_amount = df["Montant"].apply(_parse_amount)
    df["Montant"] = raw_amount.abs()

    df["TypeTransaction"] = [
        _normalize_type_transaction(tt, amt)
        for tt, amt in zip(df.get("TypeTransaction"), raw_amount)
    ]

    for col in ["Département", "TypeDépense", "Responsable", "Client_Fournisseur", "Projet"]:
        df[col] = df[col].apply(_normalize_text)

    df["Département"] = df["Département"].fillna("Non renseigné")
    df["TypeDépense"] = df["TypeDépense"].fillna("N/A")
    df["Responsable"] = df["Responsable"].fillna("Non renseigné")
    df["Client_Fournisseur"] = df["Client_Fournisseur"].fillna("Non renseigné")
    df["Projet"] = df["Projet"].fillna("Sans projet")

    df["Montant_Signe"] = [
        (-abs(m) if t == "Depense" else abs(m)) if pd.notna(m) else None
        for m, t in zip(df["Montant"], df["TypeTransaction"])
    ]

    df["Année"] = df["Date"].dt.year
    df["Mois"] = df["Date"].dt.month
    df["Trimestre"] = df["Date"].dt.quarter
    df["Semaine"] = df["Date"].dt.isocalendar().week.astype("Int64")
    df["JourSemaine"] = df["Date"].dt.day_name()
    df["JourAnnée"] = df["Date"].dt.dayofyear
    df["YearMonth"] = df["Date"].dt.strftime("%Y-%m")
    df["AnnéeFiscale"] = df["Date"].dt.year

    invalid_mask = df["Date"].isna() | df["Montant"].isna()
    invalid_count = int(invalid_mask.sum())
    if invalid_count:
        log.append(f"⚠ {invalid_count} ligne(s) invalide(s) ignorée(s) (Date ou Montant)")
        df = df[~invalid_mask]

    if df.empty:
        raise ValueError("Aucune ligne exploitable après nettoyage. Vérifiez Date et Montant dans le fichier importé.")

    final_cols = [
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

    df = df[final_cols]
    log.append(f"✓ Nettoyage terminé : {len(df)} lignes, {len(df.columns)} colonnes")
    return df.reset_index(drop=True), log


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


def _load_transactions(cursor, df, maps, replace_existing, log) -> int:
    if replace_existing:
        cursor.execute("DELETE FROM `transactions`")
        next_id = 1
        log.append("✓ Table transactions vidée avant rechargement")
    else:
        next_id = _next_id(cursor, "transactions", "Transaction_ID")

    rows = []
    skipped = 0

    for _, row in df.iterrows():
        date_key = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
        dept = row["Département"]
        ids = (
            maps["date"].get(date_key),
            maps["departement"].get(dept),
            maps["typetransaction"].get(row["TypeTransaction"]),
            maps["typedepense"].get(row["TypeDépense"]),
            maps["responsable"].get((row["Responsable"], dept)),
            maps["clientfournisseur"].get(row["Client_Fournisseur"]),
            maps["projet"].get(row["Projet"]),
        )

        if None in ids:
            skipped += 1
            continue

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

    if skipped:
        log.append(f"⚠ {skipped} transaction(s) ignorée(s) (correspondance dimension manquante)")
    log.append(f"✓ {len(rows)} transaction(s) insérée(s)")
    return len(rows)


def run_generic_etl(file_path: str, replace_existing: bool = True) -> dict:
    path = Path(file_path)
    log = []

    try:
        df_raw = _read_file(path, log)
        df_clean, clean_log = _clean_dataframe(df_raw)
        log.extend(clean_log)

        out_path = path.parent / "donnees_nettoyees.csv"
        df_clean.to_csv(out_path, index=False, encoding="utf-8-sig")
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
                inserted = _load_transactions(cursor, df_clean, maps, replace_existing, log)
            conn.commit()
        finally:
            conn.close()

        before_rows = _json_safe_records(df_raw)
        after_rows = _json_safe_records(df_clean)

        return {
            "success": True,
            "log": log,
            "before_rows": before_rows,
            "after_rows": after_rows,
            "changed_rows": min(len(before_rows), len(after_rows)),
            "stats": {
                "lignes": int(len(df_clean)),
                "colonnes": int(len(df_clean.columns)),
                "taux_correction": "100.0%",
                "total": round(float(df_clean["Montant"].sum()), 2),
                "solde": round(float(df_clean["Montant_Signe"].sum()), 2),
                "revenus": round(float(df_clean.loc[df_clean["Montant_Signe"] > 0, "Montant_Signe"].sum()), 2),
                "depenses": round(float(df_clean.loc[df_clean["Montant_Signe"] < 0, "Montant_Signe"].sum()), 2),
            },
            "db_result": {
                "db_name": DB_CONFIG["database"],
                "table_name": "transactions",
                "rows_inserted": inserted,
                "mode": "star_schema",
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
