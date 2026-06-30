# market_miso.py
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base

class MISO_Optimizer(BESS_Simulator_Base):
    def __init__(self, power_mw=100.0, duration_hr=4.0, rte=0.90, max_cycles_per_day=1.0, 
                 initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, mileage_factor=0.10,
                 capacity_price_mw_day=50.0):
        super().__init__(power_mw, duration_hr, rte, max_cycles_per_day, initial_soc_pct, 
                         degradation_cost_per_mwh, mileage_factor, market_name="MISO")
        
        self.m_to_c_ratio = self.config.get("m_to_c_ratio", 7.2)
        self.capacity_price_mw_day = capacity_price_mw_day

    def generate_sample_data(self, days=365, freq='1h'):
        """Generates synthetic MISO prices for 1 year."""
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
        
        # MISO synthetic ancillary services
        df['REG_CAP'] = np.random.lognormal(mean=1.5, sigma=0.5, size=len(df))
        df['REG_MIL'] = np.random.lognormal(mean=0.8, sigma=0.4, size=len(df))
        df['SPIN'] = np.random.lognormal(mean=1.1, sigma=0.4, size=len(df))
        df['SUPP'] = np.random.lognormal(mean=0.8, sigma=0.3, size=len(df))
        
        return df

    def define_market_variables(self, prob, T_day):
        """Defines MISO specific LpVariables."""
        reg = pulp.LpVariable.dicts("REG", range(T_day), lowBound=0, upBound=self.power_mw)
        spin = pulp.LpVariable.dicts("SPIN", range(T_day), lowBound=0, upBound=self.power_mw)
        supp = pulp.LpVariable.dicts("SUPP", range(T_day), lowBound=0, upBound=self.power_mw)
        
        return {
            'reg': reg,
            'spin': spin,
            'supp': supp
        }

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Adds MISO power capacity and reserve SOC reservation constraints."""
        reg = subclass_vars['reg']
        spin = subclass_vars['spin']
        supp = subclass_vars['supp']
        
        # Reserve durations from config (default to 1.0 hour)
        dur_reg = self.config.get("reserve_durations", {}).get("REG", 1.0)
        dur_spin = self.config.get("reserve_durations", {}).get("SPIN", 1.0)
        dur_supp = self.config.get("reserve_durations", {}).get("SUPP", 1.0)

        for t in range(T_day):
            # Power Capacity Limits (MISO regulation is bidirectional, so it binds both charging and discharging)
            prob += d[t] + reg[t] + spin[t] + supp[t] <= self.power_mw
            prob += c[t] + reg[t] <= self.power_mw
            
            # State of Charge Reservation Constraints (Sustainability)
            prob += soc[t] >= (reg[t] * dur_reg + spin[t] * dur_spin + supp[t] * dur_supp) * timestep_hours
            prob += self.energy_mwh - soc[t] >= reg[t] * dur_reg * timestep_hours

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective function terms for MISO ancillary service revenues."""
        reg = subclass_vars['reg']
        spin = subclass_vars['spin']
        supp = subclass_vars['supp']
        
        REG_CAP_p = df_prices['REG_CAP'].values
        REG_MIL_p = df_prices['REG_MIL'].values
        SPIN_p = df_prices['SPIN'].values
        SUPP_p = df_prices['SUPP'].values
        
        as_rev_terms = []
        for t in range(T_day):
            # Clearing revenue: capacity + mileage
            clearing_rev = (reg[t] * REG_CAP_p[t] + reg[t] * self.m_to_c_ratio * REG_MIL_p[t] + 
                             spin[t] * SPIN_p[t] + supp[t] * SUPP_p[t]) * timestep_hours
            
            # Regulation degradation penalty
            reg_deg = reg[t] * timestep_hours * self.deg_cost * self.mileage_factor
            
            as_rev_terms.append(clearing_rev - reg_deg)
            
        return pulp.lpSum(as_rev_terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts cleared variables."""
        return {
            'REG_MW': [subclass_vars['reg'][t].varValue for t in day_indices],
            'SPIN_MW': [subclass_vars['spin'][t].varValue for t in day_indices],
            'SUPP_MW': [subclass_vars['supp'][t].varValue for t in day_indices]
        }

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates revenue columns post-optimization."""
        df_out['REG_CAP_Revenue'] = df_out['REG_MW'] * df_out['REG_CAP'] * timestep_hours
        df_out['REG_MIL_Revenue'] = df_out['REG_MW'] * self.m_to_c_ratio * df_out['REG_MIL'] * timestep_hours
        df_out['REG_Revenue'] = df_out['REG_CAP_Revenue'] + df_out['REG_MIL_Revenue'] - df_out['REG_MW'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['SPIN_Revenue'] = df_out['SPIN_MW'] * df_out['SPIN'] * timestep_hours
        df_out['SUPP_Revenue'] = df_out['SUPP_MW'] * df_out['SUPP'] * timestep_hours
        
        df_out['Ancillary_Revenue'] = df_out['REG_Revenue'] + df_out['SPIN_Revenue'] + df_out['SUPP_Revenue']
        
        # MISO Planning Resource Auction (PRA) Capacity Revenue
        # Distributed evenly across all hourly timesteps
        elcc = self.config.get("elcc_factor", 0.50)
        hourly_capacity_rate = (self.power_mw * elcc * self.capacity_price_mw_day) / 24.0
        df_out['Capacity_Revenue'] = hourly_capacity_rate * timestep_hours
        
        df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + df_out['REG_MW'] * timestep_hours * self.deg_cost * self.mileage_factor
        
        # Net operational merchant revenue
        df_out['revenue'] = (df_out['Energy_Revenue'] + df_out['Ancillary_Revenue'] + 
                             df_out['Capacity_Revenue'] - df_out['Total_Degradation_Cost'])
        
        return df_out

    def calculate_summary_metrics(self, df_out):
        """Returns financial and operational KPIs for MISO."""
        timestep_hours = 1.0
        if len(df_out) > 1:
            td = (df_out['timestamp'].iloc[1] - df_out['timestamp'].iloc[0]).total_seconds() / 3600.0
            if td != 0:
                timestep_hours = td
                
        total_rev = df_out['revenue'].sum()
        energy_rev = df_out['Energy_Revenue'].sum()
        as_rev = df_out['Ancillary_Revenue'].sum()
        reg_rev = df_out['REG_Revenue'].sum()
        spin_rev = df_out['SPIN_Revenue'].sum()
        supp_rev = df_out['SUPP_Revenue'].sum()
        capacity_rev = df_out['Capacity_Revenue'].sum()
        deg_expense = df_out['Total_Degradation_Cost'].sum()
        loc_sum = df_out['Lost_Opportunity_Cost'].sum()
        
        # Operational KPIs
        total_discharge_mwh = (df_out['discharge_mw'] * timestep_hours).sum()
        total_charge_mwh = (df_out['charge_mw'] * timestep_hours).sum()
        
        efc = total_discharge_mwh / self.energy_mwh
        
        achieved_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        
        as_sum = (df_out['REG_MW'] + df_out['SPIN_MW'] + df_out['SUPP_MW'])
        as_fraction = (as_sum > 1e-3).mean()
        
        mode_counts = df_out['decision'].value_counts()
        total_intervals = len(df_out)
        utilization = {k: v/total_intervals for k, v in mode_counts.items()}
        
        metrics = {
            'Total Net Merchant Revenue ($)': total_rev,
            'Energy Arbitrage Revenue ($)': energy_rev,
            'Ancillary Services Revenue ($)': as_rev,
            'REG Revenue ($)': reg_rev,
            'SPIN Revenue ($)': spin_rev,
            'SUPP Revenue ($)': supp_rev,
            'Static Capacity Revenue ($)': capacity_rev,
            'Degradation Expense ($)': deg_expense,
            'Reported Lost Opportunity Cost ($)': loc_sum,
            'Equivalent Full Cycles (EFC)': efc,
            'Achieved Round-Trip Efficiency': achieved_rte,
            'Charging Energy (MWh)': total_charge_mwh,
            'Discharging Energy (MWh)': total_discharge_mwh,
            'Ancillary Participation Fraction': as_fraction
        }
        
        return metrics, utilization
