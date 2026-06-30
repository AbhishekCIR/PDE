# Market Rules Register

This document tracks the regulatory sources, manual sections, and tariff rules governing Battery Energy Storage System (BESS) market participation and co-optimization in ERCOT, MISO, and PJM.

---

## 1. ERCOT Market Rules

### Reference Standards
- **Source Document:** *ERCOT Nodal Operating Guides*
- **Primary Section:** Section 8 (Ancillary Services Rules & Qualifications)
- **Version Referenced:** v2026.01 (Effective Jan 2026)

### Key Rules Formulated in Solver
1. **Contingency Reserve Durations:**
   - **ERCOT Contingency Reserve Service (ECRS):** Enforces a **2-hour physical duration requirement**. If a battery commits $X$ MW to ECRS, the state of charge (SoC) must have at least $X \times 2.0$ MWh of energy stored.
   - **Responsive Reserve Service (RRS), Reg-Up, Non-Spin:** Enforces a **1-hour physical duration requirement** (SoC must have $X \times 1.0$ MWh).
2. **Downward Reserve Room:**
   - **Reg-Down:** Enforces a **1-hour headroom requirement** (battery must have at least $X \times 1.0$ MWh of empty capacity to absorb power).
3. **Power Limitations:**
   - Charging power plus Reg-Down award cannot exceed nameplate power capacity.
   - Discharging power plus upward reserve awards (Reg-Up, RRS, Non-Spin, ECRS) cannot exceed nameplate power capacity.

---

## 2. MISO Market Rules

### Reference Standards
- **Source Document:** *MISO Market Manual 002: Energy & Ancillary Services Markets*
- **Primary Section:** Section 4 & Section 5 (Operating Reserve Market & Scheduling)
- **Compliance Filing:** *MISO FERC Order 841 Compliance Filing (Electric Storage Resource model)*
- **Version Referenced:** v2025.12 (Effective Dec 2025)

### Key Rules Formulated in Solver
1. **Bidirectional Regulation Service:**
   - A resource cleared for Regulating Reserve is committed to move both up and down. This binds capacity in both directions.
   - Charging power plus Regulating Reserve capacity cannot exceed nameplate power.
   - Discharging power plus Regulating Reserve, Spinning Reserve, and Supplemental Reserve cannot exceed nameplate power.
2. **Reserve Durations:**
   - **Regulating Reserve, Spinning Reserve, Supplemental Reserve:** Enforces a **1-hour physical duration requirement** (SoC must have $X \times 1.0$ MWh for upward, and $X \times 1.0$ MWh of empty capacity for downward regulation).
3. **Regulation Mileage Payments:**
   - MISO clears regulation capacity and pays for mileage based on a historical Mileage-to-Capacity ratio ($m\_to\_c = 7.2$). The clearing revenue is:
     $$\text{Award} \times (\text{REG\_CAP\_Price} + 7.2 \times \text{REG\_MIL\_Price})$$

---

## 3. PJM Market Rules

### Reference Standards
- **Source Document:** *PJM Manual 11: Energy & Ancillary Services Market Operations*
- **Primary Section:** Section 3 (Regulation Market Operations) and Section 4 (Reserve Markets)
- **Version Referenced:** v2025.10 (Effective Oct 2025 - following Regulation Market Redesign)

### Key Rules Formulated in Solver
1. **RegA vs RegD Signal Splits (Co-optimization):**
   - The solver co-optimizes RegA (traditional, slow) and RegD (dynamic, fast) as mutually exclusive signals.
   - Pre-redesign historical datasets are used for backtesting RegA and RegD splits.
2. **Pay-for-Performance Accounting:**
   - Regulation credit is calculated based on:
     $$\text{Award} \times (\text{RMCCP} \times \text{PerfScore} + \text{RMPCP} \times \text{MileageRatio} \times \text{PerfScore})$$
   - Default performance scores: RegD = 0.95, RegA = 0.90.
   - Default mileage ratios: RegD = 3.5, RegA = 1.2.
3. **Reserve Durations:**
   - **RegD, Synchronized Reserve (SYNCH), Non-Synchronized Reserve (NONSYNCH):** Enforces a **30-minute sustainability requirement** ($0.5$ hours).
   - **RegA:** Enforces a **1-hour sustainability requirement** ($1.0$ hours).
4. **Bidirectional Regulation Reservation:**
   - If committed to RegA or RegD, the battery must hold BOTH state-of-charge headroom (for charging movement) and footroom (for discharging movement) equal to the award times the duration requirement.
