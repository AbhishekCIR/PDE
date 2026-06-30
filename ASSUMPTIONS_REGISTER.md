# Assumptions Register

This document tracks engineering assumptions, modeling approximations, and financial boundaries adopted in the Multi-Market BESS Optimization Engine.

---

## 1. Physical Battery Approximations

1. **Linear Round-Trip Efficiency (RTE):**
   - **Assumption:** Charging efficiency ($\eta_c$) and discharging efficiency ($\eta_d$) are modeled as constant values equal to $\sqrt{RTE}$.
   - **Rationale:** Prevents non-linearities in the objective function, keeping the MILP solvable in fraction-of-a-second times.
   - **Deviation from Reality:** In physical batteries, efficiency varies with State of Charge (SoC), C-rate, and temperature.

2. **State of Charge (SoC) Boundaries:**
   - **Assumption:** BESS can charge and discharge continuously across the entire range $[0, E_{\text{max}}]$ unless constrained by VPP capacity.
   - **Deviation from Reality:** BMS (Battery Management Systems) typically limit the operational range to $[5\%, 95\%]$ or similar window to prevent accelerated cell degradation. This can be adjusted by the user in the initial SoC and energy capacity inputs.

3. **Throughput-Based Degradation:**
   - **Assumption:** Degradation is modeled as a linear cost per MWh of throughput ($/MWh).
   - **Rationale:** Simplifies optimization objective. During backtests, we evaluate this against EFC (Equivalent Full Cycles).
   - **Deviation from Reality:** Calendar degradation (time-based) and temperature-based degradation are not included in the operational dispatch since they do not affect marginal dispatch decision variables.

---

## 2. Ancillary Services and Performance Scores

1. **Constant Performance Scores:**
   - **Assumption:** PJM RegA (0.90) and RegD (0.95) performance scores are held constant across all intervals.
   - **Rationale:** Performance scores depend on real-world telemetry response and are not known during pre-dispatch optimization. 
   - **Deviation from Reality:** Real performance scores fluctuate hourly based on asset response accuracy, precision, and delay.

2. **Bidirectional Regulation Mileage:**
   - **Assumption:** Regulation mileage factors (MISO $m\_to\_c = 7.2$, PJM RegD = 3.5, RegA = 1.2) are treated as constants representing typical rolling 30-day averages.
   - **Deviation from Reality:** Actual mileage ratios are calculated ex-post based on the real-world regulation signals issued by the ISO.

---

## 3. Financial Model Boundaries

1. **Operational vs. Project Finance Model:**
   - **Assumption:** Taxes (federal, state, local), ITC (Investment Tax Credit), MACRS depreciation, debt service, property tax, insurance, and fixed O&M costs are **excluded** from the optimization engine.
   - **Rationale:** These parameters do not influence the marginal operational dispatch (the battery dispatch doesn't change based on tax brackets or interest rates). They are post-dispatch financial accounting items.

2. **Capacity Market Compliance Assumption:**
   - **Assumption:** Static MISO PRA and PJM RPM capacity revenues assume **100% compliance** with all availability obligations.
   - **Rationale:** Penalty risks (e.g., PJM Capacity Performance non-performance charges during PAIs) are highly stochastic and non-linear, and are excluded from the operational model.
