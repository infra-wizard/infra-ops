"""
convert_xlsx_to_csv.py
----------------------
Run this LOCALLY (not in pod) to convert your xlsx to a CSV
that is safe to store in a Kubernetes ConfigMap.

Usage:
    python convert_xlsx_to_csv.py certs.xlsx

Output:
    certs.csv  (all sheets merged, with a _sheet column)

Then create the ConfigMap:
    kubectl create configmap cert-data-file --from-file=certs.csv --dry-run=client -o yaml | kubectl apply -f -
    kubectl rollout restart deployment/cert-expiry-monitor
"""

import sys
import pandas as pd
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python convert_xlsx_to_csv.py <file.xlsx>")
    sys.exit(1)

src = Path(sys.argv[1])
out = src.with_suffix(".csv")

sheets = pd.read_excel(src, sheet_name=None, dtype=str, engine="openpyxl")
frames = []
for name, df in sheets.items():
    df["_sheet"] = name
    frames.append(df)

combined = pd.concat(frames, ignore_index=True)
combined.to_csv(out, index=False)

size_kb = out.stat().st_size / 1024
print(f"✅ Saved {len(combined)} rows → {out}  ({size_kb:.1f} KB)")
print()
print("Now run:")
print(f"  kubectl create configmap cert-data-file --from-file={out} --dry-run=client -o yaml | kubectl apply -f -")
print(f"  kubectl rollout restart deployment/cert-expiry-monitor")
