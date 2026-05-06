import streamlit as st
import pandas as pd
import numpy as np
import io
from market_generic import Generic_Optimizer
from market_ercot import ERCOT_Optimizer

# Configure the Streamlit page
st.set_page_config(page_title="BESS Optimization Engine", page_icon="🔋", layout="wide")

st.title("🔋 Battery Energy Storage System (BESS) Optimizer")
st.markdown("""
Upload a CSV with your hourly constraints (like LMP and Regulation Price), tweak the battery specifications, and run a mathematical MILP optimization to find the maximum possible revenue.
""")

# Market Selection
st.sidebar.header("🌍 Optimization Market")
market_options = ["Generic", "ERCOT", "PJM", "NYISO", "MISO"]
selected_market = st.sidebar.selectbox("Select Market", options=market_options, index=0)

# Sidebar inputs for battery specifications
st.sidebar.header("⚙️ Battery Specifications")
power_mw = st.sidebar.number_input("Power Capacity (MW)", min_value=1.0, value=100.0, step=1.0)
duration_hr = st.sidebar.number_input("Duration (Hours)", min_value=1.0, value=4.0, step=1.0)
rte = st.sidebar.slider("Round Trip Efficiency", min_value=0.50, max_value=1.00, value=0.90, step=0.01)
max_cycles_per_day = st.sidebar.number_input("Max Cycles per Day", min_value=0.1, value=1.0, step=0.1)
initial_soc_pct = st.sidebar.slider("Initial State of Charge (%)", min_value=0.0, max_value=1.0, value=0.5, step=0.05)

st.sidebar.markdown("---")
st.sidebar.subheader("Degradation Settings")
deg_cost = st.sidebar.number_input("Degradation Cost ($/MWh)", min_value=0.0, value=5.0, step=0.5)
mileage_factor = st.sidebar.slider("Regulation Mileage Factor", min_value=0.01, max_value=0.50, value=0.10, step=0.01, help="Fraction of 1 MW regulation that translates to degradation throughput.")

st.sidebar.markdown("---")
st.sidebar.info("Modify these parameters to test different scenarios instantly.")

# Initialize session state for data
if 'data_df' not in st.session_state:
    st.session_state['data_df'] = None
if 'data_source' not in st.session_state:
    st.session_state['data_source'] = None
if 'active_market' not in st.session_state:
    st.session_state['active_market'] = None

# Main area: File uploader
st.write(f"### 1. Upload Hourly Pricing Data ({selected_market})")
uploaded_file = st.file_uploader("Upload a CSV or Excel file", type=["csv", "xlsx"])

if selected_market == "ERCOT":
    required_cols_msg = "`timestamp`, `LMP`, `REGUP`, `REGDN`, `RRS`, `NSPIN`, `ECRS`"
    required_cols = ['timestamp', 'LMP', 'REGUP', 'REGDN', 'RRS', 'NSPIN', 'ECRS']
else:
    required_cols_msg = "`timestamp`, `LMP`, `Reg_Price`"
    required_cols = ['timestamp', 'LMP', 'Reg_Price']

st.markdown(f"*Note: For the {selected_market} market, your file should contain columns named {required_cols_msg}. (Case insensitive mapping will be attempted).*")

def standardize_columns(df, target_market):
    # Strip whitespace from column names and make lower case for mapping
    col_mapping = {c.strip().lower(): c for c in df.columns}
    
    # Rename commonly uploaded ERCOT columns to our standard expected names
    rename_dict = {}
    if target_market == "ERCOT":
        if 'deliverydate' in col_mapping and 'deliveryhour' in col_mapping and 'timestamp' not in col_mapping:
            # We will handle timestamp creation outside if needed, but let's see if we can just rename
            pass
            
        mapping_keys = {
            'settlementpointprice': 'LMP',
            'lmp': 'LMP',
            'regup': 'REGUP',
            'regdn': 'REGDN',
            'rrs': 'RRS',
            'nspin': 'NSPIN',
            'ecrs': 'ECRS'
        }
        for k, v in mapping_keys.items():
            if k in col_mapping:
                rename_dict[col_mapping[k]] = v
    else:
        mapping_keys = {
            'settlementpointprice': 'LMP',
            'lmp': 'LMP',
            'reg_price': 'Reg_Price',
            'regulation': 'Reg_Price',
            'regup': 'Reg_Price' # fallback for generic
        }
        for k, v in mapping_keys.items():
            if k in col_mapping:
                rename_dict[col_mapping[k]] = v

    df = df.rename(columns=rename_dict)
    
    # Handle timestamp creation from deliveryDate and deliveryHour if timestamp is missing
    if 'timestamp' not in df.columns:
        if 'deliveryDate' in df.columns and 'deliveryHour' in df.columns:
            # Need to clean deliveryHour (e.g. "24:00:00" to next day 00:00 or "24:00")
            # For simplicity, let's just make a dummy timestamp if it's too complex, or parse it.
            try:
                # Replace 24:00:00 with 00:00:00
                hours = df['deliveryHour'].astype(str).str.replace('24:00:00', '00:00:00').str.replace('24:00', '00:00')
                df['timestamp'] = pd.to_datetime(df['deliveryDate'].astype(str) + ' ' + hours)
                # Add 1 day where hour was 24
                mask_24 = df['deliveryHour'].astype(str).str.contains('24:00')
                if mask_24.any():
                    df.loc[mask_24, 'timestamp'] += pd.Timedelta(days=1)
            except Exception as e:
                st.warning(f"Could not parse deliveryDate/deliveryHour automatically: {e}")
                
    return df

# Logic for handling the CSV or Excel file uploaded manually
if uploaded_file is not None:
    try:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
            
        df = standardize_columns(df, selected_market)
            
        if 'timestamp' in df.columns:
            parsed_ts = pd.to_datetime(df['timestamp'], errors='coerce')
            
            # Check if we should override the timestamp with a standard index
            override_ts = False
            
            # Case 1: Many NaTs (e.g., "8760:00" fails to parse)
            if parsed_ts.isna().sum() > len(df) * 0.1:
                override_ts = True
            # Case 2: Purely numeric (e.g., 1, 2, 3... 8760) 
            elif pd.api.types.is_numeric_dtype(df['timestamp']):
                override_ts = True
            # Case 3: All timestamps parsed to the exact same value (e.g. date without hour)
            elif len(df) > 1 and parsed_ts.nunique() <= 1:
                override_ts = True
                
            if override_ts:
                st.info("Detected non-standard timestamps (e.g., relative hours like '8760:00' or identical dates). Automatically generating a standard hourly datetime sequence starting 2026-01-01.")
                df['timestamp'] = pd.date_range(start='2026-01-01', periods=len(df), freq='h')
            else:
                df['timestamp'] = parsed_ts
            
        st.session_state['data_df'] = df
        st.session_state['data_source'] = f"Your Uploaded File ({selected_market})"
        st.session_state['active_market'] = selected_market
    except Exception as e:
        st.error(f"Error reading File: {e}")
else:
    st.info(f"No file uploaded. You can generate random sample data for {selected_market}.")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🎲 Generate Random 1-Year Data"):
            if selected_market == "ERCOT":
                dummy_sim = ERCOT_Optimizer()
            else:
                dummy_sim = Generic_Optimizer()
                
            df = dummy_sim.generate_sample_data(days=365, freq='1h')
            st.session_state['data_df'] = df
            st.session_state['data_source'] = f"Random Synthetic Data ({selected_market})"
            st.session_state['active_market'] = selected_market

if st.session_state['data_df'] is not None:
    st.success(f"Active Dataset: {st.session_state['data_source']}")
    with st.expander("Preview Active Data", expanded=True):
        st.dataframe(st.session_state['data_df'].head(24), use_container_width=True)

# Run Optimization
st.write("### 2. Run Optimization")
st.write("Once your data is loaded, you can run the MILP engine. This may take up to a minute depending on your computer's speed.")

if st.session_state['data_df'] is not None:
    if st.session_state['active_market'] != selected_market:
        st.warning(f"Your loaded data is for {st.session_state['active_market']}, but you selected {selected_market}. Please re-upload or re-generate data.")
        
    if st.button("🚀 Run Optimization", type="primary"):
        missing_cols = [c for c in required_cols if c not in st.session_state['data_df'].columns]
        if missing_cols:
            st.error(f"Cannot run optimization. Missing required columns for {selected_market} market: {missing_cols}")
        else:
            with st.spinner("Preparing Optimization Engine..."):
                try:
                    # 1. Initialize simulator with sidebar parameters
                    if selected_market == "ERCOT":
                        simulator = ERCOT_Optimizer(
                            power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                            max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                            degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor
                        )
                    else:
                        simulator = Generic_Optimizer(
                            power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                            max_cycles_per_day=max_cycles_per_day, initial_soc_pct=initial_soc_pct,
                            degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor
                        )
                    
                    # 2. Add Progress Bar and Callback
                    progress_bar = st.progress(0, text="Solving daily MILP equations...")
                    def update_progress(current, total):
                        progress_bar.progress((current + 1) / total, text=f"Solving Optimization: Day {current + 1} of {total}")

                    # 3. Run the optimization over the DataFrame
                    df_opt = simulator.run_optimization_dispatch(st.session_state['data_df'], progress_callback=update_progress)
                    
                    # Clear the progress bar after completion
                    progress_bar.empty()
                    
                    # 4. Calculate metrics
                    metrics, utilization = simulator.calculate_summary_metrics(df_opt)
                    
                    # --- DISPLAY RESULTS ---
                    st.success("Optimization Complete!")
                    st.markdown("---")
                    st.header("📊 Optimization Results")
                    
                    # Use columns to display key financial metrics
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Total Revenue", f"${metrics['Total Revenue ($)']:,.0f}")
                    m2.metric("Energy Revenue", f"${metrics['Energy Revenue ($)']:,.0f}")
                    
                    if selected_market == "ERCOT":
                        m3.metric("Ancillary Revenue", f"${metrics['Ancillary Revenue ($)']:,.0f}")
                    else:
                        m3.metric("Reg. Revenue", f"${metrics['Regulation Revenue ($)']:,.0f}")
                        
                    m4.metric("Avg Cycles/Year", f"{metrics['Cycles / Year (Annualized)']:,.1f}")
                    
                    # Display Utilization percentages
                    st.write("")
                    st.subheader("Time Utilization (Mode %)")
                    util_cols = st.columns(len(utilization))
                    for idx, (mode, frac) in enumerate(utilization.items()):
                        util_cols[idx].metric(mode, f"{frac*100:.1f}%")
                    
                    # Preview of the optimized hourly sequence
                    st.subheader("Hourly Dispatch Data (Optimized)")
                    st.dataframe(df_opt, use_container_width=True)
                    
                    # --- PREPARE DATA FOR DOWNLOAD ---
                    if selected_market == "ERCOT":
                        cols_to_keep = ['timestamp', 'LMP', 'REGUP', 'REGDN', 'RRS', 'NSPIN', 'ECRS', 
                                        'charge_mw', 'discharge_mw', 'REGUP_MW', 'REGDN_MW', 'RRS_MW', 'NSPIN_MW', 'ECRS_MW',
                                        'soc_mwh', 'Energy_Revenue', 'REGUP_Revenue', 'REGDN_Revenue', 'RRS_Revenue', 
                                        'NSPIN_Revenue', 'ECRS_Revenue', 'revenue', 'decision']
                    else:
                        cols_to_keep = ['timestamp', 'LMP', 'Reg_Price', 'charge_mw', 'discharge_mw', 
                                        'reg_mw', 'soc_mwh', 'energy_revenue', 'reg_revenue', 'revenue', 'decision']
                    
                    # Check if all desired columns exist, drop extra ones, reorder
                    existing_cols = [c for c in cols_to_keep if c in df_opt.columns]
                    df_export = df_opt[existing_cols]

                    excel_buffer = io.BytesIO()
                    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                        df_export.to_excel(writer, index=False, sheet_name='Optimized_Results')
                        
                        # Format the Excel sheet
                        workbook = writer.book
                        worksheet = writer.sheets['Optimized_Results']
                        
                        # Formats
                        header_format = workbook.add_format({
                            'bold': True,
                            'border': 1,
                            'bg_color': '#D3D3D3',
                            'align': 'center',
                            'valign': 'vcenter'
                        })
                        date_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm', 'border': 1})
                        num_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
                        
                        # Write headers with format
                        for col_num, value in enumerate(df_export.columns.values):
                            worksheet.write(0, col_num, value, header_format)
                            
                        # Write column formatting
                        worksheet.set_column('A:A', 20, date_format)  # timestamp
                        worksheet.set_column('B:W', 15, num_format)   # Other data
                        
                        # --- ADD SUMMARY WORKSHEET ---
                        ws_summary = workbook.add_worksheet('Summary')
                        
                        sum_header_fmt = workbook.add_format({
                            'bold': True,
                            'font_color': 'white',
                            'bg_color': '#00A2E8',  # Vibrant light blue matching image
                            'border': 1
                        })
                        sum_data_fmt = workbook.add_format({
                            'bg_color': '#FFFFE0',  # Light yellow data cell
                            'num_format': '#,##0',
                            'border': 1
                        })
                        
                        ws_summary.set_column('A:A', 25)
                        ws_summary.set_column('B:B', 20)
                        ws_summary.write('A1', 'Metric', sum_header_fmt)
                        ws_summary.write('B1', 'Total Value', sum_header_fmt)
                        
                        timestep_hours = 1.0
                        if len(df_opt) > 1:
                            td = (pd.to_datetime(df_opt['timestamp'].iloc[1]) - pd.to_datetime(df_opt['timestamp'].iloc[0])).total_seconds() / 3600.0
                            if td != 0:
                                timestep_hours = td
                        
                        total_discharge = (df_opt['discharge_mw'] * timestep_hours).sum()
                        
                        summary_rows = [
                            ('Total Revenue ($)', metrics.get('Total Revenue ($)', 0), sum_data_fmt),
                            ('Total Energy Revenue ($)', metrics.get('Energy Revenue ($)', 0), sum_data_fmt),
                        ]
                        
                        if selected_market == "ERCOT":
                            summary_rows.extend([
                                ('Total Ancillary Revenue ($)', metrics.get('Ancillary Revenue ($)', 0), sum_data_fmt),
                                ('Total REGUP Revenue ($)', metrics.get('REGUP Revenue ($)', 0), sum_data_fmt),
                                ('Total REGDN Revenue ($)', metrics.get('REGDN Revenue ($)', 0), sum_data_fmt),
                                ('Total RRS Revenue ($)', metrics.get('RRS Revenue ($)', 0), sum_data_fmt),
                                ('Total NSPIN Revenue ($)', metrics.get('NSPIN Revenue ($)', 0), sum_data_fmt),
                                ('Total ECRS Revenue ($)', metrics.get('ECRS Revenue ($)', 0), sum_data_fmt),
                            ])
                        else:
                            summary_rows.append(
                                ('Total Reg Revenue ($)', metrics.get('Regulation Revenue ($)', 0), sum_data_fmt)
                            )
                            
                        summary_rows.extend([
                            ('Total Discharge (MWh)', total_discharge, sum_data_fmt),
                            ('Cycles / Year', metrics.get('Cycles / Year (Annualized)', 0), sum_data_fmt)
                        ])
                        
                        for row_idx, (label, val, fmt) in enumerate(summary_rows, start=1):
                            ws_summary.write(row_idx, 0, label, fmt)
                            ws_summary.write(row_idx, 1, val, fmt)
                    
                    st.download_button(
                        label="💾 Download Full Results as Excel (.xlsx)",
                        data=excel_buffer.getvalue(),
                        file_name=f"BESS_Optimized_Results_{selected_market}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                    
                except Exception as e:
                    st.error(f"An error occurred during optimization: {e}")
