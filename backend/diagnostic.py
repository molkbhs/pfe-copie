#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic script to debug ETL, KPI calculations, and dataset selection."""

import sys
import json
import pandas as pd
from config import DB_CONFIG
from app import (
    get_active_user_dataset, 
    get_last_successful_import,
    _build_kpis_from_rows,
    decode_import_rows,
    run_query,
)

def diagnose():
    print("=" * 80)
    print("[DIAGNOSTIC] ETL, KPI Calculations, Dataset Selection")
    print("=" * 80)
    
    # Test DB connection
    try:
        result = run_query("SELECT 1")
        print("✓ Database connection OK")
    except Exception as e:
        print(f"✗ Database connection FAILED: {e}")
        return
    
    # Get all users
    try:
        users = run_query("SELECT id, email FROM users LIMIT 5", fetch_one=False) or []
        print(f"✓ Found {len(users)} users in database")
    except Exception as e:
        print(f"✗ Failed to query users: {e}")
        return
    
    if not users:
        print("✗ No users found!")
        return
    
    # Test with first user
    user_id = users[0].get("id")
    user_email = users[0].get("email")
    print(f"\n--- Testing with user: {user_email} (ID={user_id}) ---")
    
    # Check imports for this user
    try:
        imports = run_query(
            "SELECT id, nom_fichier, statut, nb_lignes, date_import FROM historique_imports WHERE user_id=%s ORDER BY date_import DESC LIMIT 5",
            (user_id,),
            fetch_one=False
        ) or []
        print(f"✓ Found {len(imports)} imports for user")
        for imp in imports:
            print(f"  - {imp['nom_fichier']} ({imp['statut']}) - {imp['nb_lignes']} rows - {imp['date_import']}")
    except Exception as e:
        print(f"✗ Failed to query imports: {e}")
        return
    
    # Get last successful import
    try:
        last_import = get_last_successful_import(user_id)
        if last_import:
            print(f"\n✓ Last successful import found:")
            print(f"  - ID: {last_import.get('id')}")
            print(f"  - File: {last_import.get('nom_fichier')}")
            print(f"  - Status: {last_import.get('statut')}")
            print(f"  - Date: {last_import.get('date_import')}")
            print(f"  - DB nb_lignes: {last_import.get('nb_lignes')}")
            
            # Decode rows from import
            raw_data = last_import.get("data")
            if raw_data:
                decoded_rows = decode_import_rows(raw_data)
                print(f"  - Decoded rows: {len(decoded_rows)}")
                if decoded_rows:
                    print(f"  - First row keys: {list(decoded_rows[0].keys())}")
                    print(f"  - First row sample: {decoded_rows[0]}")
            else:
                print("  - NO DATA STORED in import!")
        else:
            print("✗ No last successful import found!")
    except Exception as e:
        print(f"✗ Failed to get last import: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Get active dataset (this is what analytics uses)
    try:
        dataset = get_active_user_dataset(user_id)
        print(f"\n--- Active Dataset for Analytics ---")
        print(f"  - Success: {dataset.get('success')}")
        print(f"  - Source: {dataset.get('source')}")
        print(f"  - Import ID: {dataset.get('import_id')}")
        print(f"  - Filename: {dataset.get('filename')}")
        print(f"  - Rows count: {len(dataset.get('rows') or [])}")
        print(f"  - Message: {dataset.get('message')}")
        
        if dataset.get('success') and dataset.get('rows'):
            rows = dataset.get('rows')
            print(f"\n  - Row sample (first row):")
            if rows:
                print(f"    {json.dumps(rows[0], indent=6, default=str)}")
            
            # Check critical columns
            print(f"\n  - Column analysis:")
            df = pd.DataFrame(rows)
            for col in ['Montant', 'Montant_Signe', 'type_depense', 'type_transaction', 'departement']:
                if col in df.columns:
                    print(f"    ✓ {col}: {df[col].dtype}")
                    print(f"      - Sample values: {df[col].dropna().unique()[:3].tolist()}")
                else:
                    print(f"    ✗ {col}: MISSING")
        else:
            print(f"  - DATASET NOT VALID: {dataset.get('message')}")
            
    except Exception as e:
        print(f"✗ Failed to get active dataset: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Build KPIs
    try:
        if dataset.get('success') and dataset.get('rows'):
            rows = dataset.get('rows')
            print(f"\n--- KPI Calculation ---")
            kpis = _build_kpis_from_rows(rows)
            print(f"✓ Built {len(kpis)} KPIs")
            
            # Show key KPIs
            for kpi in kpis:
                if kpi.get('periode') == 'global' and kpi.get('kpiNom') in ['CA_Total', 'Revenus', 'Dépenses', 'Solde_Net']:
                    print(f"  - {kpi['kpiNom']}: {kpi['valeur']}")
            
            # Check for N/A handling
            dep_kpis = [k for k in kpis if k.get('kpiNom', '').startswith('Dep_')]
            print(f"  - Dep_* KPIs found: {len(dep_kpis)}")
            for kpi in dep_kpis[:10]:
                print(f"    {kpi['kpiNom']}: {kpi['valeur']}")
    except Exception as e:
        print(f"✗ Failed to build KPIs: {e}")
        import traceback
        traceback.print_exc()
    
    # Check transactions table
    try:
        tx_count = run_query("SELECT COUNT(*) AS n FROM transactions", fetch_one=True)
        print(f"\n--- Transactions Table ---")
        print(f"  - Row count: {tx_count.get('n')}")
    except Exception:
        print(f"  - transactions table not available")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    diagnose()
