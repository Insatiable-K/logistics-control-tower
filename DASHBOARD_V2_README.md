# Logistics Control Tower V2 — Financial Risk Dashboard

## Quick Start (Local Testing)

### Prerequisites
✓ dashboard_data.xlsx must exist (run `python build_dashboard_data.py` first)  
✓ Python 3.13 + Streamlit installed

### Launch V2 Dashboard
```bash
streamlit run app_dashboard_v2.py
```

Browser opens to: http://localhost:8501

### Upload Data
1. Click sidebar "Upload dashboard_data.xlsx"
2. Select file from `/dashboard_data.xlsx`
3. Dashboard loads automatically

---

## What You'll See

### Homepage: 🎯 ACTION QUEUE
**The single most important view** — shows top items needing action TODAY.

**Summary Cards:**
- Demurrage Risk: # of containers at risk
- Missing Invoices: # of containers missing invoices  
- Container Issues: # on HOLD/EXAM/DAMAGED
- Total Financial Exposure: $XXX,XXX

**Action Table:**
- SIPL, Container, Supplier
- Issue Type (Demurrage, Missing Invoice, Container Status)
- Urgency (🔴 RED, 🟠 ORANGE)
- Financial Impact ($)
- Details (what's wrong)
- Action (what to do)

**Sorted by:** Financial impact (highest first)

---

### Tab 2: 💰 Demurrage/LFD Risk Cockpit
**Deep dive** on containers approaching or past Last Free Day.

**KPIs:**
- Overdue at Port: # containers
- Approaching LFD: # days away
- Total Exposure: $XXX,XXX (at $150/day estimate)

**Detailed Table:**
- SIPL, Container, Supplier
- Days to/past ETA
- Days Exposed (demurrage clock)
- Estimated Exposure ($)
- Hold Reason (from status field)
- Current Status

**Chart:** Bar chart of top 10 containers by exposure

**Note:** $150/day is a PLACEHOLDER rate for estimation only, not actual contracted rates

---

### Tab 3: 📋 Missing Invoices
**Financial view** of missing invoice risk.

**KPIs:**
- SIPLs with Missing Invoices
- Total Unaccrued Cost ($2,000/category × # missing)
- Average per SIPL

**Detailed Table:**
- SIPL, Container, Supplier
- Port ETA
- Missing Categories (OF, Customs, Duty, Drayage)
- Unaccrued Cost ($)

**Chart:** Top 15 SIPLs by unaccrued cost

**Note:** $2,000/category is a PLACEHOLDER average from GL history, not an actual invoice

---

### Tab 4: 🔍 Container Detail
**Single-shipment drill-down** — see full context without tab-switching.

**Search Options:**
- By SIPL: Enter SIPL ID (e.g., 155933B)
- By Container: Enter container ID (e.g., SEGU3671280)

**Shows:**
- Invoice Compliance Status (Complete/Pending/Missing per category)
- Transit Status (ETA, location, status)

---

## Key Differences from App Cloud (V1)

| Aspect | V1 (Current) | V2 (New) |
|--------|----------|---------|
| **Entry Point** | KPI tiles | Action Queue |
| **Framing** | "What's missing?" | "What's costing us?" |
| **Focus** | Compliance checklist | Financial + Operational Risk |
| **Primary View** | Tables (Booking, Container, Invoice) | Unified Exception List |
| **Ranking** | By date | By financial impact × urgency |
| **Missing Invoices** | Status = "Missing" | Quantified as "$X unaccrued" |
| **Demurrage** | Read-only table | Quantified exposure + ranking |

---

## Data Assumptions & Estimates

**All clearly marked in the app footer:**

1. **Demurrage Rate**: $150/day
   - This is a PLACEHOLDER
   - Actual rates vary by port, vessel, forwarder
   - Used only for exposure ranking/prioritization
   - When real rates are available, we can update

2. **Unaccrued Invoice Cost**: $2,000/category
   - This is a PLACEHOLDER AVERAGE
   - Actual costs vary by shipment, supplier, category
   - Used to surface financial impact of missing invoices
   - GL history (Nov 2025+) too short for long-term averaging yet

3. **LFD Risk Window**: 3 days before/after port ETA
   - Simplified assumption
   - Real LFD depends on vessel stay days, port, etc.
   - Can be refined when vessel data becomes available

---

## Testing Checklist

### Functional Tests
- [ ] All 4 tabs load without errors
- [ ] Upload dashboard_data.xlsx → homepage populates
- [ ] Demurrage KPIs show: 77 at-risk, $76,050 exposure
- [ ] Missing invoices show: 852 records, ~$6.8M unaccrued
- [ ] Click into each tab → data displays correctly
- [ ] Search by SIPL works (try 155933B)
- [ ] Search by Container works (try SEGU3671280)

### Performance Tests
- [ ] Page load time: < 3 seconds
- [ ] Tab switching: instant
- [ ] Scrolling tables: smooth
- [ ] Charts render properly

### Data Quality Tests
- [ ] Financial numbers are in reasonable range
- [ ] No null values where data should exist
- [ ] Urgency badges display correctly
- [ ] Sorting by impact works (highest $ first)

---

## Troubleshooting

**"Please upload dashboard_data.xlsx"**
→ Run `python build_dashboard_data.py` first

**Blank tables or charts**
→ Check if invoice_compliance sheet exists in Excel file
→ Verify columns: sipl, container, overall_status, missing_categories

**Errors loading data**
→ Try deleting `~$dashboard_data.xlsx` (temporary lock file)
→ Re-export from `build_dashboard_data.py`

---

## Next Phases

**After V2 Launch:**
1. Gather user feedback on financial framing
2. Integrate actual demurrage rate cards (when available)
3. Add forwarder performance scorecard
4. Build historical trending (if snapshots available)
5. Expand drill-down to show full PO → Booking → GL journey

---

## Questions?

See: [[dashboard_v2_requirements]] for full specification  
See: [[invoice_compliance_business_logic]] for data pipeline details
