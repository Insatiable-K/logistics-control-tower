#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate interactive HTML dashboard from invoice_compliance_v3_final.csv
"""

import pandas as pd
import json
from pathlib import Path

# Load compliance data
df = pd.read_csv('invoice_compliance_v3_final.csv')

# Convert NaN to None for JSON serialization
df = df.where(pd.notna(df), None)

# Convert to list of dicts
data = df.to_dict('records')

# Clean up data - replace NaN with empty string for display
for row in data:
    for key, val in row.items():
        if val is None or (isinstance(val, float) and pd.isna(val)):
            row[key] = ''

# Generate JSON
data_json = json.dumps(data, indent=2)

# Read template
template_path = Path('containers_reaching_port_final.html')
with open(template_path, 'r') as f:
    html = f.read()

# Replace placeholder
html = html.replace('DATA_PLACEHOLDER', data_json)

# Write output
output_path = Path('containers_reaching_port.html')
with open(output_path, 'w') as f:
    f.write(html)

print(f"[OK] Generated {output_path}")
print(f"  Total SIPLs: {len(data)}")
print(f"  Complete: {sum(1 for row in data if row['overall_status'] == 'Complete')}")
print(f"  Pending: {sum(1 for row in data if row['overall_status'] == 'Pending')}")
print(f"  Missing: {sum(1 for row in data if row['overall_status'] == 'Missing')}")
