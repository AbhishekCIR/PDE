import streamlit as st
import pandas as pd
import numpy as np
import io
from bess_rtc_model import BESS_Simulator

# Configure the Streamlit page
st.set_page_config(page_title="BESS Optimization Engine", page_icon="🔋", layout="wide")

st.title("🔋 Battery Energy Storage System (BESS) Optimizer")
st.markdown("""
Upload a CSV with your hourly constraints (like LMP and Regulation Price), tweak the battery specifications, and run a mathematical MILP optimization to find the maximum possible revenue.
""")

# Sidebar inputs for battery specifications
st.sidebar.header("⚙️ Battery Specifications")
power_mw = st.sidebar.number_input("Power Capacity (MW)", min_value=1.0, value=100.0, step=1.0)
duration_hr = st.sidebar.number_input("Duration (Hours)", min_value=1.0, value=4.0, step=1.0)
rte = st.sidebar.slider("Round Trip Efficiency", min_value=0.50, max_value=1.00, value=0.90, step=0.01)
max_cycles_per_day = st.sidebar.number_input("Max Cycles per Day", min_value=0.1, value=1.0, step=0.1)
initial_soc_pct = st.sidebar.slider("Initial State of Charge (%)", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
deg_cost = st.sidebar.number_input("Degradation Cost ($/MWh)", min_value=0.0, value=5.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.info("Modify these parameters to test different scenarios instantly.")

# Main area: File uploader
st.write("### 1. Upload Hourly Pricing Data")
uploaded_file = st.file_uploader("Upload a CSV or Excel file", type=["csv", "xlsx"])
st.markdown("*Note: Your file must contain columns literally named `timestamp`, `LMP`, and `Reg_Price`.*")

data_df = None

# Logic for handling the CSV or Excel file
if uploaded_file is not None:
    try:
        if uploaded_file.name.endswith('.csv'):
            data_df = pd.read_csv(uploaded_file)
        else:
            data_df = pd.read_excel(uploaded_file)
        # Ensure timestamp is datetime
        if 'timestamp' in data_df.columns:
            data_df['timestamp'] = pd.to_datetime(data_df['timestamp'])
        st.success("File uploaded successfully!")
        
        with st.expander("Preview Uploaded Data"):
            st.dataframe(data_df.head(24), use_container_width=True)
            
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
else:
    st.info("No file uploaded. You can generate a 1-year sample dataset to test the app.")
    if st.button("Generate Sample 1-Year Data"):
        dummy_sim = BESS_Simulator()
        data_df = dummy_sim.generate_sample_data(days=365, freq='1h')
        st.success("Sample data generated! You can preview it below.")
        with st.expander("Preview Sample Data", expanded=True):
            st.dataframe(data_df.head(24), use_container_width=True)

# Run Optimization
st.write("### 2. Run Optimization")
st.write("Once your data is loaded, you can run the MILP engine. This may take up to a minute depending on your computer's speed.")

if data_df is not None:
    if st.button("🚀 Run Optimization", type="primary"):
        with st.spinner("Preparing Optimization Engine..."):
            try:
                # 1. Initialize simulator with sidebar parameters
                simulator = BESS_Simulator(
                    power_mw=power_mw,
                    duration_hr=duration_hr,
                    rte=rte,
                    max_cycles_per_day=max_cycles_per_day,
                    initial_soc_pct=initial_soc_pct,
                    degradation_cost_per_mwh=deg_cost
                )
                
                # 2. Add Progress Bar and Callback
                progress_bar = st.progress(0, text="Solving daily MILP equations...")
                def update_progress(current, total):
                    progress_bar.progress((current + 1) / total, text=f"Solving Optimization: Day {current + 1} of {total}")

                # 3. Run the optimization over the DataFrame
                df_opt = simulator.run_optimization_dispatch(data_df, progress_callback=update_progress)
                
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
                    worksheet.set_column('B:K', 15, num_format)   # Other data
                    
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
                        td = pd.to_datetime(df_opt['timestamp'].iloc[1]) - pd.to_datetime(df_opt['timestamp'].iloc[0])
                        timestep_hours = td.total_seconds() / 3600.0
                    
                    total_discharge = (df_opt['discharge_mw'] * timestep_hours).sum()
                    
                    summary_rows = [
                        ('Total Energy Revenue ($)', metrics.get('Energy Revenue ($)', 0), sum_data_fmt),
                        ('Total Reg Revenue ($)', metrics.get('Regulation Revenue ($)', 0), sum_data_fmt),
                        ('Total Revenue ($)', metrics.get('Total Revenue ($)', 0), sum_data_fmt),
                        ('Total Discharge (MWh)', total_discharge, sum_data_fmt),
                        ('Cycles / Year', metrics.get('Cycles / Year (Annualized)', 0), sum_data_fmt)
                    ]
                    
                    for row_idx, (label, val, fmt) in enumerate(summary_rows, start=1):
                        ws_summary.write(row_idx, 0, label, fmt)
                        ws_summary.write(row_idx, 1, val, fmt)
                
                st.download_button(
                    label="💾 Download Full Results as Excel (.xlsx)",
                    data=excel_buffer.getvalue(),
                    file_name="BESS_Optimized_Results.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                
            except Exception as e:
                st.error(f"An error occurred during optimization: {e}")
