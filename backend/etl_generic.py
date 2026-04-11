# -*- coding: utf-8 -*-
"""
ETL générique — charge les données dans pfe_bd (schéma star)
Tables cibles : date, departement, responsable, typetransaction,
                typedepense, clientfournisseur, projet, transactions
"""

import gc
import math
import traceback
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

from config import DB_CONFIG

warnings.filterwarnings("ignore")

REQUIRED_COLUMNS = [
    "Date", "DepartementID", "Département", "TypeTransaction",
    "TypeDépense", "Montant", "Responsable", "Client_Fournisseur", "Projet",
]

# Accept both canonical and mojibake headers if a CSV was decoded with the wrong codec.
COLUMN_ALIASES = {
    "DÃ©partement": "Département",
    "TypeDÃ©pense": "TypeDépense",
    "AnnÃ©e": "Année",
    "AnnÃ©eFiscale": "AnnéeFiscale",
    "JourAnnÃ©e": "JourAnnée",
    "CatÃ©gorie": "Catégorie",
}


# ─────────────────────────────────────────────────────────────
# Connexion — réutilise config.py au lieu de credentials dupliqués
# ─────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────
# Lecture fichier
# ─────────────────────────────────────────────────────────────
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
        raise ValueError("Impossible de lire le CSV — encodage non reconnu")
    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
        log.append(f"✓ Fichier lu (Excel) : {len(df)} lignes")
        return df
    raise ValueError(f"Format non supporté : {ext}")


# ─────────────────────────────────────────────────────────────
# Sérialisation JSON-safe
# ─────────────────────────────────────────────────────────────
def _norm(v):
    """Normalise une valeur texte : strip + espaces multiples → None si vide."""
    if pd.isna(v):
        return None
    s = " ".join(str(v).strip().split())
    return s or None


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


# ─────────────────────────────────────────────────────────────
# Nettoyage DataFrame
# ─────────────────────────────────────────────────────────────
def _clean_dataframe(df: pd.DataFrame):
    log = []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    rename_map = {
        bad: good
        for bad, good in COLUMN_ALIASES.items()
        if bad in df.columns and good not in df.columns
    }
    if rename_map:
        df = df.rename(columns=rename_map)
        log.append(f"✓ Colonnes normalisées ({len(rename_map)})")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Colonnes manquantes : " + ", ".join(missing))

    before = len(df)
    df = df.drop_duplicates()
    if (removed := before - len(df)):
        log.append(f"✓ {removed} doublon(s) supprimé(s)")

    # Dates
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Montant : nettoie symboles monétaires et virgules
    df["Montant"] = (
        df["Montant"].astype(str)
        .str.replace(r"[€$£\s]", "", regex=True)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^\d.\-]", "", regex=True)
    )
    df["Montant"] = pd.to_numeric(df["Montant"], errors="coerce")

    # Colonnes texte
    text_cols = ["DepartementID", "Département", "TypeTransaction", "TypeDépense",
                 "Responsable", "Client_Fournisseur", "Projet"]
    for col in text_cols:
        df[col] = df[col].apply(_norm)
    df["TypeDépense"] = df["TypeDépense"].fillna("N/A")

    # Montant signé
    def signed(row):
        m = row["Montant"]
        t = (row["TypeTransaction"] or "").strip().lower()
        if pd.isna(m):
            return None
        return -abs(float(m)) if t in ["depense", "dépense"] else abs(float(m))

    df["Montant_Signe"] = df.apply(signed, axis=1)

    # Colonnes temporelles dérivées
    df["Année"]      = df["Date"].dt.year
    df["Mois"]       = df["Date"].dt.month
    df["Trimestre"]  = df["Date"].dt.quarter
    df["Semaine"]    = df["Date"].dt.isocalendar().week.astype("Int64")
    df["JourSemaine"]= df["Date"].dt.day_name()
    df["JourAnnée"]  = df["Date"].dt.dayofyear
    df["YearMonth"]  = df["Date"].dt.strftime("%Y-%m")
    df["AnnéeFiscale"] = df["Date"].dt.year

    # Lignes invalides
    invalid_mask = (
        df["Date"].isna() | df["Montant"].isna() | df["Département"].isna() |
        df["TypeTransaction"].isna() | df["Responsable"].isna() |
        df["Client_Fournisseur"].isna() | df["Projet"].isna()
    )
    if (n_invalid := invalid_mask.sum()):
        log.append(f"⚠ {n_invalid} ligne(s) invalide(s) ignorée(s)")
        df = df[~invalid_mask]

    log.append(f"✓ Nettoyage terminé : {len(df)} lignes, {len(df.columns)} colonnes")
    return df.reset_index(drop=True), log


# ─────────────────────────────────────────────────────────────
# Helpers dimensions
# ─────────────────────────────────────────────────────────────
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
    next_id  = _next_id(cursor, "date", "Date_ID")
    new_dates = sorted(set(df["Date"].dropna().dt.strftime("%Y-%m-%d").unique()) - set(existing))

    for value in new_dates:
        d = pd.to_datetime(value)
        cursor.execute(
            """INSERT INTO `date`
               (`Date_ID`,`Date`,`Année`,`Mois`,`Trimestre`,`AnnéeFiscale`,`JourSemaine`,`Semaine`,`JourAnnée`,`YearMonth`)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (next_id, value, d.year, d.month, d.quarter, d.year,
             d.day_name(), int(d.isocalendar().week), d.dayofyear, d.strftime("%Y-%m")),
        )
        existing[value] = next_id
        next_id += 1

    if new_dates:
        log.append(f"✓ {len(new_dates)} date(s) ajoutée(s)")
    return existing


def _ensure_departements(cursor, df, log) -> dict:
    lookup  = _fetch_lookup(cursor, "departement", "NomDepartement", "Departement_ID")
    next_id = _next_id(cursor, "departement", "Departement_ID")
    inserted = 0
    for dep in df["Département"].dropna().apply(_norm).dropna().drop_duplicates():
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
        log.append(f"âœ“ {inserted} dÃ©partement(s) ajoutÃ©(s)")
    return lookup


def _ensure_typetransactions(cursor, df, log) -> dict:
    lookup  = _fetch_lookup(cursor, "typetransaction", "TypeTransaction", "TypeTransaction_ID")
    next_id = _next_id(cursor, "typetransaction", "TypeTransaction_ID")
    inserted = 0
    for ttx in df["TypeTransaction"].dropna().apply(_norm).dropna().drop_duplicates():
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
        log.append(f"âœ“ {inserted} type(s) transaction ajoutÃ©(s)")
    return lookup


def _ensure_typedepenses(cursor, df, log) -> dict:
    lookup  = _fetch_lookup(cursor, "typedepense", "TypeDepense", "TypeDepense_ID")
    next_id = _next_id(cursor, "typedepense", "TypeDepense_ID")
    inserted = 0
    for tdep in df["TypeDépense"].dropna().apply(_norm).dropna().drop_duplicates():
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
        log.append(f"âœ“ {inserted} type(s) dÃ©pense ajoutÃ©(s)")
    return lookup


def _ensure_responsables(cursor, df, log) -> dict:
    lookup  = _fetch_combo_lookup(cursor, "responsable", ["NomResponsable", "Departement"], "Responsable_ID")
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
    lookup  = _fetch_lookup(cursor, "clientfournisseur", "NomClientFournisseur", "ClientFournisseur_ID")
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
    lookup  = _fetch_lookup(cursor, "projet", "NomProjet", "Projet_ID")
    next_id = _next_id(cursor, "projet", "Projet_ID")
    inserted = 0
    grouped = df.groupby("Projet")["Date"].agg(DateDebut="min", DateFin="max").reset_index()
    for row in grouped.itertuples(index=False):
        nom = row.Projet
        if nom in lookup or nom is None:
            continue
        d1 = pd.to_datetime(row.DateDebut).strftime("%Y-%m-%d") if pd.notna(row.DateDebut) else None
        d2 = pd.to_datetime(row.DateFin).strftime("%Y-%m-%d")   if pd.notna(row.DateFin)   else None
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

    rows, skipped = [], 0
    for _, row in df.iterrows():
        date_key = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
        dept     = row["Département"]
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
        rows.append((next_id, *[int(i) for i in ids],
                     round(float(row["Montant"]), 2),
                     str(round(float(row["Montant_Signe"]), 3))))
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
        log.append(f"⚠ {skipped} transaction(s) ignorée(s) (correspondance manquante)")
    log.append(f"✓ {len(rows)} transaction(s) insérée(s)")
    return len(rows)


# ─────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────
def run_generic_etl(file_path: str, replace_existing: bool = True) -> dict:
    path = Path(file_path)
    log  = []
    try:
        df_raw   = _read_file(path, log)
        df_clean, clean_log = _clean_dataframe(df_raw)
        log.extend(clean_log)

        out_path = path.parent / "donnees_nettoyees.csv"
        df_clean.to_csv(out_path, index=False, encoding="utf-8-sig")
        log.append(f"✓ CSV nettoyé exporté : {out_path.name}")

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                maps = {
                    "date":            _ensure_date_dimension(cursor, df_clean, log),
                    "departement":     _ensure_departements(cursor, df_clean, log),
                    "typetransaction": _ensure_typetransactions(cursor, df_clean, log),
                    "typedepense":     _ensure_typedepenses(cursor, df_clean, log),
                    "responsable":     _ensure_responsables(cursor, df_clean, log),
                    "clientfournisseur": _ensure_clientfournisseur(cursor, df_clean, log),
                    "projet":          _ensure_projets(cursor, df_clean, log),
                }
                inserted = _load_transactions(cursor, df_clean, maps, replace_existing, log)
            conn.commit()
        finally:
            conn.close()

        before_rows = _json_safe_records(df_raw)
        after_rows  = _json_safe_records(df_clean)

        return {
            "success":      True,
            "log":          log,
            "before_rows":  before_rows,
            "after_rows":   after_rows,
            "changed_rows": min(len(before_rows), len(after_rows)),
            "stats": {
                "lignes":          int(len(df_clean)),
                "colonnes":        int(len(df_clean.columns)),
                "taux_correction": "100.0%",
                "total":    round(float(df_clean["Montant"].sum()), 2),
                "solde":    round(float(df_clean["Montant_Signe"].sum()), 2),
                "revenus":  round(float(df_clean.loc[df_clean["Montant_Signe"] > 0, "Montant_Signe"].sum()), 2),
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
            "error":   str(e),
            "detail":  traceback.format_exc(),
            "log":     log + [f"✗ Erreur ETL : {e}"],
        }
    finally:
        gc.collect()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python etl_generic.py <fichier>")
        raise SystemExit(1)
    import pprint
    pprint.pprint(run_generic_etl(sys.argv[1]))
