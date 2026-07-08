# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Logistics Control Tower** is a Streamlit-based dashboard for tracking container shipments and invoice compliance. The system integrates with SQL Server for local deployment and provides a file-based cloud variant.

### Two Deployment Variants
- **Local** (`app.py`): Direct SQL Server connection
- **Cloud** (`app_cloud.py`): File-based (users upload Excel)

## Architecture

### Core Files

| File | Purpose | Type |
|------|---------|------|
| `app.py` | Local Streamlit dashboard (3 tabs) | App |
| `app_cloud.py` | Cloud Streamlit dashboard (file upload) | App |
| `logic.py` | Data layer for local (SQL Server) | Logic |
| `logic_cloud.py` | Data layer for cloud (file-based) | Logic |
| `build_dashboard_data.py` | Main ETL pipeline (orchestrates all data generation) | ETL |
| `build_dashboard_data_v3.py` | Invoice compliance calculation (called by build_dashboard_data.py) | ETL |
| `ETL_Clean_Load.py` | Legacy ETL (loads source Excel → SQL Server) | ETL |
| `utils.py` | Utility functions (Excel loading, data cleaning) | Utilities |

### Database
- **Local Only**: SQL Server (`localhost\SQLEXPRESS01`), database `logistics_db`
- **Cloud**: No database required (Excel-based)

## Data Pipeline

### How Data Gets Built (build_dashboard_data.py)
1. Runs `ETL_Clean_Load.py` (legacy ETL: Excel → SQL Server)
2. Runs `build_dashboard_data_v3.py` (invoice compliance calculation)
3. Exports SQL Server tables to Excel sheets
4. Loads invoice_compliance CSV and includes as Excel sheet
5. Creates `dashboard_data.xlsx` with all sheets

### How App Consumes Data (app_cloud.py)
1. User uploads `dashboard_data.xlsx`
2. App reads all sheets from Excel
3. `logic_cloud.py` processes sheets
4. Dashboard displays results

### File Flow
```
Source Excel Files
    ↓
ETL_Clean_Load.py
    ↓
SQL Server (logistics_db)
    ↓
build_dashboard_data.py ←→ build_dashboard_data_v3.py
    ↓
dashboard_data.xlsx
    ↓
app_cloud.py (Streamlit)
    ↓
Dashboard Display
```

## Invoice Compliance Business Logic

**Location**: `build_dashboard_data_v3.py` (310 lines, production-ready)

### What It Does
Answers: "For every container reaching port in 2-3 days or 7 days, which invoices are Complete/Pending/Missing?"

### Data Scope
**Inputs:**
- In-Transit List by SIPL (operational shipments)
- Bills.xls (invoice registry with bill_inv values)
- GL 1275 (Capitalized Inventory Freight)
- GL 1313 (Prepaid Container Freight)

**Outputs:**
- 84 SIPLs reaching port (within ETA window)
- Per-SIPL, per-category (Ocean Freight, Customs, Duty, Drayage) status
- Result: `invoice_compliance_v3_final.csv`

### 7-Step Pipeline
1. **Load Shipments**: Filter In-Transit to ISO 6346 containers (international only) → 234 valid
2. **Filter by ETA**: Port ETA 2-3 days OR Location ETA 7 days → 84 SIPLs in scope
3. **Match Bills**: Load Bills, match by container or SIPL → 85 bills matched
4. **Load GL**: Combine GL 1275 + 1313, filter to alphabetic descriptions only → 6,353 entries
5. **Classify GL**: Map descriptions to 4 categories (OF→Ocean Freight, CU→Customs, DU→Duty, DR→Drayage)
6. **Match GL to Bills**: Join on normalized invoice numbers → 114 GL entries matched
7. **Score Compliance**: For each SIPL, for each category, assign Complete/Pending/Missing

### Scoring Logic (CRITICAL)
For each SIPL, for each category:

1. **Complete** = GL entry exists
   - Example: GL has invoice='6143/26I A' with description='OF' (Ocean Freight)

2. **Pending** = NO GL entry BUT bill_inv='PENDING' exists
   - Example: Bills has PENDING marker, meaning invoice team is waiting for vendor

3. **Missing** = NO GL entry AND (NO PENDING marker AND no real bill)
   - Example: Container reaching tomorrow, no invoice received yet

**Key Insight**: PENDING marker indicates entry was made by invoice team. If it exists, category is Pending (not Missing).

### Example: SIPL 155933B
```
Container: SEGU3671280
Bills: 'PENDING' + '6143/26I A'
GL: invoice='6143/26I A' with desc='OF'

Scoring:
  Ocean Freight: Complete (GL entry exists)
  Customs: Pending (no GL, but PENDING marker exists)
  Duty: Pending (no GL, but PENDING marker exists)
  Drayage: Pending (no GL, but PENDING marker exists)

Overall: Pending
```

### GL Description Classification
Handles both abbreviations and full text:
- **Abbreviations**: OF/OI→Ocean Freight, CU/CA→Customs, DU→Duty, DR→Drayage
- **Full text**: OCEAN FREIGHT, CUSTOM, DUTY, DRAYAGE, DRYAGE, CARTAGE, LOCAL

## Current Results
- **84 SIPLs** analyzed (reaching port within ETA window)
- **Complete**: 4 (4.8%) — all invoices received
- **Pending**: 28 (33.3%) — awaiting vendor invoices
- **Missing**: 52 (61.9%) — no invoices yet

## Running the System

### Generate Dashboard Data
```bash
python build_dashboard_data.py
```
Output: `dashboard_data.xlsx` (with invoice_compliance sheet)

### Run Local App (SQL Server)
```bash
streamlit run app.py
```

### Run Cloud App (File-based)
```bash
streamlit run app_cloud.py
```
Then upload `dashboard_data.xlsx` in sidebar

## Key Files to Know

### Main Entry Points
- `build_dashboard_data.py` — Run this to build all data
- `app_cloud.py` — Run this to display dashboard (cloud variant)

### Logic/Calculations
- `logic_cloud.py` — Core dashboard calculations
- `build_dashboard_data_v3.py` — Invoice compliance calculations (called by build_dashboard_data.py)

### Data Utilities
- `utils.py` — Excel loading, column standardization, date parsing, container cleaning

## Important Notes

1. **No Manual CSV Handling**: The CSV is generated inside `build_dashboard_data.py` automatically
2. **One Excel File**: User only needs to upload `dashboard_data.xlsx` to the app
3. **No Database Required for Cloud**: File-based approach eliminates SQL Server dependency
4. **Backward Compatible**: Legacy data (bookings, open_po) still exported for other dashboards

## Common Tasks

### Update Invoice Compliance Logic
Edit: `build_dashboard_data_v3.py` (lines 200-280 contain scoring logic)

### Add New Dashboard Tab
Edit: `app_cloud.py` → `logic_cloud.py` → add calculation function

### Change ETA Window
Edit: `build_dashboard_data_v3.py` line 60-68 (currently 2-3 days port, 7 days location)

## Troubleshooting

**App shows "Please upload dashboard_data.xlsx"**
→ Run `python build_dashboard_data.py` first

**Invoice compliance sheet missing**
→ Check `build_dashboard_data_v3.py` ran successfully (check stdout for errors)

**ETL fails on Excel load**
→ Check file path in `utils.py` `load_html_table()` function
