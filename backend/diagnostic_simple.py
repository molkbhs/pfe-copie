#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
    print("="*80)
    print("[DIAGNOSTIC] ETL, KPI Calculations, Dataset Selection")
    print("="*80)
    
    # Test DB connection
    try:
        result = run_query("SELECT 1")
        print("[OK] Database connection OK")
    except Exception as e:
        print(f"[ERROR] Database connection FAILED: {e}")
        return
    
    # Get all users
    try:
        users = run_query("SELECT id, email FROM users LIMIT 5", fetch_one=False) or []
        print(f"[OK] Found {len(users)} users in database")
    except Exception as e:
        print(f"[ERROR] Failed to query users: {e}")
        return
    
    if not users:
        print("[ERROR] No users found!")
        return
    
    # Test with first user
    user_id = users[0].get("id")
    user_email = users[0].get("email")
    print(f"\n--- Testing with user: {user_email} (ID={user_id}) ---")
    
    # Get active dataset (this is what analytics uses)
    try:
        dataset = get_active_user_dataset(user_id)
        print(f"\n--- Active Dataset for Analytics ---")
        print(f"  - Success: {dataset.get('success')}")
        print(f"  - Source: {dataset.get('source')}")
        print(f"  - Rows count: {len(dataset.get('rows') or [])}")
        
        if dataset.get('success') and dataset.get('rows'):
            rows = dataset.get('rows')
            print(f"\n  - Sample row:")
            if rows:
                print(f"    {json.dumps(rows[0], indent=6, default=str)}")
            
            # Build KPIs
            print(f"\n--- KPI Calculation ---")
            kpis = _build_kpis_from_rows(rows)
            print(f"[OK] Built {len(kpis)} KPIs")
            
            # Show key KPIs
            for kpi in kpis:
                if kpi.get('periode') == 'global' and kpi.get('kpiNom') in ['CA_Total', 'Revenus', 'Depenses', 'Solde_Net']:
                    print(f"  - {kpi['kpiNom']}: {kpi['valeur']}")
            
            # Check for Dep_* handling
            dep_kpis = [k for k in kpis if k.get('kpiNom', '').startswith('Dep_')]
            print(f"  - Dep_* KPIs found: {len(dep_kpis)}")
            for kpi in dep_kpis[:15]:
                print(f"    {kpi['kpiNom']}: {kpi['valeur']}")
    except Exception as e:
        print(f"[ERROR] Failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*80)

if __name__ == "__main__":
    diagnose()
