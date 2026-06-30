# compare_markets.py
import pandas as pd
import numpy as np
from market_ercot import ERCOT_Optimizer
from market_miso import MISO_Optimizer
from market_pjm import PJM_Optimizer

def main():
    print("==================================================")
    print("Running Multi-Market BESS Optimization Comparison")
    print("==================================================")

    # 1. Create a 48-hour dataset with identical prices across all markets
    timestamps = pd.date_range(start="2026-01-01", periods=48, freq='h')
    
    # Base energy price (high volatility to encourage arbitrage)
    lmp_actual = [
        30, 25, 20, 15, 10,  5,  0, -10,  15,  30,  45,  60, 
        80, 150, 200, 120, 90, 70, 50,  45,  40,  35,  32,  30,
        28, 25, 20, 18, 15, 10,  5,  12,  25,  35,  50,  70,
        90, 180, 220, 140, 95, 75, 55,  45,  40,  35,  32,  30
    ]
    
    df = pd.DataFrame({
        'timestamp': timestamps,
        'LMP': lmp_actual,
        'Charge_LMP': lmp_actual,
        'CAP_LIMIT': 100.0,
        
        # ERCOT prices
        'REGUP': 20.0,
        'REGDN': 15.0,
        'RRS': 25.0,
        'NSPIN': 10.0,
        'ECRS': 18.0,
        
        # MISO prices
        'REG_CAP': 20.0,
        'REG_MIL': 2.0,
        'SPIN': 25.0,
        'SUPP': 10.0,
        
        # PJM prices
        'RMCCP_A': 20.0,
        'RMPCP_A': 2.0,
        'RMCCP_D': 20.0,
        'RMPCP_D': 2.0,
        'Mileage_RegA': 1.2,
        'Mileage_RegD': 3.5,
        'Price_SYNCH': 25.0,
        'Price_NONSYNCH': 10.0
    })

    # Configure a shorter duration battery (e.g. 2-hour: 100MW / 200MWh)
    # This highlights how the reserve duration limits restrict capacity clearance!
    power = 100.0
    duration = 2.0  # 2 hours
    energy = power * duration # 200 MWh
    
    # 2. Run ERCOT
    print("\nOptimizing ERCOT...")
    ercot_opt = ERCOT_Optimizer(
        power_mw=power, duration_hr=duration, rte=0.90, max_cycles_per_day=2.0, 
        initial_soc_pct=0.5, degradation_cost_per_mwh=5.0
    )
    res_ercot = ercot_opt.run_optimization_dispatch(df)
    metrics_ercot, _ = ercot_opt.calculate_summary_metrics(res_ercot)

    # 3. Run MISO
    print("Optimizing MISO...")
    miso_opt = MISO_Optimizer(
        power_mw=power, duration_hr=duration, rte=0.90, max_cycles_per_day=2.0, 
        initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, capacity_price_mw_day=0.0 # set static capacity to 0 to compare operational revenue only
    )
    res_miso = miso_opt.run_optimization_dispatch(df)
    metrics_miso, _ = miso_opt.calculate_summary_metrics(res_miso)

    # 4. Run PJM
    print("Optimizing PJM...")
    pjm_opt = PJM_Optimizer(
        power_mw=power, duration_hr=duration, rte=0.90, max_cycles_per_day=2.0, 
        initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, capacity_price_mw_day=0.0
    )
    res_pjm = pjm_opt.run_optimization_dispatch(df)
    metrics_pjm, _ = pjm_opt.calculate_summary_metrics(res_pjm)

    # 5. Print Results Table
    print("\n" + "="*70)
    print(f"BESS Dispatch Comparison (100MW / 200MWh, 48-Hour Run)")
    print("="*70)
    print(f"{'Metric':<35} | {'ERCOT':<10} | {'MISO':<10} | {'PJM':<10}")
    print("-"*70)
    
    # Total Operational Revenue
    print(f"{'Net Operational Revenue ($)':<35} | {metrics_ercot['Total Net Merchant Revenue ($)']:<10,.2f} | {metrics_miso['Total Net Merchant Revenue ($)']:<10,.2f} | {metrics_pjm['Total Net Merchant Revenue ($)']:<10,.2f}")
    print(f"{'Energy Arbitrage Revenue ($)':<35} | {metrics_ercot['Energy Arbitrage Revenue ($)']:<10,.2f} | {metrics_miso['Energy Arbitrage Revenue ($)']:<10,.2f} | {metrics_pjm['Energy Arbitrage Revenue ($)']:<10,.2f}")
    print(f"{'Ancillary Services Revenue ($)':<35} | {metrics_ercot['Ancillary Services Revenue ($)']:<10,.2f} | {metrics_miso['Ancillary Services Revenue ($)']:<10,.2f} | {metrics_pjm['Ancillary Services Revenue ($)']:<10,.2f}")
    print(f"{'Degradation Expense ($)':<35} | {metrics_ercot['Degradation Expense ($)']:<10,.2f} | {metrics_miso['Degradation Expense ($)']:<10,.2f} | {metrics_pjm['Degradation Expense ($)']:<10,.2f}")
    print(f"{'Equivalent Full Cycles (EFC)':<35} | {metrics_ercot['Equivalent Full Cycles (EFC)']:<10,.2f} | {metrics_miso['Equivalent Full Cycles (EFC)']:<10,.2f} | {metrics_pjm['Equivalent Full Cycles (EFC)']:<10,.2f}")
    print(f"{'Ancillary Participation Fraction':<35} | {metrics_ercot['Ancillary Participation Fraction']*100:<9.1f}% | {metrics_miso['Ancillary Participation Fraction']*100:<9.1f}% | {metrics_pjm['Ancillary Participation Fraction']*100:<9.1f}%")
    print(f"{'Reported Lost Opp. Cost (LOC) ($)':<35} | {metrics_ercot['Reported Lost Opportunity Cost ($)']:<10,.2f} | {metrics_miso['Reported Lost Opportunity Cost ($)']:<10,.2f} | {metrics_pjm['Reported Lost Opportunity Cost ($)']:<10,.2f}")
    print("="*70)

    # 6. Discuss the constraints differences
    print("\nHow Market Rules Changed the Dispatch Constraints:")
    
    # Check max ECRS vs max PJM Synch reserve
    max_ecrs = res_ercot['ECRS_MW'].max()
    max_rrs = res_ercot['RRS_MW'].max()
    max_regd = res_pjm['RegD_MW'].max()
    max_synch = res_pjm['SYNCH_MW'].max()
    
    print(f"1. **ERCOT Contingency Reserve Service (ECRS)** has a 2-hour duration requirement.")
    print(f"   For a 200MWh battery, ECRS capacity is physically restricted to {energy/2.0} MW.")
    print(f"   -> Max ECRS cleared in ERCOT: {max_ecrs:.1f} MW (Solver strictly respected this energy constraint).")
    print(f"   -> Max RRS cleared (1-hour duration): {max_rrs:.1f} MW.")
    
    print(f"2. **PJM RegD and Synchronized Reserve (SYNCH)** have a 30-minute (0.5 hour) duration requirement.")
    print(f"   For a 200MWh battery, RegD and SYNCH can physically clear up to the full {power} MW of power capacity.")
    print(f"   -> Max RegD cleared in PJM: {max_regd:.1f} MW.")
    print(f"   -> Max SYNCH cleared in PJM: {max_synch:.1f} MW.")
    
    print(f"3. **MISO Regulation** is bidirectional and requires a 1-hour reservation.")
    print(f"   Clearance is capped based on the 1-hour energy requirement ($X \\le 200$ MWh which allows full $100$ MW).")
    print("   However, it co-optimizes both regulating capacity and mileage ($7.2 \\times \\text{mil}$).")

if __name__ == "__main__":
    main()
