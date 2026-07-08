import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.')))
from utils import load_html_table, standardize_columns

gl_1275 = standardize_columns(load_html_table(Path('Account Register_ 1275 - Capitalized Inventory Freight.xls')))
gl_1313 = standardize_columns(load_html_table(Path('Account Register_ 1313 - Prepaid Container Freight.xls')))

gl_data = pd.concat([gl_1275, gl_1313], ignore_index=True)

# Filter to alphabetic only
def has_valid_description(description):
    if pd.isna(description):
        return False
    desc = str(description).strip()
    if not desc:
        return False
    has_alpha = any(c.isalpha() for c in desc)
    has_digit = any(c.isdigit() for c in desc)
    return has_alpha and not has_digit

gl_data['_has_valid_desc'] = gl_data['description'].apply(has_valid_description)
gl_filtered = gl_data[gl_data['_has_valid_desc']].copy()

# Show unique descriptions
print('Unique GL descriptions (alphabetic only):')
unique_descs = sorted(gl_filtered['description'].unique())
for i, desc in enumerate(unique_descs):
    print(f'{i:3d}: {repr(desc)}')