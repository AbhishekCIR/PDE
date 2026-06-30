# market_ercot.py
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base

class ERCOT_Optimizer(BESS_Simulator_Base):
    def __init__(self, power_mw=100.0, duration_hr=4.0, rte=0.90, max_cycles_per_day=1.0, 
                 initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, mileage_factor=0.10):
        super().__init__(power_mw, duration_hr, rte, max_cycles_per_day, initial_soc_pct, 
                         degradation_cost_per_mwh, mileage_factor, market_name="ERCOT")

    def generate_sample_data(self, days=365, freq='1h'):
        """Generates synthetic ERCOT prices for 1 year."""
        timestamps = pd.date_range(start="2026-01-01", periods=days * 24, freq=freq)
        df = pd.DataFrame({'timestamp': timestamps})
        
        hours = df['timestamp'].dt.hour
        months = df['timestamp'].dt.month
        summer_mult = np.where((months >= 6) & (months <= 8), 1.5, 1.0)
        
        base_lmp = 30 + 40 * np.sin((hours - 12) * np.pi / 12) * summer_mult
        noise = np.random.normal(0, 5, len(df))
        df['LMP'] = base_lmp + noise
        df['LMP'] = df['LMP'].clip(lower=-20)  # Support negative prices
        
        # Add random spikes
        spike_indices = np.random.choice(df.index, size=int(len(df)*0.03), replace=False)
        df.loc[spike_indices, 'LMP'] += np.random.uniform(50, 250, size=len(spike_indices))
        
        # Synthetic ancillary services
        df['REGUP'] = np.random.lognormal(mean=1.5, sigma=0.5, size=len(df))
        df['REGDN'] = np.random.lognormal(mean=1.2, sigma=0.5, size=len(df))
        df['RRS'] = np.random.lognormal(mean=1.8, sigma=0.6, size=len(df))
        df['NSPIN'] = np.random.lognormal(mean=1.0, sigma=0.5, size=len(df))
        df['ECRS'] = np.random.lognormal(mean=1.3, sigma=0.6, size=len(df))
        
        return df

    def define_market_variables(self, prob, T_day):
        """Defines ERCOT specific LpVariables."""
        regup = pulp.LpVariable.dicts("REGUP", range(T_day), lowBound=0, upBound=self.power_mw)
        regdn = pulp.LpVariable.dicts("REGDN", range(T_day), lowBound=0, upBound=self.power_mw)
        rrs = pulp.LpVariable.dicts("RRS", range(T_day), lowBound=0, upBound=self.power_mw)
        nspin = pulp.LpVariable.dicts("NSPIN", range(T_day), lowBound=0, upBound=self.power_mw)
        ecrs = pulp.LpVariable.dicts("ECRS", range(T_day), lowBound=0, upBound=self.power_mw)
        
        return {
            'regup': regup,
            'regdn': regdn,
            'rrs': rrs,
            'nspin': nspin,
            'ecrs': ecrs
        }

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Adds ERCOT power capacity and reserve SOC reservation constraints."""
        regup = subclass_vars['regup']
        regdn = subclass_vars['regdn']
        rrs = subclass_vars['rrs']
        nspin = subclass_vars['nspin']
        ecrs = subclass_vars['ecrs']
        
        # Reserve durations from config (default to 1.0 or 2.0)
        dur_regup = self.config.get("reserve_durations", {}).get("REGUP", 1.0)
        dur_regdn = self.config.get("reserve_durations", {}).get("REGDN", 1.0)
        dur_rrs = self.config.get("reserve_durations", {}).get("RRS", 1.0)
        dur_nspin = self.config.get("reserve_durations", {}).get("NSPIN", 1.0)
        dur_ecrs = self.config.get("reserve_durations", {}).get("ECRS", 2.0)

        for t in range(T_day):
            # Power Capacity Limits
            prob += d[t] + regup[t] + rrs[t] + nspin[t] + ecrs[t] <= self.power_mw
            prob += c[t] + regdn[t] <= self.power_mw
            
            # State of Charge Reservation Constraints (Sustainability)
            prob += soc[t] >= (ecrs[t] * dur_ecrs + (regup[t] * dur_regup + rrs[t] * dur_rrs + nspin[t] * dur_nspin)) * timestep_hours
            prob += self.energy_mwh - soc[t] >= regdn[t] * dur_regdn * timestep_hours

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective function terms for ERCOT ancillary service revenues."""
        regup = subclass_vars['regup']
        regdn = subclass_vars['regdn']
        rrs = subclass_vars['rrs']
        nspin = subclass_vars['nspin']
        ecrs = subclass_vars['ecrs']
        
        REGUP_p = df_prices['REGUP'].values
        REGDN_p = df_prices['REGDN'].values
        RRS_p = df_prices['RRS'].values
        NSPIN_p = df_prices['NSPIN'].values
        ECRS_p = df_prices['ECRS'].values
        
        as_rev_terms = []
        for t in range(T_day):
            # Clearing revenue
            clearing_rev = (regup[t] * REGUP_p[t] + regdn[t] * REGDN_p[t] + 
                             rrs[t] * RRS_p[t] + nspin[t] * NSPIN_p[t] + ecrs[t] * ECRS_p[t]) * timestep_hours
            
            # Regulation degradation penalty
            reg_deg = (regup[t] + regdn[t]) * timestep_hours * self.deg_cost * self.mileage_factor
            
            as_rev_terms.append(clearing_rev - reg_deg)
            
        return pulp.lpSum(as_rev_terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts cleared variables."""
        return {
            'REGUP_MW': [subclass_vars['regup'][t].varValue for t in day_indices],
            'REGDN_MW': [subclass_vars['regdn'][t].varValue for t in day_indices],
            'RRS_MW': [subclass_vars['rrs'][t].varValue for t in day_indices],
            'NSPIN_MW': [subclass_vars['nspin'][t].varValue for t in day_indices],
            'ECRS_MW': [subclass_vars['ecrs'][t].varValue for t in day_indices]
        }

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates revenue columns post-optimization."""
        df_out['REGUP_Revenue'] = df_out['REGUP_MW'] * df_out['REGUP'] * timestep_hours - df_out['REGUP_MW'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['REGDN_Revenue'] = df_out['REGDN_MW'] * df_out['REGDN'] * timestep_hours - df_out['REGDN_MW'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['RRS_Revenue'] = df_out['RRS_MW'] * df_out['RRS'] * timestep_hours
        df_out['NSPIN_Revenue'] = df_out['NSPIN_MW'] * df_out['NSPIN'] * timestep_hours
        df_out['ECRS_Revenue'] = df_out['ECRS_MW'] * df_out['ECRS'] * timestep_hours
        
        df_out['Ancillary_Revenue'] = (df_out['REGUP_Revenue'] + df_out['REGDN_Revenue'] + 
                                       df_out['RRS_Revenue'] + df_out['NSPIN_Revenue'] + df_out['ECRS_Revenue'])
        
        # In ERCOT, capacity market is 0
        df_out['Capacity_Revenue'] = 0.0
        
        df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + (df_out['REGUP_MW'] + df_out['REGDN_MW']) * timestep_hours * self.deg_cost * self.mileage_factor
        
        # Net operational merchant revenue
        df_out['revenue'] = df_out['Energy_Revenue'] + df_out['Ancillary_Revenue'] - df_out['Total_Degradation_Cost']
        
        return df_out

    def calculate_summary_metrics(self, df_out):
        """Returns financial and operational KPIs for ERCOT."""
        timestep_hours = 1.0
        if len(df_out) > 1:
            td = (df_out['timestamp'].iloc[1] - df_out['timestamp'].iloc[0]).total_seconds() / 3600.0
            if td != 0:
                timestep_hours = td
                
        total_rev = df_out['revenue'].sum()
        energy_rev = df_out['Energy_Revenue'].sum()
        as_rev = df_out['Ancillary_Revenue'].sum()
        regup_rev = df_out['REGUP_Revenue'].sum()
        regdn_rev = df_out['REGDN_Revenue'].sum()
        rrs_rev = df_out['RRS_Revenue'].sum()
        nspin_rev = df_out['NSPIN_Revenue'].sum()
        ecrs_rev = df_out['ECRS_Revenue'].sum()
        deg_expense = df_out['Total_Degradation_Cost'].sum()
        loc_sum = df_out['Lost_Opportunity_Cost'].sum()
        
        # Operational KPIs
        total_discharge_mwh = (df_out['discharge_mw'] * timestep_hours).sum()
        total_charge_mwh = (df_out['charge_mw'] * timestep_hours).sum()
        
        efc = total_discharge_mwh / self.energy_mwh
        
        achieved_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        
        # AS participation fraction (intervals with AS cleared / total intervals)
        as_sum = (df_out['REGUP_MW'] + df_out['REGDN_MW'] + df_out['RRS_MW'] + df_out['NSPIN_MW'] + df_out['ECRS_MW'])
        as_fraction = (as_sum > 1e-3).mean()
        
        # Mode counts
        mode_counts = df_out['decision'].value_counts()
        total_intervals = len(df_out)
        utilization = {k: v/total_intervals for k, v in mode_counts.items()}
        
        metrics = {
            'Total Net Merchant Revenue ($)': total_rev,
            'Energy Arbitrage Revenue ($)': energy_rev,
            'Ancillary Services Revenue ($)': as_rev,
            'REGUP Revenue ($)': regup_rev,
            'REGDN Revenue ($)': regdn_rev,
            'RRS Revenue ($)': rrs_rev,
            'NSPIN Revenue ($)': nspin_rev,
            'ECRS Revenue ($)': ecrs_rev,
            'Static Capacity Revenue ($)': 0.0,
            'Degradation Expense ($)': deg_expense,
            'Reported Lost Opportunity Cost ($)': loc_sum,
            'Equivalent Full Cycles (EFC)': efc,
            'Achieved Round-Trip Efficiency': achieved_rte,
            'Charging Energy (MWh)': total_charge_mwh,
            'Discharging Energy (MWh)': total_discharge_mwh,
            'Ancillary Participation Fraction': as_fraction
        }
        
        return metrics, utilization
