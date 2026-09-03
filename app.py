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

# Configure the Streamlit page
st.set_page_config(page_title="Multi-Market BESS Optimizer", page_icon="🔋", layout="wide")

st.title("🔋 Battery Energy Storage System (BESS) Multi-Market Optimizer")
st.markdown("""
Co-optimize energy arbitrage and ancillary service awards dynamically using Mixed-Integer Linear Programming (MILP). Supports multiple operating modes, variable regulation tranches, rolling-horizon simulations, and externalized market rules.
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

st.sidebar.markdown("---")
st.sidebar.header("🎲 Synthetic Data Config")
enable_seed = st.sidebar.checkbox("Use Deterministic Random Seed", value=True)
random_seed_val = None
if enable_seed:
    random_seed_val = st.sidebar.number_input("Random Seed Value", min_value=0, value=42, step=1)

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
    required_cols_msg = "`timestamp`, `LMP`, `RMCCP_A`, `RMPCP_A`, `RMCCP_D`, `RMPCP_D`, `Mileage_RegA`, `Mileage_RegD`, `Price_SYNCH`, `Price_NONSYNCH` (or `Reg_Effective_Price` / `Reg_Price`)"
else:
    required_cols_msg = "`timestamp`, `LMP`, `Reg_Price`"

st.info(f"Required columns (case-insensitive): {required_cols_msg}")

uploaded_file = st.file_uploader("Upload Market CSV or Excel file", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        # Load appropriate data adapter
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
            
        df_synthetic = opt.generate_sample_data(days=365, random_seed=random_seed_val)
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
                # 1. Initialize Optimizer with sidebar settings
                is_tolling_flag = (selected_mode == "Tolling Agreement")
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
<<<<<<< Updated upstream
                        reg_throughput_factor=reg_throughput_factor, is_tolling=is_tolling_flag
=======
                        reg_throughput_factor=reg_throughput_factor,
                        enable_tranches=enable_pjm_tranches,
                        tranches=pjm_user_tranches,
                        elcc_factor=elcc_factor
>>>>>>> Stashed changes
                    )
                else:
                    optimizer = Generic_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        reg_throughput_factor=reg_throughput_factor, is_tolling=is_tolling_flag
                    )

                # Set capacity credit derating factors in config dynamically
                optimizer.config['elcc_factor'] = elcc_factor

                # 2. Inject VPP and Tolling constraints into input DataFrame
                data_in = st.session_state['data_df'].copy()
                
                # Apply Tolling constraint: charging prices set to 0 (pass-through charging)
                if selected_mode == "Tolling Agreement":
                    data_in['Charge_LMP'] = 0.0
                else:
                    data_in['Charge_LMP'] = data_in['LMP']
                
                # Apply VPP capacity constraint: reduce power capacity in reserved HE block
                data_in['CAP_LIMIT'] = power_mw
                if selected_mode == "Merchant + VPP":
                    hours = data_in['timestamp'].dt.hour
                    vpp_mask = (hours >= vpp_start_h) & (hours <= vpp_end_h)
                    data_in.loc[vpp_mask, 'CAP_LIMIT'] = max(0.0, power_mw - vpp_reserve_mw)

                # 3. Handle Forecast Engine setup for Rolling Horizon
                forecaster = None
                if selected_sim_mode == "Rolling Horizon (Forecast-Driven)":
                    forecaster = PersistenceForecastEngine(
                        market_name=selected_market,
                        method=forecast_method,
                        mape=forecast_mape
                    )

                # 4. Progress bar callback setup
                progress_bar = st.progress(0, text="Solving LP bounds...")
                def update_progress(current, total):
                    progress_bar.progress((current + 1) / total, text=f"Solving Optimization: step {current+1} of {total}")

                # 5. Solve optimization
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
                
                # If Capacity Revenue is disabled in business model, zero it out and recalculate
                if selected_mode in ["Merchant Only", "Tolling Agreement"]:
                    metrics['Total Net Merchant Revenue ($)'] -= metrics.get('Static Capacity Revenue ($)', 0.0)
                    metrics['Static Capacity Revenue ($)'] = 0.0
                    df_opt['Capacity_Revenue'] = 0.0
                    # Recalculate net hourly revenues
                    df_opt['revenue'] = df_opt['Energy_Revenue'] + df_opt['Ancillary_Revenue'] - df_opt['Total_Degradation_Cost']
                
                st.subheader("📊 Operational & Financial Dashboard")
                
                # Financial Metric Cards
                st.write("**💰 Financial Metrics**")
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Net Merchant Revenue", f"${metrics['Total Net Merchant Revenue ($)']:,.2f}")
                c2.metric("Energy Arbitrage Revenue", f"${metrics['Energy Arbitrage Revenue ($)']:,.2f}")
                c3.metric("Ancillary Services Revenue", f"${metrics['Ancillary Services Revenue ($)']:,.2f}")
<<<<<<< Updated upstream
                c4.metric("Capacity Revenue", f"${metrics['Static Capacity Revenue ($)']:,.2f}")
                c5.metric("Degradation Cost", f"${metrics['Degradation Expense ($)']:,.2f}")
                
                # Physical vs. Arbitrage Metrics
                st.write("**🔋 Physical Battery vs. Scheduled Arbitrage Metrics**")
                c6, c7, c8, c9, c10 = st.columns(5)
                c6.metric("Total Physical EFC", f"{metrics.get('Total EFC', 0.0):,.2f}", help="Total cycles including physical AGC regulation throughput.")
                c7.metric("Arbitrage EFC (Legacy)", f"{metrics['Equivalent Full Cycles (EFC)']:,.2f}", help="Cycles calculated from scheduled arbitrage charging/discharging only.")
                c8.metric("Physical BESS RTE", f"{metrics.get('Physical Round-Trip Efficiency', 0.0)*100:.1f}%", help="True physical round-trip efficiency of the BESS including dynamic AGC signals.")
                c9.metric("Arbitrage RTE (Legacy)", f"{metrics['Achieved Round-Trip Efficiency']*100:.1f}%", help="Efficiency of scheduled arbitrage charging/discharging only.")
                c10.metric("AS Participation Fraction", f"{metrics['Ancillary Participation Fraction']*100:.1f}%")
=======
                c4.metric("Capacity Revenue (Compliance)", f"${metrics['Static Capacity Revenue ($)']:,.2f}")
                
                c5, c6, c7, c8 = st.columns(4)
                c5.metric("Degradation Cost", f"-${metrics['Degradation Expense ($)']:,.2f}")
                c6.metric("Equivalent Full Cycles", f"{metrics['Equivalent Full Cycles (EFC)']:,.2f} cycles")
                c7.metric("Achieved Round-Trip Efficiency", f"{metrics['Achieved Round-Trip Efficiency']*100:.1f}%")
                c8.metric("AS Participation Fraction", f"{metrics['Ancillary Participation Fraction']*100:.1f}%")
>>>>>>> Stashed changes

                # Tranche Breakdown Metrics (if PJM Tranches Active)
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
                                label=f"{t_name} ({tr['mw']} MW max @ ${tr['hurdle_rate']}/MW)",
                                value=f"${tot_rev:,.2f}",
                                delta=f"Avg Cleared: {avg_cleared:.1f} MW"
                            )

                # Detailed metrics expander (Phases 3, 4, 5)
                with st.expander("🔍 Detailed Physical & Operational Analysis", expanded=True):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.write("**🔋 Equivalent Full Cycles (EFC)**")
                        st.metric("Arbitrage EFC", f"{metrics.get('Arbitrage EFC', 0.0):,.2f}")
                        st.metric("AGC EFC", f"{metrics.get('AGC EFC', 0.0):,.2f}")
                        st.metric("Total Physical EFC", f"{metrics.get('Total EFC', 0.0):,.2f}")
                    with col2:
                        st.write("**⚡ Physical Throughput & Efficiency**")
                        st.metric("Arbitrage RTE", f"{metrics.get('Arbitrage Round-Trip Efficiency', 0.0)*100:.1f}%")
                        st.metric("Physical BESS RTE", f"{metrics.get('Physical Round-Trip Efficiency', 0.0)*100:.1f}%")
                        st.metric("Total AGC Throughput", f"{metrics.get('Total AGC Throughput (MWh)', 0.0):,.1f} MWh")
                    with col3:
                        st.write("**📈 Battery Utilization**")
                        avg_soc_val = df_opt['soc_mwh'].mean()
                        st.metric("Average SOC", f"{avg_soc_val:.2f} MWh ({avg_soc_val/(power_mw*duration_hr)*100:.1f}%)" if (power_mw*duration_hr) > 0 else f"{avg_soc_val:.2f} MWh")
                        # Average daily cycles
                        total_days = len(df_opt) / 24.0
                        avg_daily_cycles = metrics.get('Total EFC', 0.0) / total_days if total_days > 0 else 0.0
                        st.metric("Avg Daily Cycles", f"{avg_daily_cycles:.2f}")

                    # AGC & Scheduled Energy details
                    col4, col5 = st.columns(2)
                    with col4:
                        st.write("**⚡ Energy Arbitrage Details**")
                        st.write(f"- **Scheduled Charge:** `{metrics.get('Charging Energy (MWh)', 0.0):,.2f} MWh`")
                        st.write(f"- **Scheduled Discharge:** `{metrics.get('Discharging Energy (MWh)', 0.0):,.2f} MWh`")
                    with col5:
                        st.write("**⚡ Ancillary AGC Details**")
                        st.write(f"- **AGC Charge Throughput:** `{metrics.get('AGC Charge Throughput (MWh)', 0.0):,.2f} MWh`")
                        st.write(f"- **AGC Discharge Throughput:** `{metrics.get('AGC Discharge Throughput (MWh)', 0.0):,.2f} MWh`")
                        # Add hourly metrics
                        reg_hours = 0
                        avg_award = 0.0
                        if selected_market == "PJM":
                            reg_hours = ((df_opt['RegA_MW'] > 1e-3) | (df_opt['RegD_MW'] > 1e-3)).sum()
                            awards = df_opt.loc[(df_opt['RegA_MW'] > 1e-3) | (df_opt['RegD_MW'] > 1e-3), ['RegA_MW', 'RegD_MW']].sum(axis=1)
                            avg_award = awards.mean() if len(awards) > 0 else 0.0
                        elif selected_market == "MISO":
                            reg_hours = (df_opt['REG_MW'] > 1e-3).sum()
                            avg_award = df_opt.loc[df_opt['REG_MW'] > 1e-3, 'REG_MW'].mean() if reg_hours > 0 else 0.0
                        elif selected_market == "ERCOT":
                            reg_hours = ((df_opt['REGUP_MW'] > 1e-3) | (df_opt['REGDN_MW'] > 1e-3)).sum()
                            awards = df_opt.loc[(df_opt['REGUP_MW'] > 1e-3) | (df_opt['REGDN_MW'] > 1e-3), ['REGUP_MW', 'REGDN_MW']].sum(axis=1)
                            avg_award = awards.mean() if len(awards) > 0 else 0.0
                        elif selected_market == "Generic":
                            reg_hours = (df_opt['reg_mw'] > 1e-3).sum()
                            avg_award = df_opt.loc[df_opt['reg_mw'] > 1e-3, 'reg_mw'].mean() if reg_hours > 0 else 0.0
                        
                        st.write(f"- **Regulation Award Hours:** `{reg_hours} hrs ({reg_hours/len(df_opt)*100:.1f}%)`")
                        st.write(f"- **Average Regulation Award:** `{avg_award:.2f} MW`")

                    # SOC Histogram
                    st.write("**📊 SoC Distribution Histogram**")
                    soc_hist_df = pd.DataFrame({'SoC (MWh)': df_opt['soc_mwh']})
                    hist_chart = altair.Chart(soc_hist_df).mark_bar(color='#38BDF8').encode(
                        x=altair.X('SoC (MWh):Q', bin=altair.Bin(maxbins=25), title='SoC (MWh)'),
                        y=altair.Y('count():Q', title='Hours')
                    ).properties(height=180)
                    st.altair_chart(hist_chart, use_container_width=True)

                # State of charge and dispatch tracking chart
                st.markdown("---")
                st.subheader("🔋 State of Charge and Prices (HE 1-168 Preview)")
                preview_len = min(168, len(df_opt))
                chart_df = df_opt.head(preview_len).copy()
                
                # Dynamic Altair Chart
                base_chart = altair.Chart(chart_df).encode(x='timestamp:T')
                
                soc_line = base_chart.mark_line(color='#38BDF8', strokeWidth=3).encode(
                    y=altair.Y('soc_mwh:Q', title='State of Charge (MWh)')
                )
                
                price_line = base_chart.mark_line(color='#F43F5E', strokeWidth=1, strokeDash=[4,4]).encode(
                    y=altair.Y('LMP:Q', title='LMP Price ($/MWh)')
                )
                
                dual_chart = altair.layer(soc_line, price_line).resolve_scale(y='independent').properties(height=380)
                st.altair_chart(dual_chart, use_container_width=True)

                # Stacked Dispatch Chart
                st.subheader("⚡ Hourly Power Dispatch Profile (HE 1-72 Preview)")
                disp_preview = df_opt.head(min(72, len(df_opt))).copy()
                disp_preview['Hour_Label'] = disp_preview['timestamp'].dt.strftime('%m/%d %H:00')
                
                # Identify active dispatch columns
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

                # Dispatch Action Breakdown Table
                st.subheader("📋 Detailed Dispatch Results Preview")
                st.dataframe(df_opt.head(72), use_container_width=True)

                # Excel Spreadsheet Export Build
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                    df_opt.to_excel(writer, index=False, sheet_name='8760_Dispatch_Results')
                    
                    workbook = writer.book
                    worksheet = writer.sheets['8760_Dispatch_Results']
                    
                    # Formatting
                    header_format = workbook.add_format({
                        'bold': True,
                        'border': 1,
                        'bg_color': '#1E293B',
                        'font_color': 'white',
                        'align': 'center'
                    })
                    date_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm', 'border': 1})
                    num_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
                    
                    for col_num, value in enumerate(df_opt.columns.values):
                        worksheet.write(0, col_num, value, header_format)
                        
                    worksheet.set_column('A:A', 20, date_format)
                    worksheet.set_column('B:AZ', 15, num_format)
                    
                    # Create custom Summary sheet
                    ws_summary = workbook.add_worksheet('Summary')
                    ws_summary.set_column('A:A', 38)
                    ws_summary.set_column('B:B', 22)
                    
                    sum_header_fmt = workbook.add_format({
                        'bold': True,
                        'font_color': 'white',
                        'bg_color': '#0284C7',
                        'border': 1
                    })
                    sum_data_fmt = workbook.add_format({
                        'bg_color': '#FFFFE0', # light yellow
                        'num_format': '#,##0.00',
                        'border': 1
                    })
                    
                    ws_summary.write('A1', 'KPI Metric', sum_header_fmt)
                    ws_summary.write('B1', 'Value', sum_header_fmt)
                    
                    for idx, (label, val) in enumerate(metrics.items(), start=1):
                        ws_summary.write(idx, 0, label, workbook.add_format({'border':1}))
                        ws_summary.write(idx, 1, val, sum_data_fmt)

                    # If PJM Tranches were used, write Tranche Settings sheet
                    if selected_market == "PJM" and enable_pjm_tranches and pjm_user_tranches:
                        ws_tr = workbook.add_worksheet('Tranche_Settings')
                        ws_tr.set_column('A:C', 25)
                        tr_header_fmt = workbook.add_format({'bold': True, 'font_color': 'white', 'bg_color': '#10B981', 'border': 1})
                        ws_tr.write('A1', 'Tranche Name', tr_header_fmt)
                        ws_tr.write('B1', 'Capacity (MW)', tr_header_fmt)
                        ws_tr.write('C1', 'Hurdle Rate ($/MW)', tr_header_fmt)
                        for t_i, tr_item in enumerate(pjm_user_tranches, start=1):
                            ws_tr.write(t_i, 0, tr_item['name'], workbook.add_format({'border': 1}))
                            ws_tr.write(t_i, 1, tr_item['mw'], workbook.add_format({'border': 1, 'num_format': '#,##0.00'}))
                            ws_tr.write(t_i, 2, tr_item['hurdle_rate'], workbook.add_format({'border': 1, 'num_format': '$#,##0.00'}))
                        
                st.download_button(
                    label="💾 Download Finalized Results (.xlsx)",
                    data=excel_buffer.getvalue(),
                    file_name=f"BESS_Optimized_Results_{selected_market}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                
            except Exception as e:
                st.error(f"Solver Error: {e}")
                import traceback
                st.code(traceback.format_exc())
