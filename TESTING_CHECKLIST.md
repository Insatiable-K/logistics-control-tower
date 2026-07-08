# Testing Checklist — Full Application

**Status:** Ready for Testing  
**Date:** July 8, 2026  

---

## ✅ ETL Validation

Run before testing the app:

```bash
python build_dashboard_data.py
```

**Verify these outputs:**
- [ ] `--- STEP 2: BUILD SHIPMENT_MAPPING (Backward Compatible) ---`
- [ ] `shipment_mapping (Backward Compatible): (31099, 3)`
- [ ] `--- STEP 7: BUILD INVOICE COMPLIANCE (PER-SIPL) ---`
- [ ] `invoice_compliance: (827, 17)`
- [ ] `BUILD COMPLETE` at the end
- [ ] File created: `data/dashboard_data.xlsx`

---

## ✅ App Startup

```bash
streamlit run app_cloud.py
```

**Verify:**
- [ ] App starts without errors
- [ ] Upload sidebar appears
- [ ] "Upload dashboard_data.xlsx" button visible

---

## ✅ Data Upload

- [ ] Click "Upload dashboard_data.xlsx"
- [ ] Select `data/dashboard_data.xlsx`
- [ ] File uploads successfully
- [ ] Three tabs appear: "📦 Booking", "🚢 Container Movement", "🧾 Invoice Compliance"

---

## ✅ Tab 1: Booking Performance Dashboard

Navigate to: **📦 Booking** tab

**Sidebar Filters:**
- [ ] Date range filter shows
- [ ] Can select booking date range
- [ ] Filter applies without errors

**KPIs Display:**
- [ ] "📊 Booking KPIs" section shows
- [ ] Shows metrics: Bookings Received, Approved, Containers Created, etc.
- [ ] Numbers display (even if 0)

**Data Display:**
- [ ] "📋 Booking Events" table displays
- [ ] Columns: event_date, etd, eta, event_status, container_id, po_number
- [ ] po_number column is present ✓ (this was broken before)
- [ ] Data shows correctly

**Trends & Insights:**
- [ ] "📈 Monthly Container Creation Trend" displays
- [ ] Chart renders without errors
- [ ] Expander "Monthly Breakdown" works

**Unmapped Bookings:**
- [ ] "🚨 Booking Gaps" metric shows
- [ ] Table displays unmapped POs

**Other Sections:**
- [ ] High Rollover Containers shows
- [ ] Container Drilldown search works

---

## ✅ Tab 2: Container Movement Control Tower

Navigate to: **🚢 Container Movement** tab

**Container Status:**
- [ ] Status table displays
- [ ] Shows all container statuses

**Current Movement:**
- [ ] "🚢 On Water" metric shows
- [ ] "📍 Arriving at port Today" metric shows
- [ ] "🏢 Reaching Branch Today" metric shows

**Upcoming Movement:**
- [ ] "📍 Port ETA" metric shows (Next 7 Days)
- [ ] "📦 Branch ETA" metric shows

**Exceptions:**
- [ ] "Containers in Exception" metric displays
- [ ] Table of exceptions shows (if any)

**LFD Risk:**
- [ ] "LFD Breach" count shows
- [ ] "Approaching LFD" count shows
- [ ] Tables display breach/approaching containers

**Documentation Risk:**
- [ ] Risk table displays
- [ ] Shows "Already at Port" and "Upcoming" sections

**Data Integrity:**
- [ ] Problem containers count shows
- [ ] Integrity check table displays

**Delay Performance:**
- [ ] Delayed containers metric shows
- [ ] Performance table displays

---

## ✅ Tab 3: Invoice Compliance Dashboard (NEW)

Navigate to: **🧾 Invoice Compliance** tab

**Sidebar Filters:**
- [ ] Invoice Compliance filters section shows in sidebar
- [ ] "Port ETA From" date picker works
- [ ] "Port ETA To" date picker works
- [ ] "Arrival Status" dropdown works
- [ ] "Missing Bill Type" dropdown works
- [ ] "Invoice Readiness" dropdown works

**Inline Filters:**
- [ ] "Freight Forwarder" selector works
- [ ] "Destination" selector works
- [ ] Search box works (container/SIPL/PO)

**Compliance Summary:**
- [ ] "📊 Invoice Compliance Summary" KPIs show:
  - [ ] Compliance Rate (%)
  - [ ] Ocean Freight Missing (count)
  - [ ] Customs Missing (count)
  - [ ] Duty Missing (count)
  - [ ] Drayage Missing (count)

**Missing Bills Tab:**
- [ ] "🔴 Missing Bills" tab shows count in label
- [ ] Table displays missing bills (if any)
- [ ] Columns show: Container, SIPL, Port ETA, Missing Bills, etc.

**Pending Bills Tab:**
- [ ] "⏳ Pending Bills" tab shows count in label
- [ ] Table displays pending bills (if any)
- [ ] Caption explains what "Pending" means

**Complete Tab:**
- [ ] "✅ Complete" tab shows count in label
- [ ] Table displays complete SIPLs (if any)
- [ ] Shows all 4 categories are satisfied

**Reconciliation Report:**
- [ ] Expander "📊 Reconciliation Report (Unmatched Bills Audit)" works
- [ ] Shows unmatched bills sheet (if available)
- [ ] Shows reconciliation audit summary

---

## ✅ Tabs Navigation & Persistence

**Critical Fix Verification:**

- [ ] Click Tab 1 (Booking)
- [ ] Adjust filters
- [ ] Click Tab 2 (Container Movement)
- [ ] Click back to Tab 1
- [ ] **Verify:** Tab 1 still shows, no disappearance ✅
- [ ] **Verify:** Filters didn't reset ✅

- [ ] Press F5 (refresh page)
- [ ] **Verify:** All three tabs still visible ✅
- [ ] **Verify:** Not displaying as single scrolling page ✅
- [ ] **Verify:** Same tab remains active ✅

---

## ✅ Error Handling

**Test Error Cases:**

- [ ] Try uploading wrong file (should show helpful error)
- [ ] Click different filters and tabs (should not crash)
- [ ] Enter invalid search text (should handle gracefully)

---

## ✅ Column Validation

**Verify All Required Columns Exist:**

```bash
python check_excel_columns.py
```

**Should show NO [TRAILING SPACE] or [LEADING SPACE] markers:**
- [ ] container_id ✓
- [ ] sipl_number ✓
- [ ] po_number ✓
- [ ] po_numbers (in invoice_compliance) ✓
- [ ] missing_categories ✓
- [ ] pending_categories ✓
- [ ] overall_status ✓

---

## Summary Checklist

### ETL Tests
- [ ] `python build_dashboard_data.py` completes successfully
- [ ] All sheets created in Excel
- [ ] shipment_mapping has po_number (backward compatibility)
- [ ] invoice_compliance has all required columns

### App Tests  
- [ ] Streamlit app starts
- [ ] File upload works
- [ ] All three tabs display
- [ ] Tab 1 (Booking) works with po_number
- [ ] Tab 2 (Container) works
- [ ] Tab 3 (Invoice) new features work

### Bug Fixes
- [ ] Tabs don't disappear on refresh ✅ (FIXED)
- [ ] No KeyError: 'po_number' ✅ (FIXED)
- [ ] All filters work properly ✅

### Backward Compatibility
- [ ] Booking dashboard: po_number field present ✅
- [ ] Container Movement: all data displays ✅
- [ ] Existing functionality unchanged ✅

---

## Sign-Off

When all checkboxes are complete:

```
✅ ETL generates valid data
✅ App loads successfully
✅ All three dashboards work
✅ Tabs persist across refresh
✅ No errors or crashes
✅ Backward compatibility maintained
✅ Invoice Compliance features working
✅ READY FOR PRODUCTION
```

---

**Test Date:** ______________  
**Tester Name:** ______________  
**Status:** ______________
