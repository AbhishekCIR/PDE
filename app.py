# app.py
import streamlit as st
import pandas as pd
import numpy as np
import io
import os
import altair as altair

from market_generic import Generic_Optimizer
from market_ercot import ERCOT_Optimizer
from market_miso import MISO_Optimizer
from market_pjm import PJM_Optimizer

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
market_options = ["Generic", "ERCOT", "MISO", "PJM"]
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
    elcc_default = 0.50
    cap_price_default = 329.17
else:
    elcc_default = 1.00
    cap_price_default = 0.0

elcc_factor = st.sidebar.slider("ELCC Capacity Credit Factor", min_value=0.0, max_value=1.0, value=elcc_default, step=0.05, help="Derating fraction for Capacity Market revenue.")
capacity_price_mw_day = st.sidebar.number_input("Capacity Price ($/MW-day)", min_value=0.0, value=cap_price_default, step=10.0, help="Capacity price for MISO/PJM calculations.")

st.sidebar.markdown("---")
st.sidebar.header("🔋 Battery Specifications")
default_power = 250.0 if selected_market == "PJM" else 100.0
power_mw = st.sidebar.number_input("Power Capacity (MW)", min_value=1.0, value=default_power, step=10.0)
duration_hr = st.sidebar.number_input("Duration (Hours)", min_value=1.0, value=4.0, step=1.0)
rte = st.sidebar.slider("Round Trip Efficiency (RTE)", min_value=0.50, max_value=1.00, value=0.88, step=0.01)
max_cycles_per_day = st.sidebar.number_input("Max Cycles per Day", min_value=0.1, value=1.2, step=0.1)
initial_soc_pct = st.sidebar.slider("Initial SoC (%)", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
deg_cost = st.sidebar.number_input("Degradation Cost ($/MWh Discharged)", min_value=0.0, value=5.0, step=0.5)
mileage_factor = st.sidebar.slider("Regulation Mileage Wear Factor", min_value=0.01, max_value=0.50, value=0.10, step=0.01, help="Fraction of regulation capacity translating to cycle wear.")
reg_throughput_factor = st.sidebar.slider("Regulation AGC Throughput Factor", min_value=0.00, max_value=0.50, value=0.15, step=0.01, help="Approximate expected energy throughput (MWh) per cleared MW of regulation capacity per hour.")

# PJM-Specific Variable Tranche Configuration
pjm_user_tranches = []
enable_pjm_tranches = True
if selected_market == "PJM":
    st.sidebar.markdown("---")
    st.sidebar.header("📊 PJM Variable Regulation Tranches")
    enable_pjm_tranches = st.sidebar.checkbox("Enable Classified Tranche Bidding", value=True, help="Divides regulation offers into segmented price-quantity tranches to prevent market self-price suppression.")
    
    if enable_pjm_tranches:
        num_tranches = st.sidebar.number_input("Number of Tranches to Bid", min_value=1, max_value=10, value=3, step=1)
        default_names = ["Tranche 1 (Base)", "Tranche 2 (Mid)", "Tranche 3 (Peak)", "Tranche 4 (Spike)", "Tranche 5 (Super-Peak)"]
        default_mws = [min(50.0, power_mw*0.25), min(50.0, power_mw*0.25), min(25.0, power_mw*0.15), 20.0, 15.0]
        default_rates = [0.0, 30.0, 60.0, 90.0, 120.0]

        total_reg_cap = 0.0
        for i in range(int(num_tranches)):
            d_name = default_names[i] if i < len(default_names) else f"Tranche {i+1}"
            d_mw = default_mws[i] if i < len(default_mws) else 20.0
            d_rate = default_rates[i] if i < len(default_rates) else float(i * 30.0)

            with st.sidebar.expander(f"⚙️ Config for Tranche #{i+1}", expanded=(i < 3)):
                t_name = st.text_input(f"Name #{i+1}", value=d_name, key=f"app_tname_{i}")
                t_mw = st.number_input(f"Capacity MW #{i+1}", min_value=0.0, max_value=float(power_mw), value=float(d_mw), step=5.0, key=f"app_tmw_{i}")
                t_rate = st.number_input(f"Offer Hurdle Rate ($/MW) #{i+1}", min_value=0.0, max_value=1000.0, value=float(d_rate), step=5.0, key=f"app_trate_{i}")
                
                pjm_user_tranches.append({
                    "name": t_name,
                    "mw": t_mw,
                    "hurdle_rate": t_rate
                })
                total_reg_cap += t_mw

        st.sidebar.caption(f"**Total Regulation Max Cap:** {total_reg_cap:.1f} MW / {power_mw:.1f} MW ({total_reg_cap/power_mw*100:.1f}%)")

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

if selected_sim_mode == "Rolling Horizon (Forecast-Driven)":
    st.sidebar.subheader("Forecast Engine Configuration")
    forecast_method = st.sidebar.selectbox("Forecasting Algorithm", options=["naive", "rolling_average", "noisy_actual"], index=0)
    forecast_horizon_hrs = st.sidebar.slider("Look-ahead Horizon (Hours)", min_value=12, max_value=168, value=48, step=12)
    if forecast_method == "noisy_actual":
        forecast_mape = st.sidebar.slider("Forecast Error (MAPE %)", min_value=0, max_value=50, value=15, step=5) / 100.0

# Initialize Session State Data
if 'data_df' not in st.session_state:
    st.session_state['data_df'] = None
if 'data_source' not in st.session_state:
    st.session_state['data_source'] = None
if 'active_market' not in st.session_state:
    st.session_state['active_market'] = None

# --- MAIN PAGE INGESTION ---
st.write(f"### 1. Ingest telemetry data for **{selected_market}**")

# Display required columns for selected market
if selected_market == "ERCOT":
    required_cols_msg = "`timestamp`, `LMP`, `REGUP`, `REGDN`, `RRS`, `NSPIN`, `ECRS`"
elif selected_market == "MISO":
    required_cols_msg = "`timestamp`, `LMP`, `REG_CAP`, `REG_MIL`, `SPIN`, `SUPP`"
elif selected_market == "PJM":
    required_cols_msg = "`timestamp`, `LMP`, `RMCCP`, `RMPCP`, `Mileage`, `Price_SYNCH`, `Price_NONSYNCH` (or `Reg_Price` / `Reg_Effective_Price`)"
else:
    required_cols_msg = "`timestamp`, `LMP`, `Reg_Price`"

st.info(f"Required columns (case-insensitive): {required_cols_msg}")

if selected_market == "PJM":
    tmpl_col1, tmpl_col2 = st.columns([1, 1])
    tmpl_xlsx = os.path.join(os.path.dirname(__file__), "PJM_Market_Template.xlsx")
    tmpl_csv = os.path.join(os.path.dirname(__file__), "PJM_Market_Template.csv")
    with tmpl_col1:
        if os.path.exists(tmpl_xlsx):
            with open(tmpl_xlsx, "rb") as f:
                st.download_button(
                    label="📥 Download PJM Template (.xlsx)",
                    data=f.read(),
                    file_name="PJM_Market_Template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
    with tmpl_col2:
        if os.path.exists(tmpl_csv):
            with open(tmpl_csv, "rb") as f:
                st.download_button(
                    label="📥 Download PJM Template (.csv)",
                    data=f.read(),
                    file_name="PJM_Market_Template.csv",
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
        else:
            adapter = BaseDataAdapter('Generic', ['LMP', 'Reg_Price'], {
                'LMP': ['LMP', 'lmp', 'price'],
                'Reg_Price': ['Reg_Price', 'reg_price', 'regulation']
            })
            
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
            opt = PJM_Optimizer(power_mw=power_mw, enable_tranches=enable_pjm_tranches, tranches=pjm_user_tranches)
        else:
            opt = Generic_Optimizer()
            
        df_synthetic = opt.generate_sample_data(days=365)
        st.session_state['data_df'] = df_synthetic
        st.session_state['data_source'] = f"Synthetic {selected_market} Data"
        st.session_state['active_market'] = selected_market
        st.success(f"Generated 1 year of synthetic {selected_market} pricing!")

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
                    optimizer = PJM_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        capacity_price_mw_day=capacity_price_mw_day,
                        reg_throughput_factor=reg_throughput_factor,
                        is_tolling=is_tolling_flag,
                        enable_tranches=enable_pjm_tranches,
                        tranches=pjm_user_tranches,
                        elcc_factor=elcc_factor
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
                else:
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
                    forecast_mape=forecast_mape
                )
                progress_bar.empty()
                st.success("Optimization Solved Successfully!")

                # --- SUMMARY REPORT METRICS ---
                metrics, utilization = optimizer.calculate_summary_metrics(df_opt)
                
                if selected_mode in ["Merchant Only", "Tolling Agreement"]:
                    metrics['Total Net Merchant Revenue ($)'] -= metrics.get('Static Capacity Revenue ($)', 0.0)
                    metrics['Static Capacity Revenue ($)'] = 0.0
                    df_opt['Capacity_Revenue'] = 0.0
                    df_opt['revenue'] = df_opt['Energy_Revenue'] + df_opt['Ancillary_Revenue'] - df_opt['Total_Degradation_Cost']
                
                st.subheader("📊 Operational & Financial Dashboard")
                
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
                if selected_market == "PJM" and enable_pjm_tranches and pjm_user_tranches:
                    st.markdown("---")
                    st.subheader("📈 PJM Regulation Tranche Breakdown")
                    tranche_cols_ui = st.columns(len(pjm_user_tranches))
                    for idx, tr in enumerate(pjm_user_tranches):
                        t_name = tr['name']
                        avg_cleared = metrics.get(f"Avg Cleared {t_name} (MW)", 0.0)
                        tot_rev = metrics.get(f"Total {t_name} Revenue ($)", 0.0)
                        with tranche_cols_ui[idx]:
                            st.metric(
                                label=f"{t_name} ({tr['mw']} MW @ ${tr['hurdle_rate']}/MW)",
                                value=f"${tot_rev:,.2f}",
                                delta=f"Avg Cleared: {avg_cleared:.1f} MW"
                            )

                # Charts
                st.markdown("---")
                st.subheader("🔋 State of Charge and Prices (HE 1-168 Preview)")
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
                if selected_market == "PJM" and enable_pjm_tranches and pjm_user_tranches:
                    for tr in pjm_user_tranches:
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

                # Build Audit-Ready Formula-Linked Excel Workbook with Dashboard Tab
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
                        
                st.download_button(
                    label="💾 Download Formula-Linked Results & Dashboard (.xlsx)",
                    data=excel_bytes,
                    file_name=f"BESS_Optimized_Dashboard_{selected_market}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                
            except Exception as e:
                st.error(f"Solver Error: {e}")
                import traceback
                st.code(traceback.format_exc())
