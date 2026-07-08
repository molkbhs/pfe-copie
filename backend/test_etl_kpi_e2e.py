#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Complete end-to-end test of ETL -> KPI calculations"""

import json
import pandas as pd
from app import (
    run_generic_etl, 
    save_import_history, 
    decode_import_rows,
    normalize_import_row,
    get_active_user_dataset,
    get_last_successful_import,
    _build_kpis_from_rows,
    run_query,
)
from pathlib import Path

print("="*80)
print("[TEST] End-to-End ETL -> KPI Pipeline")
print("="*80)

# Step 1: Create a test CSV with known data
test_csv_content = """Date,Departement,Type_Transaction,Type_Depense,Montant,Responsable,Client_Fournisseur,Projet
2026-01-01,IT,Revenu,N/A,1000.00,Sam,ClientA,Proj1
2026-01-02,HR,Depense,Formation,200.00,Sam,Trainer1,Proj2
2026-01-03,HR,Depense,N/A,150.00,Sam,Trainer2,Proj2
2026-01-04,Sales,Revenu,N/A,2000.00,John,ClientB,Proj3
2026-01-05,Sales,Depense,Transport,300.00,John,CarRental,Proj3
"""

test_file = Path("test_input.csv")
test_file.write_text(test_csv_content)
print(f"[STEP 1] Created test CSV: {test_file.name}")
print(f"  Rows: 5 (2 revenues, 3 depenses)")

# Step 2: Run ETL
print(f"\n[STEP 2] Running ETL on {test_file.name}...")
result = run_generic_etl(str(test_file), replace_existing=True)

if not result.get('success'):
    print(f"[ERROR] ETL failed: {result.get('error')}")
    exit(1)

after_rows = result.get('after_rows', [])
print(f"  ETL Success: True")
print(f"  Rows processed: {len(after_rows)}")
if after_rows:
    print(f"  Sample after_row (row 0):")
    print(f"    Keys: {list(after_rows[0].keys())}")
    print(f"    Data: {json.dumps(after_rows[0], indent=6, default=str)}")

# Step 3: Normalize rows
print(f"\n[STEP 3] Normalizing rows for analytics...")
normalized = []
for i, row in enumerate(after_rows, start=1):
    try:
        norm_row = normalize_import_row(row, idx=i)
        normalized.append(norm_row)
        print(f"  Row {i} normalized: dept={norm_row.get('departement')}, tx={norm_row.get('type_transaction')}, amount={norm_row.get('Montant_Signe')}")
    except Exception as e:
        print(f"  Row {i} FAILED: {e}")

# Step 4: Check for N/A handling
print(f"\n[STEP 4] Checking N/A handling...")
n_a_rows = [r for r in normalized if r.get('type_depense') == 'Non classée (N/A)']
print(f"  Rows with type_depense='Non classée (N/A)': {len(n_a_rows)}")
for r in n_a_rows:
    print(f"    - {r.get('departement')} | {r.get('type_transaction')} | Montant_Signe={r.get('Montant_Signe')}")

# Step 5: Build KPIs
print(f"\n[STEP 5] Building KPIs from normalized rows...")
kpis = _build_kpis_from_rows(normalized)
print(f"  Total KPIs: {len(kpis)}")

# Show global KPIs
print(f"\n  Global KPIs:")
for kpi in kpis:
    if kpi.get('periode') == 'global':
        print(f"    {kpi['kpiNom']}: {kpi['valeur']}")

# Show Dep_* KPIs
dep_kpis = [k for k in kpis if k.get('kpiNom', '').startswith('Dep_')]
print(f"\n  Expense Type KPIs ({len(dep_kpis)} found):")
for kpi in dep_kpis:
    print(f"    {kpi['kpiNom']}: {kpi['valeur']}")

# Step 6: Manual validation
print(f"\n[STEP 6] Manual Validation:")
print(f"  Expected revenues: 1000 + 2000 = 3000")
print(f"  Expected expenses: 200 + 150 + 300 = 650")
print(f"  Expected net: 3000 - 650 = 2350")
print(f"  Calculated CA_Total: {[k for k in kpis if k['kpiNom']=='CA_Total'][0]['valeur']}")
print(f"  Calculated Revenus: {[k for k in kpis if k['kpiNom']=='Revenus'][0]['valeur']}")
print(f"  Calculated Depenses: {[k for k in kpis if k['kpiNom']=='Dépenses'][0]['valeur']}")
print(f"  Calculated Solde_Net: {[k for k in kpis if k['kpiNom']=='Solde_Net'][0]['valeur']}")

# Verify
rev_kpi = [k for k in kpis if k['kpiNom']=='Revenus'][0]['valeur']
dep_kpi = [k for k in kpis if k['kpiNom']=='Dépenses'][0]['valeur']
solde_kpi = [k for k in kpis if k['kpiNom']=='Solde_Net'][0]['valeur']

if rev_kpi == 3000 and dep_kpi == 650 and solde_kpi == 2350:
    print(f"\n[PASS] KPI calculations are CORRECT!")
else:
    print(f"\n[FAIL] KPI calculations are WRONG!")
    print(f"  Expected: Revenus=3000, Depenses=650, Solde=2350")
    print(f"  Got: Revenus={rev_kpi}, Depenses={dep_kpi}, Solde={solde_kpi}")

# Cleanup
test_file.unlink()
print(f"\n[DONE] Test completed.")
print("="*80)
