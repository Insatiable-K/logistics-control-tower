#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Create merged Bills + GL view to understand Pending vs Missing logic
"""

import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_html_table, standardize_columns, clean_container, is_pending

# Load data
bills = standardize_columns(load_html_table(Path("Bills.xls")))
gl_1275 = standardize_columns(load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls")))
gl_1313 = standardize_columns(load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls")))

# Combine GL
gl_data = pd.concat([
    gl_1313.assign(gl_source='GL 1313'),
    gl_1275.assign(gl_source='GL 1275')
], ignore_index=True)

# Clean data
bills['_container_clean'] = bills['container'].apply(clean_container)
bills['_is_pending'] = is_pending(bills['bill_inv'])

# Normalize invoice numbers
bills['_bill_inv_normalized'] = bills['bill_inv'].astype(str).str.strip().str.upper()
gl_data['_gl_invoice_normalized'] = gl_data['invoice'].astype(str).str.strip().str.upper()

# Classify GL
def classify_gl_category(description):
    if pd.isna(description):
        return 'Other'
    desc = str(description).upper()
    if 'OCEAN FREIGHT' in desc or ('FREIGHT' in desc and 'OCEAN' in desc):
        return 'Ocean Freight'
    elif 'CUSTOM' in desc:
        return 'Customs'
    elif 'DUTY' in desc:
        return 'Duty'
    elif 'DRAYAGE' in desc or 'CARTAGE' in desc or 'LOCAL' in desc:
        return 'Drayage'
    else:
        return 'Other'

gl_data['_category'] = gl_data['description'].apply(classify_gl_category)

# Merge Bills with GL
merged = pd.merge(
    bills[['bill_inv', '_bill_inv_normalized', 'container', 'sipl_inv', '_container_clean', '_is_pending', 'supplier', 'amount', 'invoice_dt']],
    gl_data[['invoice', '_gl_invoice_normalized', 'date', 'description', '_category', 'debit', 'credit', 'gl_source']],
    left_on='_bill_inv_normalized',
    right_on='_gl_invoice_normalized',
    how='inner'
)

# Add status column
merged['status'] = merged.apply(
    lambda row: f"Pending {row['_category']}" if row['_is_pending'] else f"Complete {row['_category']}",
    axis=1
)

print(f"Total bills: {len(bills)}")
print(f"Total GL entries: {len(gl_data)}")
print(f"Merged (Bills + GL): {len(merged)}")

# Group by status to show examples
print("\nStatus Distribution:")
status_counts = merged['status'].value_counts()
for status, count in status_counts.head(20).items():
    print(f"  {status}: {count}")

# Create HTML
html = """<!DOCTYPE html>
<html>
<head>
    <title>Bills Merged with GL Data</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h2 { color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }
        .legend { display: flex; gap: 20px; margin: 15px 0; flex-wrap: wrap; }
        .legend-item { padding: 8px 12px; border-radius: 4px; font-weight: bold; }
        .pending { background-color: #fff3cd; color: #856404; }
        .complete { background-color: #d4edda; color: #155724; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th { background-color: #007bff; color: white; padding: 10px; text-align: left; font-weight: bold; }
        td { border: 1px solid #ddd; padding: 8px; }
        tr:hover { background-color: #f0f0f0; }
        .row-pending { background-color: #fff3cd; }
        .row-complete { background-color: #d4edda; }
        .info-box { background-color: #d1ecf1; border-left: 4px solid #0c5460; padding: 12px; margin: 15px 0; border-radius: 4px; }
    </style>
</head>
<body>

<h1>Bills Merged with GL Data</h1>

<div class="container">
    <div class="info-box">
        <h3>The Logic for Pending vs Complete:</h3>
        <p><strong>If:</strong> bill_inv contains "PEND" (e.g., "PENDING_DRAYAGE_123")</p>
        <p><strong>And:</strong> GL description contains category (DRAYAGE, CUSTOMS, DUTY, OCEAN FREIGHT)</p>
        <p><strong>Then:</strong> Status = "Pending [Category]" (Invoice Team entered placeholder, waiting for vendor)</p>
        <p><strong>Otherwise:</strong> Status = "Complete [Category]" (Invoice confirmed in GL)</p>
    </div>

    <div class="legend">
        <div class="legend-item pending">Pending (bill_inv has "PEND")</div>
        <div class="legend-item complete">Complete (No "PEND" in bill_inv)</div>
    </div>

    <h2>Merged Data: Bills + GL</h2>
    <table>
        <tr>
            <th>bill_inv</th>
            <th>GL Invoice</th>
            <th>Container</th>
            <th>SIPL</th>
            <th>GL Description</th>
            <th>Category</th>
            <th>Amount</th>
            <th>Status</th>
        </tr>
"""

# Show examples from each status
for status in sorted(merged['status'].unique())[:10]:
    status_data = merged[merged['status'] == status].head(5)

    for idx, row in status_data.iterrows():
        is_pending = 'Pending' in status
        row_class = 'row-pending' if is_pending else 'row-complete'

        html += f"""        <tr class="{row_class}">
            <td><strong>{row['bill_inv']}</strong></td>
            <td>{row['invoice']}</td>
            <td>{row['_container_clean']}</td>
            <td>{row['sipl_inv']}</td>
            <td>{str(row['description'])[:40]}</td>
            <td><strong>{row['_category']}</strong></td>
            <td>{row['amount']}</td>
            <td><strong>{status}</strong></td>
        </tr>
"""

html += """    </table>
</div>

<div class="container">
    <h2>Examples by Category</h2>
    <h3>Ocean Freight:</h3>
    <ul>
        <li><strong>Pending Ocean Freight:</strong> bill_inv = "PENDING_123..." + GL description = "OCEAN FREIGHT"</li>
        <li><strong>Complete Ocean Freight:</strong> bill_inv = "INV123..." + GL description = "OCEAN FREIGHT"</li>
    </ul>

    <h3>Customs:</h3>
    <ul>
        <li><strong>Pending Customs:</strong> bill_inv = "PENDING_..." + GL description = "CUSTOMS"</li>
        <li><strong>Complete Customs:</strong> bill_inv = "INV..." + GL description = "CUSTOMS"</li>
    </ul>

    <h3>Duty:</h3>
    <ul>
        <li><strong>Pending Duty:</strong> bill_inv = "PENDING_..." + GL description = "DUTY"</li>
        <li><strong>Complete Duty:</strong> bill_inv = "INV..." + GL description = "DUTY"</li>
    </ul>

    <h3>Drayage:</h3>
    <ul>
        <li><strong>Pending Drayage:</strong> bill_inv = "PENDING_..." + GL description = "DRAYAGE"</li>
        <li><strong>Complete Drayage:</strong> bill_inv = "INV..." + GL description = "DRAYAGE"</li>
    </ul>
</div>

<div class="container">
    <h2>Summary Statistics</h2>
    <table>
        <tr>
            <th>Status</th>
            <th>Count</th>
        </tr>
"""

for status, count in status_counts.head(20).items():
    html += f"        <tr><td>{status}</td><td>{count}</td></tr>\n"

html += """    </table>
</div>

<div class="container">
    <h2>Next Step: Understanding MISSING</h2>
    <div class="info-box">
        <p>Once we confirm the Pending logic is correct, we need to understand:</p>
        <ul>
            <li><strong>Missing Invoice:</strong> When SIPL/Container exists in shipments BUT no GL entry for a category</li>
            <li>Should we check Bills for "PEND" when there's no GL entry?</li>
            <li>If there's a bill but no GL → Is it Pending or Missing?</li>
        </ul>
    </div>
</div>

</body>
</html>
"""

# Save HTML
with open('bills_gl_merged.html', 'w') as f:
    f.write(html)

print("\n[OK] Created bills_gl_merged.html")
