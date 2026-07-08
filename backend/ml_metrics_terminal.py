#!/usr/bin/env python3
"""
Terminal ML evaluation for financial series.

This script does not change the Flask API/backend routes.
It loads a CSV/XLSX file, applies the same ETL cleaning helpers, trains a
LinearRegression trend model, and prints metrics in terminal:
MAE, RMSE, R2, MAPE, direction accuracy, precision, recall, F1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)


def _load_with_etl_helpers(file_path: Path):
    # Local import to avoid touching Flask runtime behavior.
    from etl_generic import _clean_dataframe, _read_file

    log = []
    raw_df = _read_file(file_path, log)
    clean_df, clean_log, clean_report, _ = _clean_dataframe(raw_df)
    log.extend(clean_log)
    return clean_df, log, clean_report


def _build_monthly_series(df: pd.DataFrame, view: str) -> pd.DataFrame:
    work = df.copy()
    if "YearMonth" not in work.columns:
        raise ValueError("Column 'YearMonth' is missing after cleaning.")

    if view == "expenses":
        work = work[work["Montant_Signe"] < 0].copy()
        work["value"] = work["Montant_Signe"].abs()
    elif view == "revenues":
        work = work[work["Montant_Signe"] > 0].copy()
        work["value"] = work["Montant_Signe"]
    else:
        work["value"] = work["Montant_Signe"]

    grouped = work.groupby("YearMonth", as_index=False)["value"].sum()
    grouped["date"] = pd.to_datetime(grouped["YearMonth"].astype(str) + "-01", errors="coerce")
    grouped = grouped.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return grouped[["date", "value"]]


def _evaluate_series(series_df: pd.DataFrame) -> dict:
    if len(series_df) < 6:
        raise ValueError(
            "Not enough history. At least 6 monthly points are required "
            "to compute robust walk-forward metrics."
        )

    series_df = series_df.copy()
    series_df["value"] = pd.to_numeric(series_df["value"], errors="coerce").fillna(0.0)

    X = np.arange(len(series_df)).reshape(-1, 1)
    y = series_df["value"].values

    min_train_points = max(4, int(np.ceil(len(series_df) * 0.6)))
    if len(series_df) < min_train_points + 2:
        raise ValueError("Not enough history to evaluate with walk-forward validation.")

    wf_true = []
    wf_pred = []
    wf_prev = []
    out_rows = []

    for split in range(min_train_points, len(series_df)):
        train_X, test_X = X[:split], X[split : split + 1]
        train_y = y[:split]
        prev_y = float(y[split - 1])
        true_y = float(y[split])

        model = LinearRegression()
        model.fit(train_X, train_y)
        pred_y = float(model.predict(test_X)[0])

        wf_true.append(true_y)
        wf_pred.append(pred_y)
        wf_prev.append(prev_y)

        out_rows.append(
            {
                "periode": series_df["date"].iloc[split].strftime("%Y-%m"),
                "precedent": round(prev_y, 2),
                "reel": round(true_y, 2),
                "predit": round(pred_y, 2),
                "ecart_abs": round(abs(true_y - pred_y), 2),
                "direction_reelle": "hausse" if (true_y - prev_y) >= 0 else "baisse",
                "direction_predite": "hausse" if (pred_y - prev_y) >= 0 else "baisse",
            }
        )

    arr_true = np.array(wf_true, dtype=float)
    arr_pred = np.array(wf_pred, dtype=float)
    arr_prev = np.array(wf_prev, dtype=float)

    mae = float(mean_absolute_error(arr_true, arr_pred))
    rmse = float(np.sqrt(mean_squared_error(arr_true, arr_pred)))
    r2 = float(r2_score(arr_true, arr_pred)) if len(arr_true) >= 2 else float("nan")
    denom = np.where(np.abs(arr_true) < 1e-9, 1.0, np.abs(arr_true))
    mape = float(np.mean(np.abs((arr_true - arr_pred) / denom)) * 100.0)

    # Directional binary view:
    # class 1 = increase vs previous month, class 0 = decrease/stable.
    y_true_cls = ((arr_true - arr_prev) >= 0).astype(int)
    y_pred_cls = ((arr_pred - arr_prev) >= 0).astype(int)
    direction_acc = float(np.mean((y_true_cls == y_pred_cls).astype(float)) * 100.0)
    precision = float(precision_score(y_true_cls, y_pred_cls, zero_division=0))
    recall = float(recall_score(y_true_cls, y_pred_cls, zero_division=0))
    f1 = float(f1_score(y_true_cls, y_pred_cls, zero_division=0))

    return {
        "train_points": int(min_train_points),
        "test_points": int(len(arr_true)),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4) if not np.isnan(r2) else None,
        "mape": round(mape, 4),
        "direction_acc": round(direction_acc, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "rows": out_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate financial trend model and print metrics in terminal."
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to input file (.csv, .xlsx, .xls).",
    )
    parser.add_argument(
        "--view",
        choices=["global", "expenses", "revenues"],
        default="global",
        help="Series to evaluate (default: global).",
    )
    parser.add_argument(
        "--show-log",
        action="store_true",
        help="Print ETL cleaning logs.",
    )
    args = parser.parse_args()

    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        print(f"[ERROR] File not found: {file_path}")
        return 1

    try:
        clean_df, log, clean_report = _load_with_etl_helpers(file_path)
        series_df = _build_monthly_series(clean_df, args.view)
        result = _evaluate_series(series_df)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1

    print("\n=== ML TERMINAL EVALUATION ===")
    print(f"File          : {file_path.name}")
    print(f"View          : {args.view}")
    print(f"Rows input    : {clean_report.get('input_rows', 0)}")
    print(f"Rows cleaned  : {clean_report.get('rows_after_cleaning', 0)}")
    print(f"Train/Test    : {result['train_points']} / {result['test_points']} (walk-forward)")
    print()
    print(f"MAE      : {result['mae']}")
    print(f"RMSE     : {result['rmse']}")
    print(f"R2       : {result['r2']}")
    print(f"MAPE     : {result['mape']}%")
    print(f"Dir. Acc.: {result['direction_acc']}%")
    print(f"Precision: {result['precision']}")
    print(f"Recall   : {result['recall']}")
    print(f"F1-score : {result['f1']}")

    print("\nHoldout details:")
    for row in result["rows"]:
        print(
            f"  {row['periode']} | prev={row['precedent']} | reel={row['reel']} | "
            f"predit={row['predit']} | ecart={row['ecart_abs']} | "
            f"dir={row['direction_reelle']} dir_hat={row['direction_predite']}"
        )

    if args.show_log:
        print("\nETL log:")
        for line in log:
            print(f"  - {line}")

    print("\nNote: precision/recall/f1 are computed from direction classes:")
    print("      class 1 = hausse vs mois precedent, class 0 = baisse/stable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
