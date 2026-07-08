# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Logistics Control Tower** is a Streamlit-based dashboard for tracking container shipments, bookings, and invoice compliance across logistics operations. The system integrates with SQL Server for data persistence and processes multiple Excel-based data sources.

Two deployment variants exist:
- **Local** (`app.py` + `logic.py`): Direct SQL Server connection via pyodbc
- **Cloud** (`app_cloud.py` + `logic_cloud.py`): File-based (uploads Excel exports)

## Architecture & Data Flow

### Core Modules

| File | Purpose | Deployment |
|------|---------|-----------|
| `app.py` | Main Streamlit dashboard UI; 3 tabs: Booking, Container Movement, Invoice Compliance | Local (SQL Server) |
| `app_cloud.py` | Streamlit dashboard that reads from uploaded Excel files | Cloud (file-based) |
| `logic.py` | Data loading and transformation; interfaces with SQL Server via SQLAlchemy | Local |
| `logic_cloud.py` | Pure data transformation; no DB dependency | Cloud |
| `ETL_Clean_Load.py` | Ingests source Excel files, cleans data, loads into `logistics_db` SQL Server | Scheduled (Windows Task Scheduler) |
| `generate_dashboard_data.py` | Runs ETL, then exports SQL data to `dashboard_data.xlsx` for cloud deployment | Manual or scheduled |
| `utils.py`, `Invoice_working.py`, `build_dashboard_data.py`, `clean_excel_sheets.py` | Supporting utilities | ETL/export pipeline |

### Database

- **Engine**: SQL Server (`localhost\SQLEXPRESS01`)
- **Database**: `logistics_db`
- **Key Tables**: `bookings`, `shipment_mapping`, `open_po`, `in_transit`, `inventory_intransit`, `supplier_invoices`, `bills`, `qc_snapshot`
- **Connection**: pyodbc with SQLAlchemy ORM; `pool_pre_ping=True` to verify connection before use

### Data Sources

All source files in `C:\Users\Abhay\Architectural Surfaces\Mohan - Logistics\Logistics Tracker\`:
- `Freight Bills Processed - 2026.xlsm` (team-managed master)
- `Bookings Tracker.xlsx`
- Various SPS export files (HTML/XLS format)

These are cleaned and normalized in ETL, then loaded into SQL Server tables.

## Development Setup

### Prerequisites

- Python 3.13 (see `.venv\pyvenv.cfg`)
- Windows environment (hardcoded paths use Windows conventions)
- SQL Server with ODBC drivers (17 or 18 for SQL Server)
- Local SQL Server instance running with `logistics_db` created

### Environment

**Virtual Environment:**
```powershell
.venv\Scripts\Activate.ps1
```

**Dependencies:**
```bash
pip install -r requirements.txt
```

Key packages:
- `streamlit` — UI framework
- `pandas`, `numpy` — Data manipulation
- `openpyxl` — Excel I/O
- `sqlalchemy`, `pyodbc` — SQL Server connection
- `altair` — Charting
- `python-dateutil`, `rapidfuzz` — Utilities

## Common Commands

### Run Locally (SQL Server)
```bash
streamlit run app.py
```
App opens on `http://localhost:8501` and reads from `logistics_db`.

### Run Cloud Version (File-based)
```bash
streamlit run app_cloud.py
```
Prompts user to upload `dashboard_data.xlsx`.

### Run ETL Pipeline
```bash
python ETL_Clean_Load.py
```
Reads source Excel files, validates, cleans, and loads into SQL Server. Records issues in `qc_snapshot` table for audit trail.

### Generate Dashboard Export
```bash
python generate_dashboard_data.py
```
Runs ETL (above), then exports SQL data to:
- Dated: `...\Dashboards\Container Movement Control Tower\dashboard_data_YYYY-MM-DD.xlsx`
- Latest: `...\Dashboards\Container Movement Control Tower\dashboard_data.xlsx`

(These paths are hardcoded in the script; adjust if needed.)

## Key Logic Patterns

### Data Loading & Normalization

In `logic.py`, all load functions normalize data consistently:
- Column names: lowercase, stripped whitespace
- Container IDs: uppercase, stripped
- PO numbers: numeric (Int64 type to handle NaN)
- Dates: parsed to datetime
- Status fields: uppercase

Example from `load_bookings()`:
```python
df["po_number"] = pd.to_numeric(df["po_number"], errors="coerce").astype("Int64")
df["event_status"] = df["event_status"].astype(str).str.strip().str.upper()
```

### Mapping & Merge

`shipment_mapping` table links PO numbers to container IDs. When merging with `bookings`, use suffixes to avoid collisions, then coalesce missing values:
```python
df = df.merge(mapping[["po_number", "container_id"]], 
              on="po_number", how="left", suffixes=("", "_map"))
df["container_id"] = df["container_id"].combine_first(df["container_id_map"])
```

### Streamlit Dashboard Structure

- `st.set_page_config()` must be the first Streamlit call
- Use tabs to organize domains (Booking, Container Movement, Invoice Compliance)
- Import all compute functions upfront (`from logic import ...`)
- Load data once per page refresh using `@st.cache_resource` where appropriate

## Testing & Verification

- No automated test suite; verify manually in Streamlit UI
- Watch for data mismatches between SQL and Excel (addressed by `qc_snapshot` audit trail)
- ETL errors are logged to stdout; check before running export

## File Structure (Project Root)

```
.
├── app.py                           # Local dashboard
├── app_cloud.py                     # Cloud dashboard
├── logic.py                         # Local data logic (SQL Server)
├── logic_cloud.py                   # Cloud data logic (Excel-based)
├── ETL_Clean_Load.py                # ETL pipeline
├── generate_dashboard_data.py       # Export dashboard data
├── requirements.txt                 # Python dependencies
├── utils.py                         # Helper functions
├── Invoice_working.py               # Invoice-specific logic
├── build_dashboard_data.py          # Dashboard export utilities
├── clean_excel_sheets.py            # Excel cleanup
└── [Excel files, .spyproject/, __pycache__, .venv/, etc.]
```

## Deployment

**Local:**
- Requires SQL Server and ODBC driver
- Schedule ETL via Windows Task Scheduler
- Run Streamlit manually or via scheduler

**Cloud:**
- No SQL Server required; operates on uploaded Excel
- User uploads `dashboard_data.xlsx` in sidebar
- `generate_dashboard_data.py` produces this file (run before deploying)

## Common Gotchas

1. **ODBC Driver**: Script tries ODBC 17 first, falls back to 18. If neither is installed, `get_engine()` will fail. Verify drivers on the machine.

2. **File Paths**: Many paths are hardcoded (e.g., `TEAM_FILE_PATH` in ETL). Update if source or output locations change.

3. **Encoding**: HTML/XLS parsing uses `encoding="utf-8"` and `recover=True` to handle malformed sources. If a new source format breaks, check parser settings in ETL.

4. **Data Freshness**: Dashboard is as fresh as the last ETL run. If data seems stale, check the SQL Server `qc_snapshot` table for ETL errors.

5. **Normalization Assumptions**: All load functions expect specific column names. If a source adds or renames columns, ETL may fail silently (check QC snapshot).

## Useful SQL Queries

```sql
-- Check latest QC run
SELECT TOP 10 * FROM qc_snapshot ORDER BY run_date DESC;

-- Verify bookings loaded
SELECT COUNT(*) FROM bookings;

-- Find container-PO mismatches
SELECT * FROM shipment_mapping WHERE container_id IS NULL;
```

## Future Enhancements

- Add automated tests for ETL transformations
- Implement error notifications (email/Slack) when ETL fails
- Cache expensive dataframe operations in Streamlit
- Consider moving hardcoded paths to a config file
