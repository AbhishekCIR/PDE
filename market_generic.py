# market_generic.py
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base

class Generic_Optimizer(BESS_Simulator_Base):
    def __init__(self, power_mw=100.0, duration_hr=4.0, rte=0.90, max_cycles_per_day=1.0, 
                 initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, mileage_factor=0.10):
        super().__init__(power_mw, duration_hr, rte, max_cycles_per_day, initial_soc_pct, 
                         degradation_cost_per_mwh, mileage_factor, market_name="Generic")

    def generate_sample_data(self, days=365, freq='1h'):
        """Generates synthetic LMP and Regulation price data for 1 year."""
        timestamps = pd.date_range(start="2026-01-01", periods=days * 24, freq=freq)
        df = pd.DataFrame({'timestamp': timestamps})
        
        hours = df['timestamp'].dt.hour
        months = df['timestamp'].dt.month
        summer_mult = np.where((months >= 6) & (months <= 8), 1.5, 1.0)
        
        base_lmp = 30 + 40 * np.sin((hours - 12) * np.pi / 12) * summer_mult
        noise = np.random.normal(0, 5, len(df))
        df['LMP'] = base_lmp + noise
        df['LMP'] = df['LMP'].clip(lower=0)
        
        spike_indices = np.random.choice(df.index, size=int(len(df)*0.03), replace=False)
        df.loc[spike_indices, 'LMP'] += np.random.uniform(50, 200, size=len(spike_indices))
        
        df['Reg_Price'] = np.random.lognormal(mean=2, sigma=0.8, size=len(df))
        
        return df

    def define_market_variables(self, prob, T_day):
        """Defines Generic specific LpVariables."""
        reg = pulp.LpVariable.dicts("Reg", range(T_day), lowBound=0, upBound=self.power_mw)
        return {'reg': reg}

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Adds Generic power capacity and reserve SOC reservation constraints."""
        reg = subclass_vars['reg']
        for t in range(T_day):
            prob += d[t] + reg[t] <= self.power_mw
            prob += c[t] + reg[t] <= self.power_mw
            prob += soc[t] >= reg[t] * timestep_hours
            prob += self.energy_mwh - soc[t] >= reg[t] * timestep_hours

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective function terms for Generic ancillary service revenues."""
        reg = subclass_vars['reg']
        RegPrice = df_prices['Reg_Price'].values
        
        as_rev_terms = []
        for t in range(T_day):
            clearing_rev = reg[t] * RegPrice[t] * timestep_hours
            reg_deg = reg[t] * timestep_hours * self.deg_cost * self.mileage_factor
            as_rev_terms.append(clearing_rev - reg_deg)
            
        return pulp.lpSum(as_rev_terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts cleared variables."""
        return {
            'reg_mw': [subclass_vars['reg'][t].varValue for t in day_indices]
        }

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates revenue columns post-optimization."""
        df_out['reg_revenue'] = df_out['reg_mw'] * df_out['Reg_Price'] * timestep_hours - df_out['reg_mw'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['Ancillary_Revenue'] = df_out['reg_revenue']
        df_out['Capacity_Revenue'] = 0.0
        
        df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + df_out['reg_mw'] * timestep_hours * self.deg_cost * self.mileage_factor
        
        df_out['revenue'] = df_out['Energy_Revenue'] + df_out['Ancillary_Revenue'] - df_out['Total_Degradation_Cost']
        
        return df_out

    def calculate_summary_metrics(self, df_out):
        """Returns financial and operational KPIs for Generic."""
        timestep_hours = 1.0
        if len(df_out) > 1:
            td = (df_out['timestamp'].iloc[1] - df_out['timestamp'].iloc[0]).total_seconds() / 3600.0
            if td != 0:
                timestep_hours = td
                
        total_rev = df_out['revenue'].sum()
        energy_rev = df_out['Energy_Revenue'].sum()
        as_rev = df_out['Ancillary_Revenue'].sum()
        deg_expense = df_out['Total_Degradation_Cost'].sum()
        loc_sum = df_out['Lost_Opportunity_Cost'].sum()
        
        # Operational KPIs
        total_discharge_mwh = (df_out['discharge_mw'] * timestep_hours).sum()
        total_charge_mwh = (df_out['charge_mw'] * timestep_hours).sum()
        
        efc = total_discharge_mwh / self.energy_mwh
        
        achieved_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        
        as_sum = df_out['reg_mw']
        as_fraction = (as_sum > 1e-3).mean()
        
        mode_counts = df_out['decision'].value_counts()
        total_intervals = len(df_out)
        utilization = {k: v/total_intervals for k, v in mode_counts.items()}
        
        metrics = {
            'Total Net Merchant Revenue ($)': total_rev,
            'Energy Arbitrage Revenue ($)': energy_rev,
            'Ancillary Services Revenue ($)': as_rev,
            'Capacity Revenue ($)': 0.0,
            'Degradation Expense ($)': deg_expense,
            'Reported Lost Opportunity Cost ($)': loc_sum,
            'Equivalent Full Cycles (EFC)': efc,
            'Achieved Round-Trip Efficiency': achieved_rte,
            'Charging Energy (MWh)': total_charge_mwh,
            'Discharging Energy (MWh)': total_discharge_mwh,
            'Ancillary Participation Fraction': as_fraction
        }
        
        return metrics, utilization
