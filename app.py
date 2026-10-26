# app.py
import streamlit as st
import pandas as pd
import numpy as np
import io
import os
import altair as altair

import importlib
import sys

# Force fresh reload of sub-modules to eliminate Streamlit Cloud cache desync
for mod_name in ['market_isone', 'market_pjm', 'market_ercot', 'market_miso', 'excel_dashboard_generator']:
    if mod_name in sys.modules:
        try:
            importlib.reload(sys.modules[mod_name])
        except Exception:
            pass

from market_generic import Generic_Optimizer
from market_ercot import ERCOT_Optimizer
from market_miso import MISO_Optimizer
from market_pjm import PJM_Optimizer
from market_isone import ISONE_Optimizer

def compute_mri_accreditation(dur_hr: float) -> float:
    """Marginal Reliability Impact (MRI) accreditation curve for ISO-NE BESS."""
    durations = [2.0, 3.0, 4.0, 6.0, 8.0]
    factors = [0.22, 0.31, 0.40, 0.68, 0.85]
    if dur_hr <= durations[0]:
        return factors[0]
    if dur_hr >= durations[-1]:
        return factors[-1]
    return float(np.interp(dur_hr, durations, factors))

from data_adapters.base_adapter import BaseDataAdapter
from data_adapters.ercot_adapter import ERCOTDataAdapter
from data_adapters.miso_adapter import MISODataAdapter
from data_adapters.pjm_adapter import PJMDataAdapter

from forecast_engine.persistence_forecast import PersistenceForecastEngine
from excel_dashboard_generator import export_bess_dashboard_excel

# Configure the Streamlit page
st.set_page_config(page_title="Multi-Market BESS Optimizer", page_icon="🔋", layout="wide")

st.title("🔋 Battery Energy Storage System (BESS) Multi-Market Optimizer")
st.markdown("""
Co-optimize energy arbitrage and ancillary service awards dynamically using Mixed-Integer Linear Programming (MILP). Supports multiple operating modes, variable regulation tranches, rolling-horizon simulations, and audit-ready live formula Excel exports.
""")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("🌍 Market Selection")
market_options = ["Generic", "ERCOT", "MISO", "PJM", "ISO-NE"]
selected_market = st.sidebar.selectbox("Select Market", options=market_options, index=3)

# Advanced Configuration Parameters per Market
st.sidebar.header("⚙️ Advanced Parameters")

# Setup defaults based on selected market
if selected_market == "ERCOT":
    elcc_default = 1.00
    cap_price_default = 0.0
elif selected_market == "MISO":
    elcc_default = 0.50
    cap_price_default = 50.0
elif selected_market == "PJM":
    elcc_default = 0.37
    cap_price_default = 131.57
elif selected_market == "ISO-NE":
    elcc_default = 0.50
    cap_price_default = 0.0  # ISO-NE uses $/kW-month FCM in its own sidebar block
else:
    elcc_default = 1.00
    cap_price_default = 0.0

elcc_factor = st.sidebar.slider("ELCC Capacity Credit Factor", min_value=0.0, max_value=1.0, value=elcc_default, step=0.01, help="Derating fraction for Capacity Market revenue.")
capacity_price_mw_day = 0.0
capacity_price_kw_month = 3.50
if selected_market != "ISO-NE":
    capacity_price_mw_day = st.sidebar.number_input("Capacity Price ($/MW-day)", min_value=0.0, value=cap_price_default, step=0.01, help="Capacity price for MISO/PJM calculations.")

st.sidebar.markdown("---")
st.sidebar.header("🔋 Battery Specifications")
if selected_market == "PJM":
    default_power = 250.0
    default_duration = 4.0
elif selected_market == "ISO-NE":
    default_power = 4.5
    default_duration = 3.0
else:
    default_power = 100.0
    default_duration = 4.0
power_mw = st.sidebar.number_input("Power Capacity (MW)", min_value=0.1, value=default_power, step=0.5 if selected_market == "ISO-NE" else 10.0)
duration_hr = st.sidebar.number_input("Duration (Hours)", min_value=0.5, value=default_duration, step=0.5 if selected_market == "ISO-NE" else 1.0)
rte = st.sidebar.slider("Round Trip Efficiency (RTE)", min_value=0.50, max_value=1.00, value=0.88, step=0.01)
max_cycles_per_day = st.sidebar.number_input("Max Cycles per Day", min_value=0.1, value=1.2, step=0.1)
initial_soc_pct = st.sidebar.slider("Initial SoC (%)", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
deg_cost = st.sidebar.number_input("Degradation Cost ($/MWh Discharged)", min_value=0.0, value=5.0, step=0.5)
mileage_factor = st.sidebar.slider("Regulation Mileage Wear Factor", min_value=0.01, max_value=0.50, value=0.10, step=0.01, help="Fraction of regulation capacity translating to cycle wear.")
reg_throughput_factor = st.sidebar.slider("Regulation AGC Throughput Factor", min_value=0.00, max_value=0.50, value=0.15, step=0.01, help="Approximate expected energy throughput (MWh) per cleared MW of regulation capacity per hour.")

# ISO-NE / WDT parameters
enable_reserves = True
apply_wdt_charges = True
isone_reg_cap_pct = 20.0
isone_capacity_mode = "Seasonal (2028+ CAR Reform)"
summer_capacity_price = 4.50
winter_capacity_price = 2.50
enable_pfp_protection = True
pfp_min_soc_pct = 0.15

if selected_market == "ISO-NE":
    st.sidebar.markdown("---")
    st.sidebar.header("⚡ ISO-NE / WDT Parameters")

    # Capacity Framework Choice (Seasonal 2028+ CAR Reform vs Annual Flat Legacy)
    isone_capacity_mode = st.sidebar.radio(
        "Capacity Market Framework",
        ["Seasonal (2028+ CAR Reform)", "Annual Flat (Pre-2028 Legacy)"],
        help="ISO-NE Capacity Auction Reform (CAR) moves from 12-month annual contracts to separate Summer (May-Oct) and Winter (Nov-Apr) prompt seasonal auctions."
    )

    # Suggested MRI accreditation based on duration (safe against hot-reload cache)
    if hasattr(ISONE_Optimizer, "get_mri_accreditation"):
        suggested_mri = ISONE_Optimizer.get_mri_accreditation(duration_hr)
    else:
        suggested_mri = compute_mri_accreditation(duration_hr)
    elcc_factor = st.sidebar.slider(
        "Battery Accreditation Factor (MRI / ELCC)",
        min_value=0.05, max_value=1.0, value=float(suggested_mri), step=0.01,
        help=f"Marginal Reliability Impact (MRI) capacity accreditation derating. Suggested for {duration_hr}-hr BESS: ~{suggested_mri*100:.0f}%."
    )

    if isone_capacity_mode.startswith("Seasonal"):
        col_s1, col_s2 = st.sidebar.columns(2)
        summer_capacity_price = col_s1.number_input(
            "Summer ($/kW-mo)", min_value=0.0, value=4.50, step=0.10,
            help="Summer Peak Capacity clearing price (May 1 – Oct 31)."
        )
        winter_capacity_price = col_s2.number_input(
            "Winter ($/kW-mo)", min_value=0.0, value=2.50, step=0.10,
            help="Winter Capacity clearing price (Nov 1 – Apr 30)."
        )
        capacity_price_kw_month = (summer_capacity_price * 6.0 + winter_capacity_price * 6.0) / 12.0
    else:
        capacity_price_kw_month = st.sidebar.number_input(
            "FCM Price ($/kW-month)", min_value=0.0, value=3.50, step=0.10,
            help="ISO-NE Forward Capacity Market annual clearing price."
        )
        summer_capacity_price = capacity_price_kw_month
        winter_capacity_price = capacity_price_kw_month

    # Pay-for-Performance (PfP) Scarcity Safeguard
    enable_pfp_protection = st.sidebar.checkbox(
        "Enable PfP Scarcity Buffer (15% SoC)",
        value=True,
        help="Maintains an emergency 15% State-of-Charge buffer during peak risk hours to prevent catastrophic $9,337/MWh Pay-for-Performance penalties."
    )

    isone_reg_cap_pct = st.sidebar.slider(
        "Regulation Capacity (%)", min_value=0.0, max_value=50.0, value=20.0, step=1.0
    )
    enable_reserves = st.sidebar.checkbox("Enable Spinning/Non-Spin Reserves", value=True)
    apply_wdt_charges = st.sidebar.checkbox(
        "Apply WDT Demand Charges (RI Energy Tariff No. 3)", value=True,
        help="Narragansett Electric Wholesale Distribution Tariff demand charges + hard on-peak no-charge constraint ($10.55/kW-mo on-peak penalty prevention)."
    )
    mileage_factor = st.sidebar.number_input("Regulation Mileage Factor", min_value=0.0, value=0.10, step=0.01)
    st.sidebar.caption(
        "🚫 **On-peak (weekdays 14:00–19:00):** charging is **hard-blocked** when WDT is enabled "
        f"(${ISONE_Optimizer.ONPEAK_RATE}/kW-month)."
    )
    # Convert FCM $/kW-month -> approx $/MW-day for Excel export compatibility
    capacity_price_mw_day = capacity_price_kw_month * 1000.0 / 30.44

# PJM-Specific Regulation Safeguards & Variable Tranche Configuration
pjm_user_tranches = []
enable_pjm_tranches = False
pjm_reg_cap_pct = 20.0
pjm_max_reg_hours = 5.0
pjm_allow_slicing = False
if selected_market == "PJM":
    st.sidebar.markdown("---")
    st.sidebar.header("🛡️ PJM Regulation Market Safeguards")
    pjm_reg_cap_pct = st.sidebar.slider(
        "Regulation Capacity Ceiling (% of Nameplate)", 
        min_value=0.0, 
        max_value=100.0, 
        value=20.0, 
        step=5.0, 
        help="Hard ceiling on maximum regulation capacity allowed to clear. 20% is recommended (Ram Bonthala / CHR Memo) to prevent market self-cannibalization and enable daily energy cycling."
    )
    pjm_calc_max_reg_mw = (pjm_reg_cap_pct / 100.0) * power_mw
    st.sidebar.caption(f"🔒 **Enforced Max Reg Cap:** `{pjm_calc_max_reg_mw:.1f} MW` (out of `{power_mw:.1f} MW` Nameplate)")

    pjm_max_reg_hours = st.sidebar.slider(
        "Max Daily Regulation Hours (Budget)",
        min_value=0.0,
        max_value=24.0,
        value=5.0,
        step=1.0,
        help="Limits how many hours per day the BESS can participate in regulation. Bounding this to 4-6 hours preserves battery capacity for daily energy cycling and peak arbitrage."
    )

    st.sidebar.header("⚖️ Operational Commitment Mode")
    pjm_dispatch_mode = st.sidebar.radio(
        "Inverter Commitment Style",
        options=["Clean Product Exclusivity (Recommended)", "Allow Inverter Slicing"],
        index=0,
        help="Clean Product Exclusivity dedicates 100% of the battery to ONE product per hour (no overlapping commitments). Allow Inverter Slicing permits co-optimized fractional MW sharing across products."
    )
    pjm_allow_slicing = (pjm_dispatch_mode == "Allow Inverter Slicing")
    if not pjm_allow_slicing:
        st.sidebar.caption("🔒 **Active:** Clean Product Exclusivity (Zero double-commitment)")
    else:
        st.sidebar.caption("⚡ **Active:** Inverter Slicing (Fractional MW co-optimization)")

    st.sidebar.header("📊 PJM Variable Regulation Tranches")
    enable_pjm_tranches = st.sidebar.checkbox("Enable Classified Tranche Bidding", value=False, help="Divides regulation offers into segmented price-quantity tranches up to the capacity ceiling.")
    
    if enable_pjm_tranches:
        num_tranches = st.sidebar.number_input("Number of Tranches to Bid", min_value=1, max_value=10, value=3, step=1)
        default_names = ["Tranche 1 (Base)", "Tranche 2 (Mid)", "Tranche 3 (Peak)", "Tranche 4 (Spike)", "Tranche 5 (Super-Peak)"]
        default_mws = [pjm_calc_max_reg_mw * 0.40, pjm_calc_max_reg_mw * 0.40, pjm_calc_max_reg_mw * 0.20, 0.0, 0.0]
        default_rates = [0.0, 30.0, 60.0, 90.0, 120.0]

        total_reg_cap = 0.0
        for i in range(int(num_tranches)):
            d_name = default_names[i] if i < len(default_names) else f"Tranche {i+1}"
            d_mw = default_mws[i] if i < len(default_mws) else 0.0
            d_rate = default_rates[i] if i < len(default_rates) else float(i * 30.0)

            with st.sidebar.expander(f"⚙️ Config for Tranche #{i+1}", expanded=(i < 3)):
                t_name = st.text_input(f"Name #{i+1}", value=d_name, key=f"app_tname_{i}")
                t_mw = st.number_input(f"Capacity MW #{i+1}", min_value=0.0, max_value=float(pjm_calc_max_reg_mw), value=float(d_mw), step=5.0, key=f"app_tmw_{i}")
                t_rate = st.number_input(f"Offer Hurdle Rate ($/MW) #{i+1}", min_value=0.0, max_value=1000.0, value=float(d_rate), step=5.0, key=f"app_trate_{i}")
                
                pjm_user_tranches.append({
                    "name": t_name,
                    "mw": t_mw,
                    "hurdle_rate": t_rate
                })
                total_reg_cap += t_mw

        st.sidebar.caption(f"**Total Regulation Offer:** {total_reg_cap:.1f} MW / {pjm_calc_max_reg_mw:.1f} MW allowed cap ({total_reg_cap/power_mw*100:.1f}%)")

st.sidebar.markdown("---")
st.sidebar.header("🎯 Operating Mode")
operating_modes = ["Merchant Only", "Merchant + Capacity", "Merchant + VPP", "Tolling Agreement"]
default_mode_idx = 1 if selected_market == "PJM" else 0
selected_mode = st.sidebar.selectbox("Select Business Model", options=operating_modes, index=default_mode_idx)

vpp_reserve_mw = 0.0
vpp_start_h = 17
vpp_end_h = 21

if selected_mode == "Merchant + VPP":
    st.sidebar.subheader("VPP Reservation Details")
    vpp_reserve_mw = st.sidebar.number_input("VPP Reserved MW", min_value=0.0, max_value=power_mw, value=20.0, step=5.0)
    vpp_start_h = st.sidebar.slider("VPP Start Hour (HE)", min_value=0, max_value=23, value=17)
    vpp_end_h = st.sidebar.slider("VPP End Hour (HE)", min_value=0, max_value=23, value=21)

is_tolling_flag = (selected_mode == "Tolling Agreement")

st.sidebar.markdown("---")
st.sidebar.header("📈 Simulation Mode")
sim_modes = ["Perfect Foresight (Benchmark)", "Rolling Horizon (Forecast-Driven)"]
selected_sim_mode = st.sidebar.selectbox("Select Solver Mode", options=sim_modes, index=0)

forecast_method = "naive"
forecast_horizon_hrs = 48
forecast_mape = 0.0

rolling_step_hrs = 24
if selected_sim_mode == "Rolling Horizon (Forecast-Driven)":
    st.sidebar.subheader("Forecast Engine Configuration")
    forecast_method = st.sidebar.selectbox("Forecasting Algorithm", options=["rolling_average", "naive", "noisy_actual"], index=0)
    forecast_horizon_hrs = st.sidebar.slider("Look-ahead Horizon (Hours)", min_value=12, max_value=168, value=48, step=12)
    rolling_step_hrs = st.sidebar.selectbox(
        "Dispatch Execution Step",
        options=[24, 12, 6, 1],
        index=0,
        format_func=lambda x: f"{x} Hours (Fast Daily Rolling - Recommended)" if x == 24 else f"{x} Hour(s)",
        help="How many hours of optimal dispatch are committed before re-forecasting. 24 hours reflects standard Day-Ahead BESS operations and executes in seconds."
    )
    if forecast_method == "noisy_actual":
        forecast_mape = st.sidebar.slider("Forecast Error (MAPE %)", min_value=0, max_value=50, value=15, step=5) / 100.0

# Initialize Session State Data
if 'data_df' not in st.session_state:
    st.session_state['data_df'] = None
if 'data_source' not in st.session_state:
    st.session_state['data_source'] = None
if 'active_market' not in st.session_state:
    st.session_state['active_market'] = None
if 'opt_results' not in st.session_state:
    st.session_state['opt_results'] = None

# --- MAIN PAGE INGESTION ---
st.write(f"### 1. Ingest telemetry data for **{selected_market}**")

# Display required columns for selected market
if selected_market == "ERCOT":
    required_cols_msg = "`timestamp`, `LMP`, `REGUP`, `REGDN`, `RRS`, `NSPIN`, `ECRS`"
elif selected_market == "MISO":
    required_cols_msg = "`timestamp`, `LMP`, `REG_CAP`, `REG_MIL`, `SPIN`, `SUPP`"
elif selected_market == "PJM":
    required_cols_msg = "`timestamp`, `LMP`, `RMCCP`, `RMPCP`, `Mileage`, `Price_SYNCH`, `Price_NONSYNCH` (or `Reg_Price` / `Reg_Effective_Price`)"
elif selected_market == "ISO-NE":
    required_cols_msg = "`timestamp`, `LMP`, `Charge_LMP`, `Price_RegUp`, `Price_RegDn`, `Price_Reg_Service`, `Price_Spin`, `Price_NonSpin` (15-min preferred; hourly auto-upsampled)"
else:
    required_cols_msg = "`timestamp`, `LMP`, `Reg_Price`"

st.info(f"Required columns (case-insensitive): {required_cols_msg}")

@st.cache_data
def get_pjm_template_bytes():
    """Returns (xlsx_bytes, csv_bytes) for PJM input template, with in-memory fallback."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tmpl_xlsx = os.path.join(base_dir, "PJM_Market_Template.xlsx")
    tmpl_csv = os.path.join(base_dir, "PJM_Market_Template.csv")
    
    xlsx_bytes, csv_bytes = None, None
    if os.path.exists(tmpl_xlsx):
        try:
            with open(tmpl_xlsx, "rb") as f:
                xlsx_bytes = f.read()
        except Exception:
            pass
            
    if os.path.exists(tmpl_csv):
        try:
            with open(tmpl_csv, "rb") as f:
                csv_bytes = f.read()
        except Exception:
            pass
            
    if xlsx_bytes is None or csv_bytes is None:
        pjm_opt = PJM_Optimizer()
        df_sample = pjm_opt.generate_sample_data(days=365)
        df_sample['timestamp'] = df_sample['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
        
        if csv_bytes is None:
            csv_bytes = df_sample.to_csv(index=False).encode('utf-8')
            
        if xlsx_bytes is None:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as writer:
                df_sample.to_excel(writer, sheet_name='Hourly_Market_Data', index=False)
            xlsx_bytes = buf.getvalue()
            
    return xlsx_bytes, csv_bytes

if selected_market == "PJM":
    st.caption("💡 **Need an 8,760-hour template?** Download pre-formatted full-year PJM input files with realistic pricing curves and formulas:")
    tmpl_col1, tmpl_col2 = st.columns([1, 1])
    xlsx_data, csv_data = get_pjm_template_bytes()
    with tmpl_col1:
        st.download_button(
            label="📥 Download 8760 PJM Template (.xlsx)",
            data=xlsx_data,
            file_name="PJM_Market_Template_8760.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    with tmpl_col2:
        st.download_button(
            label="📥 Download 8760 PJM Template (.csv)",
            data=csv_data,
            file_name="PJM_Market_Template_8760.csv",
            mime="text/csv",
            use_container_width=True
        )

uploaded_file = st.file_uploader("Upload Market CSV or Excel file", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        if selected_market == "ERCOT":
            adapter = ERCOTDataAdapter()
        elif selected_market == "MISO":
            adapter = MISODataAdapter()
        elif selected_market == "PJM":
            adapter = PJMDataAdapter()
        elif selected_market == "ISO-NE":
            # Preserve native 15-min resolution (BaseDataAdapter reindexes to hourly)
            adapter = None
        else:
            adapter = BaseDataAdapter('Generic', ['LMP', 'Reg_Price'], {
                'LMP': ['LMP', 'lmp', 'price'],
                'Reg_Price': ['Reg_Price', 'reg_price', 'regulation']
            })

        if selected_market == "ISO-NE":
            logs = []
            name = uploaded_file.name.lower()
            if name.endswith('.csv'):
                df_raw = pd.read_csv(uploaded_file)
            else:
                df_raw = pd.read_excel(uploaded_file)
            # Case-insensitive column rename
            col_map = {c.strip().lower(): c for c in df_raw.columns}
            rename = {}
            alias_map = {
                'timestamp': ['timestamp', 'datetime', 'date_time', 'time'],
                'LMP': ['lmp', 'da_lmp', 'price'],
                'Charge_LMP': ['charge_lmp', 'rt_lmp'],
                'Price_RegUp': ['price_regup', 'regup'],
                'Price_RegDn': ['price_regdn', 'regdn'],
                'Price_Reg_Service': ['price_reg_service', 'reg_service', 'regmileage'],
                'Price_Spin': ['price_spin', 'spin', 'tmsr'],
                'Price_NonSpin': ['price_nonspin', 'nonspin', 'tmnsr'],
            }
            for std, aliases in alias_map.items():
                for a in aliases:
                    if a in col_map:
                        rename[col_map[a]] = std
                        break
            df_clean = df_raw.rename(columns=rename)
            if 'timestamp' not in df_clean.columns:
                raise ValueError("ISO-NE upload requires a timestamp column.")
            if 'LMP' not in df_clean.columns:
                raise ValueError("ISO-NE upload requires an LMP column.")
            df_clean['timestamp'] = pd.to_datetime(df_clean['timestamp'])
            df_clean = df_clean.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
            if 'Charge_LMP' not in df_clean.columns:
                df_clean['Charge_LMP'] = df_clean['LMP']
                logs.append("Charge_LMP missing — defaulted to LMP")
            for col in ['Price_RegUp', 'Price_RegDn', 'Price_Reg_Service', 'Price_Spin', 'Price_NonSpin']:
                if col not in df_clean.columns:
                    df_clean[col] = 0.0
                    logs.append(f"Filled missing column {col} with 0.0")
            keep = ['timestamp', 'LMP', 'Charge_LMP', 'Price_RegUp', 'Price_RegDn',
                    'Price_Reg_Service', 'Price_Spin', 'Price_NonSpin']
            if 'CAP_LIMIT' in df_clean.columns:
                keep.append('CAP_LIMIT')
            df_clean = df_clean[keep].copy()
            logs.append(f"Loaded {len(df_clean)} intervals (native resolution preserved for WDT).")
        else:
            df_clean, logs = adapter.process(uploaded_file)
        
        st.session_state['data_df'] = df_clean
        st.session_state['data_source'] = f"Uploaded File ({uploaded_file.name})"
        st.session_state['active_market'] = selected_market
        
        st.success("Data Ingestion and Standardization Successful!")
        with st.expander("Ingestion Log"):
            for log in logs:
                st.write(f"- {log}")
    except Exception as e:
        st.error(f"Failed to ingest data: {e}")
else:
    st.info("No file uploaded. You can generate synthetic market prices for testing.")
    if st.button("🎲 Generate Synthetic Market Data"):
        if selected_market == "ERCOT":
            opt = ERCOT_Optimizer()
        elif selected_market == "MISO":
            opt = MISO_Optimizer()
        elif selected_market == "PJM":
            try:
                opt = PJM_Optimizer(power_mw=power_mw, enable_tranches=enable_pjm_tranches, tranches=pjm_user_tranches if enable_pjm_tranches else None, reg_cap_pct=pjm_reg_cap_pct, max_reg_hours_per_day=pjm_max_reg_hours)
            except TypeError:
                opt = PJM_Optimizer(power_mw=power_mw, enable_tranches=enable_pjm_tranches, tranches=pjm_user_tranches if enable_pjm_tranches else None, reg_cap_pct=pjm_reg_cap_pct)
        elif selected_market == "ISO-NE":
            opt = ISONE_Optimizer(
                power_mw=power_mw,
                duration_hr=duration_hr,
                capacity_price_kw_month=capacity_price_kw_month,
                elcc_factor=elcc_factor,
                reg_cap_pct=isone_reg_cap_pct,
                enable_reserves=enable_reserves,
                apply_wdt_charges=apply_wdt_charges,
                mileage_factor=mileage_factor,
            )
        else:
            opt = Generic_Optimizer()
            
        # ISO-NE sample data is 15-min; 7 days for quick UI gen, or 365 for others
        gen_days = 30 if selected_market == "ISO-NE" else 365
        df_synthetic = opt.generate_sample_data(days=gen_days)
        st.session_state['data_df'] = df_synthetic
        st.session_state['data_source'] = f"Synthetic {selected_market} Data"
        st.session_state['active_market'] = selected_market
        st.success(f"Generated {gen_days} day(s) of synthetic {selected_market} pricing!")

if st.session_state['data_df'] is not None:
    with st.expander("Ingested Hourly Data Preview", expanded=False):
        st.dataframe(st.session_state['data_df'].head(24), use_container_width=True)

# --- RUN OPTIMIZATION ---
st.write("### 2. Solve dispatch equations")

if st.session_state['data_df'] is not None:
    if st.session_state['active_market'] != selected_market:
        st.warning(f"Loaded data is for {st.session_state['active_market']} but selected market is {selected_market}. Please re-upload or re-generate data.")
    
    if st.button("🚀 Execute Optimization", type="primary"):
        with st.spinner("Executing MILP solver..."):
            try:
                if selected_market == "ERCOT":
                    optimizer = ERCOT_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        reg_throughput_factor=reg_throughput_factor, is_tolling=is_tolling_flag
                    )
                elif selected_market == "MISO":
                    optimizer = MISO_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        capacity_price_mw_day=capacity_price_mw_day,
                        reg_throughput_factor=reg_throughput_factor, is_tolling=is_tolling_flag
                    )
                elif selected_market == "PJM":
                    pjm_init_kwargs = {
                        "power_mw": power_mw, "duration_hr": duration_hr, "rte": rte,
                        "max_cycles_per_day": max_cycles_per_day, "initial_soc_pct": initial_soc_pct,
                        "degradation_cost_per_mwh": deg_cost, "mileage_factor": mileage_factor,
                        "capacity_price_mw_day": capacity_price_mw_day,
                        "reg_throughput_factor": reg_throughput_factor,
                        "is_tolling": is_tolling_flag,
                        "enable_tranches": enable_pjm_tranches,
                        "tranches": pjm_user_tranches if enable_pjm_tranches else None,
                        "elcc_factor": elcc_factor,
                        "reg_cap_pct": pjm_reg_cap_pct,
                        "max_reg_hours_per_day": pjm_max_reg_hours,
                        "allow_slicing": pjm_allow_slicing
                    }
                    try:
                        optimizer = PJM_Optimizer(**pjm_init_kwargs)
                    except TypeError:
                        pjm_init_kwargs.pop("max_reg_hours_per_day", None)
                        optimizer = PJM_Optimizer(**pjm_init_kwargs)
                elif selected_market == "ISO-NE":
                    optimizer = ISONE_Optimizer(
                        power_mw=power_mw,
                        duration_hr=duration_hr,
                        rte=rte,
                        max_cycles_per_day=max_cycles_per_day,
                        initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost,
                        mileage_factor=mileage_factor,
                        capacity_price_kw_month=capacity_price_kw_month,
                        elcc_factor=elcc_factor,
                        reg_cap_pct=isone_reg_cap_pct,
                        enable_reserves=enable_reserves,
                        apply_wdt_charges=apply_wdt_charges,
                        reg_throughput_factor=reg_throughput_factor,
                        is_tolling=is_tolling_flag,
                        # CAR 2028+ Reform & Seasonal Parameters
                        capacity_pricing_mode="Seasonal" if isone_capacity_mode.startswith("Seasonal") else "Annual Flat",
                        summer_capacity_price_kw_month=summer_capacity_price,
                        winter_capacity_price_kw_month=winter_capacity_price,
                        enable_pfp_protection=enable_pfp_protection,
                        pfp_min_soc_pct=0.15,
                    )
                else:
                    optimizer = Generic_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        reg_throughput_factor=reg_throughput_factor, is_tolling=is_tolling_flag
                    )

                optimizer.config['elcc_factor'] = elcc_factor

                data_in = st.session_state['data_df'].copy()
                
                if selected_mode == "Tolling Agreement":
                    data_in['Charge_LMP'] = 0.0
                elif 'Charge_LMP' not in data_in.columns:
                    data_in['Charge_LMP'] = data_in['LMP']
                elif selected_market != "ISO-NE":
                    data_in['Charge_LMP'] = data_in['LMP']
                
                data_in['CAP_LIMIT'] = power_mw
                if selected_mode == "Merchant + VPP":
                    hours = data_in['timestamp'].dt.hour
                    vpp_mask = (hours >= vpp_start_h) & (hours <= vpp_end_h)
                    data_in.loc[vpp_mask, 'CAP_LIMIT'] = max(0.0, power_mw - vpp_reserve_mw)

                forecaster = None
                if selected_sim_mode == "Rolling Horizon (Forecast-Driven)":
                    forecaster = PersistenceForecastEngine(
                        market_name=selected_market,
                        method=forecast_method,
                        mape=forecast_mape
                    )

                progress_bar = st.progress(0, text="Solving LP bounds...")
                def update_progress(current, total):
                    progress_bar.progress((current + 1) / total, text=f"Solving Optimization: step {current+1} of {total}")

                df_opt = optimizer.run_optimization_dispatch(
                    df=data_in,
                    progress_callback=update_progress,
                    forecast_engine=forecaster,
                    forecast_horizon_hrs=forecast_horizon_hrs,
                    forecast_mape=forecast_mape,
                    rolling_step_hrs=rolling_step_hrs
                )
                progress_bar.empty()
                st.success("Optimization Solved Successfully!")

                # --- SUMMARY REPORT METRICS ---
                metrics, utilization = optimizer.calculate_summary_metrics(df_opt)
                
                if selected_mode in ["Merchant Only", "Tolling Agreement"]:
                    metrics['Total Net Merchant Revenue ($)'] -= metrics.get('Static Capacity Revenue ($)', 0.0)
                    metrics['Static Capacity Revenue ($)'] = 0.0
                    if 'Static Capacity Revenue (FCM) ($)' in metrics:
                        metrics['Static Capacity Revenue (FCM) ($)'] = 0.0
                    df_opt['Capacity_Revenue'] = 0.0
                    wdt_col = df_opt['WDT_Demand_Cost'] if 'WDT_Demand_Cost' in df_opt.columns else 0.0
                    df_opt['revenue'] = (
                        df_opt['Energy_Revenue']
                        + df_opt['Ancillary_Revenue']
                        - df_opt['Total_Degradation_Cost']
                        - wdt_col
                    )

                # Pre-generate live formula Excel workbook
                excel_bytes = export_bess_dashboard_excel(
                    df_results=df_opt,
                    metrics=metrics,
                    power_mw=power_mw,
                    duration_hr=duration_hr,
                    rte=rte,
                    capacity_price_mw_day=capacity_price_mw_day,
                    elcc_factor=elcc_factor,
                    deg_cost=deg_cost,
                    mileage_factor=mileage_factor,
                    tranches=pjm_user_tranches if (selected_market == "PJM" and enable_pjm_tranches) else None,
                    market_name=selected_market
                )

                # Persist results in session_state so clicking Download or tabs never causes results to disappear!
                st.session_state['opt_results'] = {
                    'df_opt': df_opt,
                    'metrics': metrics,
                    'excel_bytes': excel_bytes,
                    'market_name': selected_market,
                    'enable_pjm_tranches': enable_pjm_tranches,
                    'pjm_user_tranches': pjm_user_tranches,
                    'allow_slicing': pjm_allow_slicing,
                    'apply_wdt_charges': apply_wdt_charges if selected_market == "ISO-NE" else False,
                    'optimizer_wdt': getattr(optimizer, '_wdt_monthly_df', None),
                }
                st.success("Optimization Solved Successfully! Detailed results and download options are displayed below.")
                
            except Exception as e:
                st.error(f"Solver Error: {e}")
                import traceback
                st.code(traceback.format_exc())


# --- PERSISTENT RESULTS DASHBOARD ---
# Rendered outside the Execute button so results NEVER vanish on re-runs or downloads
if st.session_state.get('opt_results') is not None:
    res = st.session_state['opt_results']
    df_opt = res['df_opt']
    metrics = res['metrics']
    excel_bytes = res['excel_bytes']
    market_res_name = res['market_name']
    enable_tr_res = res.get('enable_pjm_tranches', False)
    tr_res = res.get('pjm_user_tranches', [])

    st.markdown("---")
    st.subheader(f"📊 Operational & Financial Dashboard ({market_res_name})")
    is_slicing = res.get('allow_slicing', False)
    if not is_slicing and market_res_name == "PJM":
        st.info("🔒 **Commitment Style:** Clean Product Exclusivity (Audited 1-product-per-hour dispatch; Zero double-commitment).")
    elif is_slicing and market_res_name == "PJM":
        st.info("⚡ **Commitment Style:** Inverter Slicing (Shared fractional MW co-optimization).")

    # Immediate Download Bar at the top of results
    dl_col1, dl_col2 = st.columns([2, 1])
    with dl_col1:
        st.download_button(
            label="📥 Download Audit-Ready Excel Dashboard (.xlsx)",
            data=excel_bytes,
            file_name=f"BESS_Optimized_Dashboard_{market_res_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
            key="dl_xlsx_top_persistent"
        )
    with dl_col2:
        st.download_button(
            label="📄 Download Raw Hourly CSV",
            data=df_opt.to_csv(index=False).encode('utf-8'),
            file_name=f"BESS_Optimized_Hourly_{market_res_name}.csv",
            mime="text/csv",
            use_container_width=True,
            key="dl_csv_top_persistent"
        )

    # Metric Cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net Merchant Revenue", f"${metrics['Total Net Merchant Revenue ($)']:,.2f}")
    c2.metric("Energy Arbitrage Revenue", f"${metrics['Energy Arbitrage Revenue ($)']:,.2f}")
    c3.metric("Ancillary Services Revenue", f"${metrics['Ancillary Services Revenue ($)']:,.2f}")
    c4.metric("Capacity Revenue (Compliance)", f"${metrics['Static Capacity Revenue ($)']:,.2f}")
    
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Degradation Cost", f"-${metrics['Degradation Expense ($)']:,.2f}")
    c6.metric("Equivalent Full Cycles", f"{metrics['Equivalent Full Cycles (EFC)']:,.2f} cycles")
    c7.metric("Achieved Round-Trip Efficiency", f"{metrics['Achieved Round-Trip Efficiency']*100:.1f}%")
    c8.metric("AS Participation Fraction", f"{metrics['Ancillary Participation Fraction']*100:.1f}%")

    # Tranche Breakdown Metrics
    if market_res_name == "PJM" and enable_tr_res and tr_res:
        st.markdown("---")
        st.subheader("📊 PJM Regulation Tranche Breakdown")
        tranche_cols_ui = st.columns(len(tr_res))
        for idx, tr in enumerate(tr_res):
            t_name = tr['name']
            avg_cleared = metrics.get(f"Avg Cleared {t_name} (MW)", 0.0)
            tot_rev = metrics.get(f"Total {t_name} Revenue ($)", 0.0)
            with tranche_cols_ui[idx]:
                st.metric(
                    label=f"{t_name} ({tr['mw']} MW @ ${tr['hurdle_rate']}/MW)",
                    value=f"${tot_rev:,.2f}",
                    delta=f"Avg Cleared: {avg_cleared:.1f} MW"
                )

    # ISO-NE WDT Demand Charge Breakdown
    if market_res_name == "ISO-NE" and res.get('apply_wdt_charges', False):
        st.markdown("---")
        st.subheader("🔌 WDT Demand Charge Breakdown (Narragansett Electric Tariff No. 3)")
        wdt_df = res.get('optimizer_wdt')
        if wdt_df is None or (hasattr(wdt_df, 'empty') and wdt_df.empty):
            # Recompute from dispatch if not stored
            tmp_opt = ISONE_Optimizer()
            wdt_df = tmp_opt.calculate_wdt_demand_charges(df_opt)
        st.dataframe(wdt_df, use_container_width=True)
        wdt_total = metrics.get('WDT Annual Demand Charges ($)', wdt_df['WDT_Demand_Charge'].sum() if wdt_df is not None and len(wdt_df) else 0.0)
        c_w1, c_w2 = st.columns(2)
        c_w1.metric("Total WDT Demand Charges", f"${wdt_total:,.2f}")
        c_w2.metric("On-Peak Charging Hours", f"{metrics.get('On-Peak Charging Hours', 0):.2f} (must be 0)")

    # ISO-NE Capacity Market Breakdown (CAR 2028+ Reform)
    if market_res_name == "ISO-NE":
        st.markdown("---")
        st.subheader("🏛️ ISO-NE Capacity Market Breakdown (CAR 2028+ Reform)")
        cap_c1, cap_c2, cap_c3, cap_c4 = st.columns(4)
        cap_c1.metric("Capacity Framework", metrics.get("Capacity Market Framework", "Seasonal (2028+ CAR Reform)"))
        cap_c2.metric("MRI Accreditation Derate", metrics.get("Accreditation Derate (MRI/ELCC)", "N/A"))
        cap_c3.metric("Summer Capacity Revenue", f"${metrics.get('Summer Capacity Revenue ($)', 0.0):,.2f}")
        cap_c4.metric("Winter Capacity Revenue", f"${metrics.get('Winter Capacity Revenue ($)', 0.0):,.2f}")
        st.caption(
            f"🛡️ **Pay-for-Performance (PfP) Safeguard:** {metrics.get('PfP Scarcity Safeguard', 'Active (15% SoC Buffer)')} — "
            "protects against $9,337/MWh scarcity penalties during high-risk peak hours."
        )

    # Charts
    st.markdown("---")
    st.subheader("📈 State of Charge and Prices (HE 1-168 Preview)")
    preview_len = min(168, len(df_opt))
    chart_df = df_opt.head(preview_len).copy()
    
    base_chart = altair.Chart(chart_df).encode(x='timestamp:T')
    soc_line = base_chart.mark_line(color='#38BDF8', strokeWidth=3).encode(
        y=altair.Y('soc_mwh:Q', title='State of Charge (MWh)')
    )
    price_line = base_chart.mark_line(color='#F43F5E', strokeWidth=1, strokeDash=[4,4]).encode(
        y=altair.Y('LMP:Q', title='LMP Price ($/MWh)')
    )
    dual_chart = altair.layer(soc_line, price_line).resolve_scale(y='independent').properties(height=380)
    st.altair_chart(dual_chart, use_container_width=True)

    st.subheader("⚡ Hourly Power Dispatch Profile (HE 1-72 Preview)")
    disp_preview = df_opt.head(min(72, len(df_opt))).copy()
    disp_preview['Hour_Label'] = disp_preview['timestamp'].dt.strftime('%m/%d %H:00')
    
    active_disp_cols = ['charge_mw', 'discharge_mw']
    if market_res_name == "PJM" and enable_tr_res and tr_res:
        for tr in tr_res:
            c_name = f"{tr['name']}_MW"
            if c_name in disp_preview.columns:
                active_disp_cols.append(c_name)
    elif 'Total_Reg_MW' in disp_preview.columns:
        active_disp_cols.append('Total_Reg_MW')
    
    if 'SYNCH_MW' in disp_preview.columns:
        active_disp_cols.append('SYNCH_MW')

    disp_melt = pd.melt(
        disp_preview, 
        id_vars=['Hour_Label'], 
        value_vars=[col for col in active_disp_cols if col in disp_preview.columns],
        var_name='Dispatch_Stream',
        value_name='MW'
    )

    dispatch_bar_chart = altair.Chart(disp_melt).mark_bar().encode(
        x=altair.X('Hour_Label:N', title='Timestamp', axis=altair.Axis(labelAngle=-45)),
        y=altair.Y('MW:Q', title='Allocated MW'),
        color=altair.Color('Dispatch_Stream:N', scale=altair.Scale(scheme='tableau10')),
        tooltip=['Hour_Label', 'Dispatch_Stream', altair.Tooltip('MW:Q', format='.1f')]
    ).properties(height=380)
    st.altair_chart(dispatch_bar_chart, use_container_width=True)

    st.subheader("📋 Detailed Dispatch Results Preview")
    st.dataframe(df_opt.head(72), use_container_width=True)

    # Bottom Download Buttons
    b_col1, b_col2 = st.columns([2, 1])
    with b_col1:
        st.download_button(
            label="📥 Download Audit-Ready Excel Dashboard (.xlsx)",
            data=excel_bytes,
            file_name=f"BESS_Optimized_Dashboard_{market_res_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_xlsx_bottom_persistent",
            use_container_width=True
        )
    with b_col2:
        st.download_button(
            label="📄 Download Raw Hourly CSV",
            data=df_opt.to_csv(index=False).encode('utf-8'),
            file_name=f"BESS_Optimized_Hourly_{market_res_name}.csv",
            mime="text/csv",
            key="dl_csv_bottom_persistent",
            use_container_width=True
        )
