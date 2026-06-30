# run_bess_simulation.py
import os
import pandas as pd
import numpy as np
from market_generic import Generic_Optimizer
from market_ercot import ERCOT_Optimizer
from market_miso import MISO_Optimizer
from market_pjm import PJM_Optimizer
from data_adapters.ercot_adapter import ERCOTDataAdapter
from data_adapters.miso_adapter import MISODataAdapter
from data_adapters.pjm_adapter import PJMDataAdapter
from data_adapters.base_adapter import BaseDataAdapter

TEMPLATE_FILE = 'BESS_Template.xlsx'
RESULTS_FILE = 'BESS_Optimized_Results.xlsx'

def create_template():
    print(f"Creating a new template file: {TEMPLATE_FILE}")
    
    # 1. Inputs sheet
    inputs_df = pd.DataFrame([
        {'Parameter': 'market_name', 'Value': 'ERCOT', 'Description': 'Optimization Market (Generic, ERCOT, MISO, PJM)'},
        {'Parameter': 'power_mw', 'Value': 100.0, 'Description': 'Battery Power Capacity (MW)'},
        {'Parameter': 'duration_hr', 'Value': 4.0, 'Description': 'Battery Duration (Hours)'},
        {'Parameter': 'rte', 'Value': 0.90, 'Description': 'Round Trip Efficiency (Fraction, 0-1)'},
        {'Parameter': 'max_cycles_per_day', 'Value': 1.0, 'Description': 'Maximum cycles (Throughput / Energy) per day'},
        {'Parameter': 'initial_soc_pct', 'Value': 0.50, 'Description': 'Initial State of Charge (Fraction, 0-1)'},
        {'Parameter': 'degradation_cost_per_mwh', 'Value': 5.0, 'Description': 'Cost of degradation per MWh discharged ($/MWh)'},
        {'Parameter': 'mileage_factor', 'Value': 0.10, 'Description': 'Ancillary regulation mileage wear factor'},
        {'Parameter': 'capacity_price_mw_day', 'Value': 50.0, 'Description': 'Zonal capacity clearing price ($/MW-day, MISO/PJM only)'},
        {'Parameter': 'operating_mode', 'Value': 'Merchant Only', 'Description': 'Operating Mode (Merchant Only, Merchant + Capacity, Merchant + VPP, Tolling Agreement)'},
        {'Parameter': 'vpp_reserve_mw', 'Value': 20.0, 'Description': 'Capacity reserved for VPP (MW, Merchant + VPP only)'},
        {'Parameter': 'vpp_start_hour', 'Value': 17, 'Description': 'VPP reservation start hour (0-23, inclusive)'},
        {'Parameter': 'vpp_end_hour', 'Value': 21, 'Description': 'VPP reservation end hour (0-23, inclusive)'}
    ])
    
    # Generate 1 full year (8760 hours) of sample data for each market
    days = 365
    generic_df = Generic_Optimizer().generate_sample_data(days=days, freq='1h')
    ercot_df = ERCOT_Optimizer().generate_sample_data(days=days, freq='1h')
    miso_df = MISO_Optimizer().generate_sample_data(days=days, freq='1h')
    pjm_df = PJM_Optimizer().generate_sample_data(days=days, freq='1h')
    
    with pd.ExcelWriter(TEMPLATE_FILE) as writer:
        inputs_df.to_excel(writer, sheet_name='Inputs', index=False)
        generic_df.to_excel(writer, sheet_name='Data_Generic', index=False)
        ercot_df.to_excel(writer, sheet_name='Data_ERCOT', index=False)
        miso_df.to_excel(writer, sheet_name='Data_MISO', index=False)
        pjm_df.to_excel(writer, sheet_name='Data_PJM', index=False)
    
    print(f"{TEMPLATE_FILE} generated. You can now edit the inputs and data inside this file and run the script again!")

def main():
    if not os.path.exists(TEMPLATE_FILE):
        create_template()
        print("Please review and populate the template, then run this script again to optimize.")
        return
        
    print(f"Reading template {TEMPLATE_FILE}...")
    try:
        inputs_df = pd.read_excel(TEMPLATE_FILE, sheet_name='Inputs')
    except Exception as e:
        print(f"Error reading {TEMPLATE_FILE}: {e}")
        return
        
    # Parse inputs to dict
    inputs_dict = pd.Series(inputs_df.Value.values, index=inputs_df.Parameter).to_dict()
    market_name = str(inputs_dict.get('market_name', 'Generic')).strip().upper()
    
    sheet_name_map = {
        'GENERIC': 'Data_Generic',
        'ERCOT': 'Data_ERCOT',
        'MISO': 'Data_MISO',
        'PJM': 'Data_PJM'
    }
    target_sheet = sheet_name_map.get(market_name, 'Data_Generic')
    
    print(f"Reading data sheet '{target_sheet}'...")
    try:
        data_df = pd.read_excel(TEMPLATE_FILE, sheet_name=target_sheet)
    except Exception as e:
        print(f"Error reading sheet {target_sheet}: {e}. Trying default Hourly_Data...")
        try:
            data_df = pd.read_excel(TEMPLATE_FILE, sheet_name='Hourly_Data')
        except Exception:
            print("Error: Could not find hourly data sheet.")
            return

    # Initialize data adapter and process data
    print(f"Standardizing data for {market_name} market...")
    try:
        if market_name == 'ERCOT':
            adapter = ERCOTDataAdapter()
        elif market_name == 'MISO':
            adapter = MISODataAdapter()
        elif market_name == 'PJM':
            adapter = PJMDataAdapter()
        else:
            adapter = BaseDataAdapter('Generic', ['LMP', 'Reg_Price'], {
                'LMP': ['LMP', 'lmp', 'price'],
                'Reg_Price': ['Reg_Price', 'reg_price', 'regulation']
            })
            
        data_clean, validation_logs = adapter.process(data_df)
        print("Data Ingestion Validation Logs:")
        for log in validation_logs:
            print(f"  {log}")
    except Exception as e:
        print(f"Error processing data with adapter: {e}")
        return
        
    # Initialize Optimizer
    print(f"Initializing {market_name} Optimizer with Inputs...")
    power_mw = float(inputs_dict.get('power_mw', 100.0))
    duration_hr = float(inputs_dict.get('duration_hr', 4.0))
    rte = float(inputs_dict.get('rte', 0.90))
    max_cycles = float(inputs_dict.get('max_cycles_per_day', 1.0))
    initial_soc = float(inputs_dict.get('initial_soc_pct', 0.50))
    deg_cost = float(inputs_dict.get('degradation_cost_per_mwh', 5.0))
    mileage_factor = float(inputs_dict.get('mileage_factor', 0.10))
    capacity_price = float(inputs_dict.get('capacity_price_mw_day', 50.0))
    operating_mode = str(inputs_dict.get('operating_mode', 'Merchant Only')).strip()
    
    try:
        if market_name == 'ERCOT':
            optimizer = ERCOT_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, initial_soc_pct=initial_soc,
                degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor
            )
        elif market_name == 'MISO':
            optimizer = MISO_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, initial_soc_pct=initial_soc,
                degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                capacity_price_mw_day=capacity_price
            )
        elif market_name == 'PJM':
            optimizer = PJM_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, initial_soc_pct=initial_soc,
                degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor,
                capacity_price_mw_day=capacity_price
            )
        else:
            optimizer = Generic_Optimizer(
                power_mw=power_mw, duration_hr=duration_hr, rte=rte,
                max_cycles_per_day=max_cycles, initial_soc_pct=initial_soc,
                degradation_cost_per_mwh=deg_cost, mileage_factor=mileage_factor
            )
    except Exception as e:
        print(f"Error initializing optimizer: {e}")
        return

    # Apply Operating Mode Constraints
    if "Merchant + VPP" in operating_mode:
        vpp_reserve = float(inputs_dict.get('vpp_reserve_mw', 20.0))
        start_h = int(inputs_dict.get('vpp_start_hour', 17))
        end_h = int(inputs_dict.get('vpp_end_hour', 21))
        print(f"Applying VPP Contract Mode: Reserving {vpp_reserve} MW from Hour {start_h} to {end_h} daily.")
        
        # We can implement this in the hourly data sheet by reducing the available power capacity of the battery 
        # or setting up a dynamic variable. For simplicity, during VPP hours, we reduce BESS power capacity 
        # for arbitrage and AS to (power_mw - vpp_reserve), locking in the reserved capacity.
        # Let's adjust self.power_mw dynamically per hour inside our solver, or override it.
        # To do this hourly in solver, let's adjust BESS_Simulator_Base or subclass.
        # Actually, reducing self.power_mw is a simple and clean approximation!
        # But to be precise, we can modify the data_clean to specify the max capacity limit per hour!
        # Let's add a column to data_clean called 'CAP_LIMIT' = power_mw, and for VPP hours, set to (power_mw - vpp_reserve).
        # We will handle this in core_optimizer's solver loop! Let's make sure it checks for 'CAP_LIMIT'.
        # Since 'CAP_LIMIT' is not in data_clean, we can inject it.
        data_clean['CAP_LIMIT'] = power_mw
        hours_series = data_clean['timestamp'].dt.hour
        vpp_mask = (hours_series >= start_h) & (hours_series <= end_h)
        data_clean.loc[vpp_mask, 'CAP_LIMIT'] = max(0.0, power_mw - vpp_reserve)
        
    elif "Tolling Agreement" in operating_mode:
        print("Applying Tolling Agreement Mode: Ignoring battery charging costs (pass-through) and maximizing discharge revenue.")
        # Under a tolling agreement, the battery toller does not pay for charging energy, 
        # so charging price is set to 0 in the solver objective.
        # We can simulate this by setting LMP to 0 when charging.
        # In core_optimizer objective, we have `(d[t] - c[t]) * LMP[t]`. We can rewrite it or pass a charging price.
        # Let's inject a 'Charge_LMP' column in data_clean which is 0 for charging, and keep 'LMP' for discharging!
        # In core_optimizer we will modify the objective to: `d[t] * df_prices['LMP'] - c[t] * df_prices['Charge_LMP']`.
        # This is a very clean way to model it!
        data_clean['Charge_LMP'] = 0.0
    else:
        data_clean['Charge_LMP'] = data_clean['LMP']
        data_clean['CAP_LIMIT'] = power_mw

    # Run the optimization
    print(f"\n--- Running LP Optimization Dispatch (Perfect Foresight) ---")
    df_opt = optimizer.run_optimization_dispatch(data_clean)
    
    metrics, utilization = optimizer.calculate_summary_metrics(df_opt)
    
    print("\n--- Summary Metrics ---")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:,.2f}")
        else:
            print(f"  {k}: {v}")
            
    print("  Utilization (%):")
    for k, v in utilization.items():
        print(f"    {k}: {v*100:.2f}%")
        
    print(f"\nExporting finalized optimized results to {RESULTS_FILE}...")
    
    summary_df = pd.DataFrame(list(metrics.items()), columns=['Metric', 'Value'])
    util_df = pd.DataFrame(list(utilization.items()), columns=['Mode', 'Fraction of Time'])
    
    with pd.ExcelWriter(RESULTS_FILE) as writer:
        summary_df.to_excel(writer, sheet_name='Summary_Metrics', index=False)
        util_df.to_excel(writer, sheet_name='Utilization', index=False)
        df_opt.to_excel(writer, sheet_name='8760_Dispatch_Results', index=False)
        
    print("Done! Check your results file.")

if __name__ == "__main__":
    main()