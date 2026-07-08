import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path('.')))
from utils import load_html_table, standardize_columns, clean_container, is_pending, clean_date
from datetime import datetime, timedelta

# Load data
bills = standardize_columns(load_html_table(Path("Bills.xls")))
in_transit = standardize_columns(load_html_table(Path("In-Transit List by SIPL.xls")))

bills['_container_clean'] = bills['container'].apply(clean_container)
bills['_is_pending'] = is_pending(bills['bill_inv'])
in_transit['_container_clean'] = in_transit['container'].apply(clean_container)

# Filter in_transit to ETA window
in_transit_intl = in_transit[in_transit['_container_clean'].notna()].copy()
today = pd.Timestamp.today().normalize()
in_transit_intl['_port_eta'] = clean_date(in_transit_intl['port_eta'])
in_transit_intl['_location_eta'] = clean_date(in_transit_intl['location_eta'])

port_eta_filter = (
    (in_transit_intl['_port_eta'] <= today + timedelta(days=4)) &
    (in_transit_intl['_port_eta'].notna())
)
location_eta_filter = (
    (in_transit_intl['_location_eta'] <= today + timedelta(days=7)) &
    (in_transit_intl['_location_eta'].notna())
)

shipment_master = in_transit_intl[port_eta_filter | location_eta_filter].copy()
shipment_sipls = set(shipment_master['sipl'].unique())
shipment_containers = set(shipment_master['_container_clean'].unique())

# Match bills
bills_valid = bills[bills['_container_clean'].notna()].copy()
bills_matched = bills_valid[
    (bills_valid['_container_clean'].isin(shipment_containers)) |
    (bills_valid['sipl_inv'].astype(str).str.strip().isin(shipment_sipls))
].copy()

print("SIPL 155933B analysis:")
print(f"  Type: {type(shipment_master[shipment_master['sipl'] == '155933B']['sipl'].iloc[0])}")
print(f"  In shipment_sipls: {'155933B' in shipment_sipls}")

bills_for_155933b = bills_matched[bills_matched['sipl_inv'].astype(str).str.strip() == '155933B']
print(f"  Bills with SIPL 155933B: {len(bills_for_155933b)}")
if len(bills_for_155933b) > 0:
    print(bills_for_155933b[['bill_inv', 'container', 'sipl_inv', '_is_pending']])
    print(f"  Has PENDING: {bills_for_155933b['_is_pending'].any()}")

# Check for type issues
print(f"\nBills SIPLs sample (raw types):")
print(bills_matched['sipl_inv'].head(10).tolist())
print(f"  Shipment SIPLs sample: {list(shipment_sipls)[:5]}")

# Check numeric vs string
print(f"\nAre SIPLs numeric or string?")
print(f"  Bill sipl_inv type: {bills_matched['sipl_inv'].dtype}")
print(f"  Shipment sipl type: {shipment_master['sipl'].dtype}")
