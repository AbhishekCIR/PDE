import os
import pandas as pd
from market_generic import Generic_Optimizer

TEMPLATE_FILE = 'BESS_Template.xlsx'
RESULTS_FILE = 'BESS_Optimized_Results.xlsx'

def create_template():
    print(f"Creating a new template file: {TEMPLATE_FILE}")
    # Default inputs
    inputs_df = pd.DataFrame([
        {'Parameter': 'power_mw', 'Value': 100, 'Description': 'Battery Power Capacity (MW)'},
        {'Parameter': 'duration_hr', 'Value': 4, 'Description': 'Battery Duration (Hours)'},
        {'Parameter': 'rte', 'Value': 0.9, 'Description': 'Round Trip Efficiency (Fraction, 0-1)'},
        {'Parameter': 'max_cycles_per_day', 'Value': 1, 'Description': 'Maximum cycles (Throughput / Energy) per day'},
        {'Parameter': 'initial_soc_pct', 'Value': 0.5, 'Description': 'Initial State of Charge (Fraction, 0-1)'},
        {'Parameter': 'degradation_cost_per_mwh', 'Value': 5.0, 'Description': 'Cost of degradation per MWh discharged ($/MWh)'}
    ])
    
    # Generate 1 full year (8760 hours) of sample data
    dummy_sim = Generic_Optimizer()
    data_df = dummy_sim.generate_sample_data(days=365, freq='1h') # Generate 1 year
    
    with pd.ExcelWriter(TEMPLATE_FILE) as writer:
        inputs_df.to_excel(writer, sheet_name='Inputs', index=False)
        data_df.to_excel(writer, sheet_name='Hourly_Data', index=False)
    
    print(f"{TEMPLATE_FILE} generated. You can now edit the inputs and data inside this file and run the script again!")

def main():
    if not os.path.exists(TEMPLATE_FILE):
        create_template()
        print("Please review and populate the template, then run this script again to optimize.")
        return
        
    print(f"Reading template {TEMPLATE_FILE}...")
    try:
        inputs_df = pd.read_excel(TEMPLATE_FILE, sheet_name='Inputs')
        data_df = pd.read_excel(TEMPLATE_FILE, sheet_name='Hourly_Data')
    except Exception as e:
        print(f"Error reading {TEMPLATE_FILE}: {e}")
        return
        
    # Parse inputs
    # Convert 'Parameter' and 'Value' columns to a dictionary
    inputs_dict = pd.Series(inputs_df.Value.values, index=inputs_df.Parameter).to_dict()
    
    print(f"Initializing BESS Simulator with Inputs from template...")
    try:
        simulator = Generic_Optimizer(
            power_mw=inputs_dict.get('power_mw', 100), 
            duration_hr=inputs_dict.get('duration_hr', 4), 
            rte=inputs_dict.get('rte', 0.9), 
            max_cycles_per_day=inputs_dict.get('max_cycles_per_day', 1), 
            initial_soc_pct=inputs_dict.get('initial_soc_pct', 0.5),
            degradation_cost_per_mwh=inputs_dict.get('degradation_cost_per_mwh', 5.0)
        )
    except Exception as e:
        print("Error initializing parameters. Please ensure 'Inputs' tab parameters are valid numbers.")
        return

    # Ensure timestamp is datetime type
    if 'timestamp' in data_df.columns:
        data_df['timestamp'] = pd.to_datetime(data_df['timestamp'])
    
    # Run the optimization
    print("\n--- Running LP Optimization Dispatch (Day-by-Day MILP) ---")
    df_opt = simulator.run_optimization_dispatch(data_df)
    
    metrics, utilization = simulator.calculate_summary_metrics(df_opt)
    
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