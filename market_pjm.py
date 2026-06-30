# market_pjm.py
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base

class PJM_Optimizer(BESS_Simulator_Base):
    def __init__(self, power_mw=100.0, duration_hr=4.0, rte=0.90, max_cycles_per_day=1.0, 
                 initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, mileage_factor=0.10,
                 capacity_price_mw_day=120.0):
        super().__init__(power_mw, duration_hr, rte, max_cycles_per_day, initial_soc_pct, 
                         degradation_cost_per_mwh, mileage_factor, market_name="PJM")
        
        self.capacity_price_mw_day = capacity_price_mw_day

    def generate_sample_data(self, days=365, freq='1h'):
        """Generates synthetic PJM prices for 1 year."""
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
        
        # PJM synthetic ancillary services (Capability and Performance prices)
        df['RMCCP_A'] = np.random.lognormal(mean=1.2, sigma=0.4, size=len(df))
        df['RMPCP_A'] = np.random.lognormal(mean=0.5, sigma=0.3, size=len(df))
        df['RMCCP_D'] = np.random.lognormal(mean=1.6, sigma=0.5, size=len(df))
        df['RMPCP_D'] = np.random.lognormal(mean=0.7, sigma=0.3, size=len(df))
        
        # Mileage ratios
        mileage_a = self.config.get("default_mileage", {}).get("RegA", 1.2)
        mileage_d = self.config.get("default_mileage", {}).get("RegD", 3.5)
        df['Mileage_RegA'] = np.clip(np.random.normal(mileage_a, 0.1, len(df)), 0.5, None)
        df['Mileage_RegD'] = np.clip(np.random.normal(mileage_d, 0.3, len(df)), 1.5, None)
        
        # Reserves
        df['Price_SYNCH'] = np.random.lognormal(mean=1.0, sigma=0.4, size=len(df))
        df['Price_NONSYNCH'] = np.random.lognormal(mean=0.7, sigma=0.3, size=len(df))
        
        return df

    def define_market_variables(self, prob, T_day):
        """Defines PJM specific LpVariables."""
        regA = pulp.LpVariable.dicts("RegA", range(T_day), lowBound=0, upBound=self.power_mw)
        regD = pulp.LpVariable.dicts("RegD", range(T_day), lowBound=0, upBound=self.power_mw)
        synch = pulp.LpVariable.dicts("SYNCH", range(T_day), lowBound=0, upBound=self.power_mw)
        nonsynch = pulp.LpVariable.dicts("NONSYNCH", range(T_day), lowBound=0, upBound=self.power_mw)
        
        # RegA and RegD are mutually exclusive per hour
        u_regA = pulp.LpVariable.dicts("u_regA", range(T_day), cat='Binary')
        u_regD = pulp.LpVariable.dicts("u_regD", range(T_day), cat='Binary')
        
        for t in range(T_day):
            prob += regA[t] <= self.power_mw * u_regA[t]
            prob += regD[t] <= self.power_mw * u_regD[t]
            prob += u_regA[t] + u_regD[t] <= 1
            
        return {
            'regA': regA,
            'regD': regD,
            'synch': synch,
            'nonsynch': nonsynch
        }

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Adds PJM power capacity and reserve SOC reservation constraints."""
        regA = subclass_vars['regA']
        regD = subclass_vars['regD']
        synch = subclass_vars['synch']
        nonsynch = subclass_vars['nonsynch']
        
        # Reserve durations from config (default to 0.5 or 1.0 hour)
        dur_rega = self.config.get("reserve_durations", {}).get("RegA", 1.0)
        dur_regd = self.config.get("reserve_durations", {}).get("RegD", 0.5)
        dur_synch = self.config.get("reserve_durations", {}).get("SYNCH", 0.5)
        dur_nonsynch = self.config.get("reserve_durations", {}).get("NONSYNCH", 0.5)

        for t in range(T_day):
            # Power Capacity Limits
            prob += d[t] + regA[t] + regD[t] + synch[t] + nonsynch[t] <= self.power_mw
            prob += c[t] + regA[t] + regD[t] <= self.power_mw
            
            # State of Charge Reservation Constraints (Sustainability)
            prob += soc[t] >= (regA[t] * dur_rega + regD[t] * dur_regd + synch[t] * dur_synch + nonsynch[t] * dur_nonsynch) * timestep_hours
            prob += self.energy_mwh - soc[t] >= (regA[t] * dur_rega + regD[t] * dur_regd) * timestep_hours

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective function terms for PJM ancillary service revenues."""
        regA = subclass_vars['regA']
        regD = subclass_vars['regD']
        synch = subclass_vars['synch']
        nonsynch = subclass_vars['nonsynch']
        
        RMCCP_A = df_prices['RMCCP_A'].values
        RMPCP_A = df_prices['RMPCP_A'].values
        RMCCP_D = df_prices['RMCCP_D'].values
        RMPCP_D = df_prices['RMPCP_D'].values
        Mileage_RegA = df_prices['Mileage_RegA'].values
        Mileage_RegD = df_prices['Mileage_RegD'].values
        Price_SYNCH = df_prices['Price_SYNCH'].values
        Price_NONSYNCH = df_prices['Price_NONSYNCH'].values
        
        perf_a = self.config.get("default_performance_score", {}).get("RegA", 0.90)
        perf_d = self.config.get("default_performance_score", {}).get("RegD", 0.95)
        
        as_rev_terms = []
        for t in range(T_day):
            # RegA Revenue: Capability + Performance
            rega_rev = regA[t] * (RMCCP_A[t] * perf_a + RMPCP_A[t] * Mileage_RegA[t] * perf_a) * timestep_hours
            # RegD Revenue: Capability + Performance
            regd_rev = regD[t] * (RMCCP_D[t] * perf_d + RMPCP_D[t] * Mileage_RegD[t] * perf_d) * timestep_hours
            
            # Reserves Revenue
            synch_rev = synch[t] * Price_SYNCH[t] * timestep_hours
            nonsynch_rev = nonsynch[t] * Price_NONSYNCH[t] * timestep_hours
            
            # Regulation degradation penalty (scaled by mileage)
            rega_deg = regA[t] * Mileage_RegA[t] * timestep_hours * self.deg_cost * self.mileage_factor
            regd_deg = regD[t] * Mileage_RegD[t] * timestep_hours * self.deg_cost * self.mileage_factor
            
            as_rev_terms.append(rega_rev + regd_rev + synch_rev + nonsynch_rev - (rega_deg + regd_deg))
            
        return pulp.lpSum(as_rev_terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts cleared variables."""
        return {
            'RegA_MW': [subclass_vars['regA'][t].varValue for t in day_indices],
            'RegD_MW': [subclass_vars['regD'][t].varValue for t in day_indices],
            'SYNCH_MW': [subclass_vars['synch'][t].varValue for t in day_indices],
            'NONSYNCH_MW': [subclass_vars['nonsynch'][t].varValue for t in day_indices]
        }

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates revenue columns post-optimization."""
        perf_a = self.config.get("default_performance_score", {}).get("RegA", 0.90)
        perf_d = self.config.get("default_performance_score", {}).get("RegD", 0.95)
        
        df_out['RegA_Revenue'] = df_out['RegA_MW'] * (df_out['RMCCP_A'] * perf_a + df_out['RMPCP_A'] * df_out['Mileage_RegA'] * perf_a) * timestep_hours - df_out['RegA_MW'] * df_out['Mileage_RegA'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['RegD_Revenue'] = df_out['RegD_MW'] * (df_out['RMCCP_D'] * perf_d + df_out['RMPCP_D'] * df_out['Mileage_RegD'] * perf_d) * timestep_hours - df_out['RegD_MW'] * df_out['Mileage_RegD'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['SYNCH_Revenue'] = df_out['SYNCH_MW'] * df_out['Price_SYNCH'] * timestep_hours
        df_out['NONSYNCH_Revenue'] = df_out['NONSYNCH_MW'] * df_out['Price_NONSYNCH'] * timestep_hours
        
        df_out['Ancillary_Revenue'] = (df_out['RegA_Revenue'] + df_out['RegD_Revenue'] + 
                                       df_out['SYNCH_Revenue'] + df_out['NONSYNCH_Revenue'])
        
        # PJM Reliability Pricing Model (RPM) Capacity Revenue
        # Distributed evenly across all hourly timesteps
        elcc = self.config.get("elcc_factor", 0.30)
        hourly_capacity_rate = (self.power_mw * elcc * self.capacity_price_mw_day) / 24.0
        df_out['Capacity_Revenue'] = hourly_capacity_rate * timestep_hours
        
        df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + (df_out['RegA_MW'] * df_out['Mileage_RegA'] + df_out['RegD_MW'] * df_out['Mileage_RegD']) * timestep_hours * self.deg_cost * self.mileage_factor
        
        # Net operational merchant revenue
        df_out['revenue'] = (df_out['Energy_Revenue'] + df_out['Ancillary_Revenue'] + 
                             df_out['Capacity_Revenue'] - df_out['Total_Degradation_Cost'])
        
        return df_out

    def calculate_summary_metrics(self, df_out):
        """Returns financial and operational KPIs for PJM."""
        timestep_hours = 1.0
        if len(df_out) > 1:
            td = (df_out['timestamp'].iloc[1] - df_out['timestamp'].iloc[0]).total_seconds() / 3600.0
            if td != 0:
                timestep_hours = td
                
        total_rev = df_out['revenue'].sum()
        energy_rev = df_out['Energy_Revenue'].sum()
        as_rev = df_out['Ancillary_Revenue'].sum()
        rega_rev = df_out['RegA_Revenue'].sum()
        regd_rev = df_out['RegD_Revenue'].sum()
        synch_rev = df_out['SYNCH_Revenue'].sum()
        nonsynch_rev = df_out['NONSYNCH_Revenue'].sum()
        capacity_rev = df_out['Capacity_Revenue'].sum()
        deg_expense = df_out['Total_Degradation_Cost'].sum()
        loc_sum = df_out['Lost_Opportunity_Cost'].sum()
        
        # Operational KPIs
        total_discharge_mwh = (df_out['discharge_mw'] * timestep_hours).sum()
        total_charge_mwh = (df_out['charge_mw'] * timestep_hours).sum()
        
        efc = total_discharge_mwh / self.energy_mwh
        
        achieved_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        
        as_sum = (df_out['RegA_MW'] + df_out['RegD_MW'] + df_out['SYNCH_MW'] + df_out['NONSYNCH_MW'])
        as_fraction = (as_sum > 1e-3).mean()
        
        mode_counts = df_out['decision'].value_counts()
        total_intervals = len(df_out)
        utilization = {k: v/total_intervals for k, v in mode_counts.items()}
        
        metrics = {
            'Total Net Merchant Revenue ($)': total_rev,
            'Energy Arbitrage Revenue ($)': energy_rev,
            'Ancillary Services Revenue ($)': as_rev,
            'RegA Revenue ($)': rega_rev,
            'RegD Revenue ($)': regd_rev,
            'SYNCH Revenue ($)': synch_rev,
            'NONSYNCH Revenue ($)': nonsynch_rev,
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
