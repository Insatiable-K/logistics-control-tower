import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path('.')))
from utils import load_html_table, standardize_columns, clean_container, is_pending

# Load data
bills = standardize_columns(load_html_table(Path("Bills.xls")))
gl_1275 = standardize_columns(load_html_table(Path("Account Register_ 1275 - Capitalized Inventory Freight.xls")))
gl_1313 = standardize_columns(load_html_table(Path("Account Register_ 1313 - Prepaid Container Freight.xls")))

bills['_container_clean'] = bills['container'].apply(clean_container)
bills['_is_pending'] = is_pending(bills['bill_inv'])

gl_data = pd.concat([
    gl_1313.assign(_gl_source='GL 1313'),
    gl_1275.assign(_gl_source='GL 1275')
], ignore_index=True)

# Process GL
def has_valid_description(description):
    if pd.isna(description):
        return False
    desc = str(description).strip()
    if not desc:
        return False
    has_alpha = any(c.isalpha() for c in desc)
    has_digit = any(c.isdigit() for c in desc)
    return has_alpha and not has_digit

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

# Filter GL
gl_data_no_pending_inv = gl_data[
    ~(gl_data['invoice'].astype(str).str.strip().str.upper().isin(['PENDING', 'PEND', '']))
].copy()

gl_data_no_pending_inv['_has_valid_desc'] = gl_data_no_pending_inv['description'].apply(has_valid_description)
gl_filtered = gl_data_no_pending_inv[gl_data_no_pending_inv['_has_valid_desc']].copy()

gl_filtered['_category'] = gl_filtered['description'].apply(classify_gl_category)

# Normalize invoice numbers
bills['_invoice_normalized'] = bills['bill_inv'].astype(str).str.strip().str.upper()
gl_filtered['_invoice_normalized'] = gl_filtered['invoice'].astype(str).str.strip().str.upper()

# Create lookups
bill_to_container = dict(zip(bills['_invoice_normalized'], bills['_container_clean']))
bill_to_sipl = dict(zip(bills['_invoice_normalized'], bills['sipl_inv'].astype(str).str.strip()))

# Map GL
gl_filtered['_gl_container'] = gl_filtered['_invoice_normalized'].map(bill_to_container)
gl_filtered['_gl_sipl'] = gl_filtered['_invoice_normalized'].map(bill_to_sipl)

gl_matched = gl_filtered[gl_filtered['_gl_container'].notna()].copy()

# Check SIPL 155933B
print("SIPL 155933B analysis:")
print(f"\nBills for 155933B:")
bills_155933b = bills[bills['sipl_inv'].astype(str).str.strip() == '155933B']
print(bills_155933b[['bill_inv', 'container', '_invoice_normalized', '_is_pending']])

print(f"\nGL entries matched for 155933B:")
gl_155933b = gl_matched[gl_matched['_gl_sipl'] == '155933B']
print(gl_155933b[['invoice', '_invoice_normalized', 'description', '_category', '_gl_container', '_gl_sipl']])

print(f"\nGL entries in general for invoices '6143/26I A' and 'PENDING':")
gl_for_invoice = gl_filtered[gl_filtered['_invoice_normalized'].isin(['6143/26I A', 'PENDING', '6143/26I B'])]
print(gl_for_invoice[['invoice', '_invoice_normalized', 'description', '_category', '_has_valid_desc']])

print(f"\nContainer SEGU3671280 in lookup: {bill_to_container}")
