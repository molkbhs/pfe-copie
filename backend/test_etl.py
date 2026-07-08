#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test ETL import to diagnose the real issue"""

import json
from config import DB_CONFIG
from app import run_generic_etl, save_import_history, run_query
from pathlib import Path

# Test with a small CSV file
test_file = Path(r"c:\Users\molkb\OneDrive\Documents\pfe-molk-main\pfe-molk-main\backend\uploads_etl\mini.csv")

if not test_file.exists():
    print(f"[ERROR] Test file not found: {test_file}")
    exit(1)

print(f"[INFO] Testing ETL with file: {test_file.name}")
print("=" * 80)

# Run ETL
result = run_generic_etl(str(test_file), replace_existing=True)

print(f"\n[RESULT] ETL Success: {result.get('success')}")
print(f"[STATS] {json.dumps(result.get('stats', {}), indent=2)}")
print(f"[ERROR] {result.get('error', 'None')}")
print(f"[DETAIL] {result.get('detail', 'None')}")

if result.get('log'):
    print(f"\n[LOG]:")
    for entry in result.get('log', [])[:10]:
        print(f"  - {entry}")

if result.get('after_rows'):
    print(f"\n[SAMPLE ROW] (first 5 rows)")
    for row in result.get('after_rows', [])[:5]:
        print(f"  {json.dumps(row, indent=4, default=str)}")

# Check transactions table before and after
try:
    before_count = run_query("SELECT COUNT(*) AS n FROM transactions", fetch_one=True)
    print(f"\n[DB STATE] transactions table rows: {before_count.get('n')}")
    
    # Check historique_imports
    imports = run_query("SELECT id, user_id, nom_fichier, statut, nb_lignes FROM historique_imports ORDER BY id DESC LIMIT 3", fetch_one=False)
    print(f"\n[IMPORTS] Last 3 imports:")
    for imp in imports or []:
        print(f"  - ID={imp['id']}, user_id={imp['user_id']}, file={imp['nom_fichier']}, status={imp['statut']}, rows={imp['nb_lignes']}")
except Exception as e:
    print(f"[ERROR] Failed to query DB: {e}")

print("\n" + "=" * 80)
