# main_bridge.py
import os
import sys
import json
import logging
import pandas as pd
import numpy as np
import xlwings as xw

# Import optimizer and adapter classes
from market_ercot import ERCOT_Optimizer
from market_miso import MISO_Optimizer
from market_pjm import PJM_Optimizer
from data_adapters.ercot_adapter import ERCOTDataAdapter
from data_adapters.miso_adapter import MISODataAdapter
from data_adapters.pjm_adapter import PJMDataAdapter
from dashboard.excel_dashboard import generate_dashboard_charts, inject_charts_to_excel

# Setup logging
LOG_FILE = "logs/bess_platform.log"
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("bess_platform_bridge")

def initialize_inputs_block(sheet):
    """
    Initializes the BESS Optimization Configuration settings block in the Input sheet
    starting at row 500 if it doesn't already exist.
    """
    if sheet.range("A500").value == "BESS Optimization Configuration":
        logger.info("Configuration block already exists.")
        return
        
    logger.info("Initializing BESS Optimization Configuration block in Input sheet...")
    
    # Write Headers
    sheet.range("A500").value = "BESS Optimization Configuration"
    sheet.range("A500").api.Font.Bold = True
    # Grey background fill
    sheet.range("A500:F500").color = (219, 223, 230)
    
    # Default values mapping
    config_defaults = [
        ("Market Selection (ERCOT / MISO / PJM)", "ERCOT", "Target energy market for BESS revenue optimization"),
        ("Solver Horizon Mode (Perfect Foresight / Rolling Horizon)", "Perfect Foresight", "Perfect foresight benchmark vs forecast-driven simulation"),
        ("Telemetry Price Year", 2025, "Historical 8760 price year to run (e.g., 2024, 2025)"),
        ("PNode Name / ID Location Override", "SanAngelo", "Pricing node identifier for nodal prices"),
        ("Forecast Uncertainty (MAPE %)", 0.15, "Synthetic lookahead MAPE error percentage (Rolling Horizon only)"),
        ("Operating Mode", "Merchant Only", "Business model constraint: Merchant Only, Merchant + Capacity, Merchant + VPP, Tolling"),
        ("VPP Reserved MW Capacity", 0.0, "Reserved battery capacity for Virtual Power Plant (MW)"),
        ("VPP Start Hour (HE)", 17, "Daily VPP contract block start hour (0-23)"),
        ("VPP End Hour (HE)", 21, "Daily VPP contract block end hour (0-23)"),
        ("Battery Degradation Cost ($/MWh)", 5.0, "Degradation expense penalty per MWh throughput"),
        ("Maximum Daily Cycles Limit", 1.0, "Physical operational limit of cycles per day"),
        ("Regulation AGC Throughput Factor", 0.15, "Expected energy throughput (fraction) per cleared MW of regulation per hour")
    ]
    
    for idx, (label, val, desc) in enumerate(config_defaults, start=1):
        row = 500 + idx
        sheet.range(f"A{row}").value = label
        sheet.range(f"D{row}").value = val
        sheet.range(f"D{row}").color = (255, 255, 224) # light yellow input highlight
        sheet.range(f"E{row}").value = desc
        sheet.range(f"A{row}:E{row}").api.Font.Size = 9
        
    logger.info("Configuration block initialized successfully.")

def execute_from_excel():
    """
    Main entry point triggered by Excel Macro 'Run_BESS_Optimization'.
    Loads configuration, runs optimizer, writes outputs, and refreshes the model.
    """
    logger.info("==================================================")
    logger.info("Executing BESS Optimization from Excel Bridge...")
    logger.info("==================================================")
    
    # Connect to workbook (caller() ensures it controls the spreadsheet that triggered the macro)
    app = None
    try:
        wb = xw.Book.caller()
        excel_interactive = True
        logger.info("Connected to Excel instance via xlwings caller.")
        app = wb.app
    except Exception as e:
        # Fallback for CLI testing
        excel_interactive = False
        excel_name = "260511-FM-SanAngelo_(Solar+BESS)_V1_SV_AK.xlsb"
        logger.info(f"Book.caller() failed. Opening workbook '{excel_name}' directly for CLI testing.")
        app = xw.App(visible=False, add_book=False)
        app.display_alerts = False
        wb = app.books.open(os.path.abspath(excel_name))
        
    # Connect to status bar
    status_bar = wb.app.status_bar if excel_interactive else None
    
    try:
        if status_bar: status_bar = "Reading optimization inputs from 'Input' tab..."
        
        sheet_input = wb.sheets["Input"]
        
        # Initialize block if not present
        initialize_inputs_block(sheet_input)
        
        # Read parameters
        market_name = str(sheet_input.range("D501").value).strip().upper()
        solver_mode = str(sheet_input.range("D502").value).strip()
        price_year = int(sheet_input.range("D503").value or 2025)
        pnode_name = str(sheet_input.range("D504").value).strip()
        forecast_mape = float(sheet_input.range("D505").value or 0.15)
        operating_mode = str(sheet_input.range("D506").value).strip()
        vpp_reserve_mw = float(sheet_input.range("D507").value or 0.0)
        vpp_start_h = int(sheet_input.range("D508").value or 17)
        vpp_end_h = int(sheet_input.range("D509").value or 21)
        deg_cost = float(sheet_input.range("D510").value or 5.0)
        max_cycles = float(sheet_input.range("D511").value or 1.0)
        reg_throughput_factor = float(sheet_input.range("D512").value or 0.15)
        
        # Read battery characteristics from BESS specs section
        power_kw = float(sheet_input.range("D407").value or 300000.0) # nameplate capacity in kW
        duration_hr = float(sheet_input.range("D408").value or 4.0)
        rte = float(sheet_input.range("D411").value or 0.88)          # battery round trip efficiency (fraction)
        
        power_mw = power_kw / 1000.0
        logger.info(f"Loaded Inputs: Market={market_name}, Solver={solver_mode}, Year={price_year}, Mode={operating_mode}")
        logger.info(f"BESS Specs: Power={power_mw}MW, Duration={duration_hr}h, RTE={rte}, Cycles={max_cycles}, DegCost=${deg_cost}, RegThroughput={reg_throughput_factor}")
        
        # Load Telemetry File
        if status_bar: wb.app.status_bar = "Loading historical price telemetry..."
        
        telemetry_filename = f"data/telemetry/{market_name.lower()}_{price_year}_hourly.csv"
        telemetry_path = os.path.abspath(telemetry_filename)
        
        # Initialize Optimizer and Data Adapter
        if market_name == "ERCOT":
            adapter = ERCOTDataAdapter()
            optimizer = ERCOT_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, degradation_cost_per_mwh=deg_cost,
                reg_throughput_factor=reg_throughput_factor
            )
        elif market_name == "MISO":
            adapter = MISODataAdapter()
            # Read MISO zonal capacity price from sheet or use default
            capacity_price_day = float(sheet_input.range("D471").value or 8.0) * 30.0 / 365.0 * 12.0 # convert $/kW-mo to $/MW-day roughly
            optimizer = MISO_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, degradation_cost_per_mwh=deg_cost,
                capacity_price_mw_day=capacity_price_day,
                reg_throughput_factor=reg_throughput_factor
            )
        elif market_name == "PJM":
            adapter = PJMDataAdapter()
            capacity_price_day = float(sheet_input.range("D471").value or 8.0) * 30.0 / 365.0 * 12.0
            optimizer = PJM_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, degradation_cost_per_mwh=deg_cost,
                capacity_price_mw_day=capacity_price_day,
                reg_throughput_factor=reg_throughput_factor
            )
        else:
            raise ValueError(f"Unsupported market: {market_name}")

        # Check telemetry file
        if os.path.exists(telemetry_path):
            logger.info(f"Loading price telemetry from file: {telemetry_path}")
            df_clean, validation_logs = adapter.process(telemetry_path)
            for log in validation_logs:
                logger.info(f"Telemetry Validation: {log}")
        else:
            logger.warning(f"Telemetry file '{telemetry_filename}' not found. Falling back to synthetic pricing data.")
            if status_bar: wb.app.status_bar = "Warning: Price file not found. Generating synthetic pricing..."
            df_synthetic = optimizer.generate_sample_data(days=365)
            df_clean, validation_logs = adapter.process(df_synthetic)
            
        # Apply Operating Mode Constraints
        # A. Tolling Pass-Through: charging costs are set to 0
        if operating_mode == "Tolling Agreement":
            df_clean['Charge_LMP'] = 0.0
        else:
            df_clean['Charge_LMP'] = df_clean['LMP']
            
        # B. VPP capacity reservation limit
        df_clean['CAP_LIMIT'] = power_mw
        if operating_mode == "Merchant + VPP" and vpp_reserve_mw > 0:
            logger.info(f"Applying VPP Contract block: Reserving {vpp_reserve_mw}MW from HE {vpp_start_h} to {vpp_end_h}.")
            hours_series = df_clean['timestamp'].dt.hour
            vpp_mask = (hours_series >= vpp_start_h) & (hours_series <= vpp_end_h)
            df_clean.loc[vpp_mask, 'CAP_LIMIT'] = max(0.0, power_mw - vpp_reserve_mw)
            
        # Execute Optimization
        if status_bar: wb.app.status_bar = f"Solving BESS {market_name} LP equations (Perfect Foresight)..."
        
        # Setup forecast engine if using Rolling Horizon
        forecaster = None
        if solver_mode == "Rolling Horizon":
            from forecast_engine.persistence_forecast import PersistenceForecastEngine
            forecaster = PersistenceForecastEngine(market_name=market_name, method="naive", mape=forecast_mape)
            
        # Solver execution wrapper with progress callback
        def progress_update(current, total):
            if status_bar and current % 10 == 0:
                wb.app.status_bar = f"Solving LP: step {current} of {total} intervals..."
                
        df_opt = optimizer.run_optimization_dispatch(
            df=df_clean,
            progress_callback=progress_update,
            forecast_engine=forecaster,
            forecast_horizon_hrs=48
        )
        
        # Recalculate metrics
        metrics, utilization = optimizer.calculate_summary_metrics(df_opt)
        logger.info(f"Solve completed successfully. Total operational revenue calculated: ${metrics['Total Net Merchant Revenue ($)']:,.2f}")
        
        # --- WRITE OUTPUTS TO EXCEL ---
        if status_bar: wb.app.status_bar = "Writing optimized results to staging sheets..."
        
        # 1. Update Optimized_Results Tab
        sheet_results = wb.sheets["Optimized_Results"]
        sheet_results.range("A9:AW8780").clear_contents() # clear old results block
        
        # Update column headers in row 8
        expected_cols = adapter.expected_cols
        # Output columns in Optimized_Results: Month ($/kW), Arbitrage, Ancillary 1, 2, 3, 4, 5, Total
        # Let's map active headers based on the market products
        headers_m = ["Month ($/kW)", "BESS Arbitrage Revenue"]
        
        # Terminology remap lists
        if market_name == "ERCOT":
            product_labels = ["Regulation UP", "Regulation Down", "Non-Spin", "ECRS", "RRS"]
            rev_cols = ['REGUP_Revenue', 'REGDN_Revenue', 'NSPIN_Revenue', 'ECRS_Revenue', 'RRS_Revenue']
        elif market_name == "MISO":
            product_labels = ["Regulation Capacity", "Regulation Mileage", "Spinning Reserve", "Supplemental Reserve", "N/A"]
            rev_cols = ['REG_CAP_Revenue', 'REG_MIL_Revenue', 'SPIN_Revenue', 'SUPP_Revenue']
        elif market_name == "PJM":
            product_labels = ["RegA CCP", "RegA PCP", "RegD CCP", "RegD PCP", "Synchronized Reserve"]
            rev_cols = ['REG_CAP_A_Rev', 'REG_PERF_A_Rev', 'REG_CAP_D_Rev', 'REG_PERF_D_Rev', 'SPIN_Revenue'] # placeholders
            # PJM revenue maps:
            df_opt['REG_CAP_A_Rev'] = df_opt['RegA_MW'] * df_opt['RMCCP_A'] * timestep_hours
            df_opt['REG_PERF_A_Rev'] = df_opt['RegA_MW'] * df_opt['Mileage_RegA'] * df_opt['RMPCP_A'] * timestep_hours
            df_opt['REG_CAP_D_Rev'] = df_opt['RegD_MW'] * df_opt['RMCCP_D'] * timestep_hours
            df_opt['REG_PERF_D_Rev'] = df_opt['RegD_MW'] * df_opt['Mileage_RegD'] * df_opt['RMPCP_D'] * timestep_hours
            df_opt['SPIN_Revenue'] = df_opt['SYNCH_MW'] * df_opt['Price_SYNCH'] * timestep_hours
        else:
            product_labels = ["Ancillary 1", "Ancillary 2", "Ancillary 3", "Ancillary 4", "Ancillary 5"]
            rev_cols = []
            
        headers_m.extend(product_labels)
        headers_m.append("Total")
        
        # Write headers to Row 8 Column B to I (which is columns 2 to 9)
        sheet_results.range("B8:I8").value = headers_m
        
        # Group df_opt by month to generate monthly staging values
        df_opt['Month'] = df_opt['timestamp'].dt.month
        monthly_gp = df_opt.groupby('Month')
        
        monthly_rows = []
        annual_sums = {
            'Arbitrage': df_opt['Energy_Revenue'].sum()
        }
        for p_idx, col in enumerate(rev_cols):
            annual_sums[product_labels[p_idx]] = df_opt[col].sum() if col in df_opt.columns else 0.0
            
        annual_total = df_opt['Energy_Revenue'].sum() + sum([df_opt[col].sum() for col in rev_cols if col in df_opt.columns])
        
        for m in range(1, 13):
            # Sum columns for the month
            m_df = monthly_gp.get_group(m) if m in monthly_gp.groups else pd.DataFrame()
            
            if not m_df.empty:
                m_arb = m_df['Energy_Revenue'].sum()
                m_revs = [m_df[col].sum() if col in m_df.columns else 0.0 for col in rev_cols]
                # fill up with zeros if less than 5 ancillary products
                while len(m_revs) < 5:
                    m_revs.append(0.0)
                    
                m_total = m_arb + sum(m_revs)
                
                # Scale revenues to $/kW-Month
                m_arb_rate = m_arb / (power_mw * 1000.0)
                m_rev_rates = [r / (power_mw * 1000.0) for r in m_revs]
                m_total_rate = m_total / (power_mw * 1000.0)
                
                # Append row: Month, Arbitrage, Anc1, Anc2, Anc3, Anc4, Anc5, Total
                row_vals = [m, m_arb_rate] + m_rev_rates + [m_total_rate]
                monthly_rows.append(row_vals)
            else:
                monthly_rows.append([m, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
                
        # Write Monthly rates to Optimized_Results rows 9 to 20 columns B to I
        sheet_results.range("B9:I20").value = monthly_rows
        
        # Write Annual Revenue Share to Row 6 (column C to I)
        share_vals = [
            annual_sums['Arbitrage'] / annual_total if annual_total > 0 else 0.0
        ]
        for p in product_labels:
            share_vals.append(annual_sums[p] / annual_total if annual_total > 0 else 0.0)
        share_vals.append(1.0) # total share is 1.0
        sheet_results.range("C6:I6").value = share_vals
        
        # 2. Overwrite Labels in 'Operations' and 'Input' to align terminology
        if status_bar: wb.app.status_bar = "Synchronizing market terminology row labels..."
        
        sheet_ops = wb.sheets["Operations"]
        sheet_ops.api.Unprotect()
        # Update row labels A73:A78 in Operations
        sheet_ops.range("A73").value = "BESS Arbitrage Revenue"
        for idx, label in enumerate(product_labels):
            sheet_ops.range(f"A{74+idx}").value = label
        sheet_ops.api.Protect()
            
        # Update row labels A463:A468 in Input
        sheet_input.range("A463").value = "BESS Arbitrage Revenue ($/kW-Month)"
        for idx, label in enumerate(product_labels):
            sheet_input.range(f"A{464+idx}").value = f"{label} ($/kW-Month)"
            
        # 3. Write Detailed 8760 Audit Trail
        if status_bar: wb.app.status_bar = "Writing 8760 hourly audit trail (hidden tab)..."
        
        # Check if Hourly_Audit_Trail sheet exists, if not create it
        try:
            sheet_audit = wb.sheets["Hourly_Audit_Trail"]
        except Exception:
            sheet_audit = wb.sheets.add("Hourly_Audit_Trail", after=sheet_results)
            
        # Clear sheet
        sheet_audit.clear()
        
        # Format df_opt columns for audit trail
        audit_cols = ['timestamp', 'LMP', 'Charge_LMP', 'charge_mw', 'discharge_mw', 'soc_mwh', 'Energy_Revenue']
        # add active market reserve awards
        if market_name == "ERCOT":
            audit_cols.extend(['REGUP_MW', 'REGDN_MW', 'RRS_MW', 'NSPIN_MW', 'ECRS_MW'])
        elif market_name == "MISO":
            audit_cols.extend(['REG_MW', 'SPIN_MW', 'SUPP_MW'])
        elif market_name == "PJM":
            audit_cols.extend(['RegA_MW', 'RegD_MW', 'SYNCH_MW'])
            
        audit_cols.append('Lost_Opportunity_Cost')
        df_audit = df_opt[audit_cols].copy()
        
        # Convert timestamp to string for writing
        df_audit['timestamp'] = df_audit['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Write in one bulk call
        sheet_audit.range("A1").value = df_audit.columns.tolist()
        sheet_audit.range("A2").value = df_audit.values.tolist()
        sheet_audit.visible = False # hide audit trail sheet to keep model clean for typical users
        
        # 4. Generate & Insert Dashboard Visualizations
        if status_bar: wb.app.status_bar = "Generating and injecting executive dashboard charts..."
        
        chart_paths = generate_dashboard_charts(df_opt, metrics, market_name, output_dir="logs")
        sheet_dash = wb.sheets["Dashboard"]
        sheet_dash.api.Unprotect()
        inject_charts_to_excel(sheet_dash, chart_paths)
        sheet_dash.api.Protect()
        
        # 5. Trigger Calculations & Save
        if status_bar: wb.app.status_bar = "Recalculating Excel financial statements..."
        wb.app.calculate()
        
        if status_bar: wb.app.status_bar = "Saving workbook..."
        wb.save()
        
        logger.info("Excel financial model update completed successfully!")
        if status_bar: wb.app.status_bar = "Optimization and recalculation completed successfully!"
        
    except Exception as e:
        logger.error(f"Error executing optimization bridge: {e}", exc_info=True)
        if status_bar: wb.app.status_bar = f"Error occurred: {str(e)[:50]}"
        raise
    finally:
        if not excel_interactive and 'wb' in locals():
            try:
                wb.close()
            except Exception:
                pass
        if not excel_interactive and app is not None:
            try:
                app.quit()
            except Exception:
                pass

if __name__ == "__main__":
    execute_from_excel()
