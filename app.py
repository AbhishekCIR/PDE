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
Co-optimize energy arbitrage and ancillary service awards dynamically using Mixed-Integer Linear Programming (MILP). Supports multiple operating modes, rolling-horizon simulations, and externalized market rules.
""")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("🌍 Market Selection")
market_options = ["Generic", "ERCOT", "MISO", "PJM"]
selected_market = st.sidebar.selectbox("Select Market", options=market_options, index=1)

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
    elcc_default = 0.30
    cap_price_default = 120.0
else:
    elcc_default = 1.00
    cap_price_default = 0.0

elcc_factor = st.sidebar.slider("ELCC Capacity Credit Factor", min_value=0.0, max_value=1.0, value=elcc_default, step=0.05, help="Derating fraction for Capacity Market revenue.")
capacity_price_mw_day = st.sidebar.number_input("Capacity Price ($/MW-day)", min_value=0.0, value=cap_price_default, step=10.0, help="Capacity price for MISO/PJM calculations.")

st.sidebar.markdown("---")
st.sidebar.header("🔋 Battery Specifications")
power_mw = st.sidebar.number_input("Power Capacity (MW)", min_value=1.0, value=100.0, step=10.0)
duration_hr = st.sidebar.number_input("Duration (Hours)", min_value=1.0, value=4.0, step=1.0)
rte = st.sidebar.slider("Round Trip Efficiency (RTE)", min_value=0.50, max_value=1.00, value=0.90, step=0.01)
max_cycles_per_day = st.sidebar.number_input("Max Cycles per Day", min_value=0.1, value=1.0, step=0.1)
initial_soc_pct = st.sidebar.slider("Initial SoC (%)", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
deg_cost = st.sidebar.number_input("Degradation Cost ($/MWh Discharged)", min_value=0.0, value=5.0, step=0.5)
mileage_factor = st.sidebar.slider("Regulation Mileage Wear Factor", min_value=0.01, max_value=0.50, value=0.10, step=0.01, help="Fraction of regulation capacity translating to cycle wear.")

st.sidebar.markdown("---")
st.sidebar.header("🎯 Operating Mode")
operating_modes = ["Merchant Only", "Merchant + Capacity", "Merchant + VPP", "Tolling Agreement"]
selected_mode = st.sidebar.selectbox("Select Business Model", options=operating_modes, index=0)

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
    required_cols_msg = "`timestamp`, `LMP`, `RMCCP_A`, `RMPCP_A`, `RMCCP_D`, `RMPCP_D`, `Mileage_RegA`, `Mileage_RegD`, `Price_SYNCH`, `Price_NONSYNCH`"
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
    st.info("No file uploaded. You can generate random synthetic prices for testing.")
    if st.button("🎲 Generate Synthetic Market Data"):
        if selected_market == "ERCOT":
            opt = ERCOT_Optimizer()
        elif selected_market == "MISO":
            opt = MISO_Optimizer()
        elif selected_market == "PJM":
            opt = PJM_Optimizer()
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
    
    if st.button("🚀 Execute Optimization"):
        with st.spinner("Executing MILP solver..."):
            try:
                # 1. Initialize Optimizer with sidebar settings
                if selected_market == "ERCOT":
                    optimizer = ERCOT_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor
                    )
                elif selected_market == "MISO":
                    optimizer = MISO_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        capacity_price_mw_day=capacity_price_mw_day
                    )
                elif selected_market == "PJM":
                    optimizer = PJM_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                        capacity_price_mw_day=capacity_price_mw_day
                    )
                else:
                    optimizer = Generic_Optimizer(
                        power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                        max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                        degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor
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
                
                # Metric Cards
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Net Merchant Revenue", f"${metrics['Total Net Merchant Revenue ($)']:,.2f}")
                c2.metric("Energy Arbitrage Revenue", f"${metrics['Energy Arbitrage Revenue ($)']:,.2f}")
                c3.metric("Ancillary Services Revenue", f"${metrics['Ancillary Services Revenue ($)']:,.2f}")
                c4.metric("Capacity Revenue (Compliance)", f"${metrics['Static Capacity Revenue ($)']:,.2f}")
                
                c5, c6, c7, c8 = st.columns(4)
                c5.metric("Degradation Cost", f"${metrics['Degradation Expense ($)']:,.2f}")
                c6.metric("Equivalent Full Cycles", f"{metrics['Equivalent Full Cycles (EFC)']:,.2f}")
                c7.metric("Achieved Round-Trip Efficiency", f"{metrics['Achieved Round-Trip Efficiency']*100:.1f}%")
                c8.metric("AS Participation Fraction", f"{metrics['Ancillary Participation Fraction']*100:.1f}%")

                st.info(f"Reported Lost Opportunity Cost (LOC): ${metrics['Reported Lost Opportunity Cost ($)']:,.2f} (Hypothetical foregone energy margin due to AS reservations)")

                # State of charge and dispatch tracking chart
                st.subheader("🔋 State of Charge and Prices (HE 1-168 Example)")
                chart_df = df_opt.head(168)[['timestamp', 'soc_mwh', 'LMP', 'charge_mw', 'discharge_mw']].copy()
                
                base_chart = altair.Chart(chart_df).encode(x='timestamp:T')
                
                soc_line = base_chart.mark_line(color='#38BDF8', strokeWidth=3).encode(
                    y=altair.Y('soc_mwh:Q', title='State of Charge (MWh)')
                )
                
                price_line = base_chart.mark_line(color='#F43F5E', strokeWidth=1, strokeDash=[4,4]).encode(
                    y=altair.Y('LMP:Q', title='LMP Price ($/MWh)')
                )
                
                dual_chart = altair.layer(soc_line, price_line).resolve_scale(y='independent').properties(height=400)
                st.altair_chart(dual_chart, use_container_width=True)

                # Dispatch Action Breakdown
                st.subheader("⚡ Hourly Dispatch Profile Preview")
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
                    worksheet.set_column('B:Z', 15, num_format)
                    
                    # Create custom Summary sheet
                    ws_summary = workbook.add_worksheet('Summary')
                    ws_summary.set_column('A:A', 35)
                    ws_summary.set_column('B:B', 20)
                    
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
                    
                    ws_summary.write('A1', 'Metric', sum_header_fmt)
                    ws_summary.write('B1', 'Value', sum_header_fmt)
                    
                    for idx, (label, val) in enumerate(metrics.items(), start=1):
                        ws_summary.write(idx, 0, label, workbook.add_format({'border':1}))
                        ws_summary.write(idx, 1, val, sum_data_fmt)
                        
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
