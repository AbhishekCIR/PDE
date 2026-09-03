# market_pjm.py
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base
from typing import List, Dict, Optional

class PJM_Optimizer(BESS_Simulator_Base):
<<<<<<< Updated upstream
    def __init__(self, power_mw=100.0, duration_hr=4.0, rte=0.90, max_cycles_per_day=1.0, 
                 initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, mileage_factor=0.10,
                 capacity_price_mw_day=120.0, reg_throughput_factor=0.15, is_tolling=False):
        super().__init__(power_mw, duration_hr, rte, max_cycles_per_day, initial_soc_pct, 
                         degradation_cost_per_mwh, mileage_factor, market_name="PJM",
                         reg_throughput_factor=reg_throughput_factor, is_tolling=is_tolling)
=======
    """
    PJM Market Optimizer for BESS assets.
    Supports both:
      1. Dynamic Variable Tranche Bidding (for large storage assets e.g. 250 MW Raccoon Island)
      2. Classic Dual Regulation (RegA / RegD) and Synchronized/Non-Synchronized Operating Reserves
    """
    def __init__(
        self, 
        power_mw: float = 100.0, 
        duration_hr: float = 4.0, 
        rte: float = 0.90, 
        max_cycles_per_day: float = 1.0, 
        initial_soc_pct: float = 0.5, 
        degradation_cost_per_mwh: float = 5.0, 
        mileage_factor: float = 0.10,
        capacity_price_mw_day: float = 120.0, 
        reg_throughput_factor: float = 0.15,
        enable_tranches: bool = True,
        tranches: Optional[List[Dict]] = None,
        elcc_factor: float = 0.50
    ):
        super().__init__(
            power_mw=power_mw, 
            duration_hr=duration_hr, 
            rte=rte, 
            max_cycles_per_day=max_cycles_per_day, 
            initial_soc_pct=initial_soc_pct, 
            degradation_cost_per_mwh=degradation_cost_per_mwh, 
            mileage_factor=mileage_factor, 
            market_name="PJM",
            reg_throughput_factor=reg_throughput_factor
        )
>>>>>>> Stashed changes
        
        self.capacity_price_mw_day = capacity_price_mw_day
        self.elcc_factor = elcc_factor
        self.enable_tranches = enable_tranches
        
        # Configure variable regulation tranches
        if tranches is None and enable_tranches:
            # Smart default 3-tranche segmentation calibrated to power_mw
            self.tranches = [
                {"name": "Tranche 1 (Base)", "mw": min(50.0, power_mw * 0.25), "hurdle_rate": 0.0},
                {"name": "Tranche 2 (Mid)",  "mw": min(50.0, power_mw * 0.25), "hurdle_rate": 30.0},
                {"name": "Tranche 3 (Peak)", "mw": min(25.0, power_mw * 0.15), "hurdle_rate": 60.0}
            ]
        else:
            self.tranches = tranches or []

    def get_market_soc_impact(self, subclass_vars, t, timestep_hours, is_value=False):
<<<<<<< Updated upstream
        regA = subclass_vars['regA'][t]
        regD = subclass_vars['regD'][t]
        regA_val = regA.varValue if is_value else regA
        regD_val = regD.varValue if is_value else regD
        if regA_val is None: regA_val = 0.0
        if regD_val is None: regD_val = 0.0

        # Get dynamic mileages
        mileage_a = self.current_df['Mileage_RegA'].iloc[t] if hasattr(self, 'current_df') and 'Mileage_RegA' in self.current_df.columns else 1.2
        mileage_d = self.current_df['Mileage_RegD'].iloc[t] if hasattr(self, 'current_df') and 'Mileage_RegD' in self.current_df.columns else 3.5
        
        throughput_a = regA_val * mileage_a * self.reg_throughput_factor * 0.5
        throughput_d = regD_val * mileage_d * self.reg_throughput_factor * 0.5
        
        # PJM dual regulation (RegA + RegD) RTE loss depletion
        return (throughput_a + throughput_d) * (self.eff_c - 1.0 / self.eff_d) * timestep_hours

    def generate_sample_data(self, days=365, freq='1h', random_seed=None):
=======
        """Calculates SOC depletion from regulation AGC round-trip losses."""
        if self.enable_tranches and self.tranches:
            tot_reg = 0.0
            for i in range(len(self.tranches)):
                v = subclass_vars['tranche_vars'][i][t]
                val = v.varValue if is_value else v
                if val is not None:
                    tot_reg += val
            return tot_reg * self.reg_throughput_factor * (self.eff_c - 1.0 / self.eff_d) * timestep_hours
        else:
            regA = subclass_vars.get('regA', {}).get(t, 0.0)
            regD = subclass_vars.get('regD', {}).get(t, 0.0)
            regA_val = regA.varValue if (is_value and hasattr(regA, 'varValue')) else (regA if not hasattr(regA, 'varValue') else 0.0)
            regD_val = regD.varValue if (is_value and hasattr(regD, 'varValue')) else (regD if not hasattr(regD, 'varValue') else 0.0)
            if regA_val is None: regA_val = 0.0
            if regD_val is None: regD_val = 0.0
            return (regA_val + regD_val) * self.reg_throughput_factor * (self.eff_c - 1.0 / self.eff_d) * timestep_hours

    def generate_sample_data(self, days: int = 365, freq: str = '1h') -> pd.DataFrame:
>>>>>>> Stashed changes
        """Generates synthetic PJM prices for 1 year."""
        if random_seed is not None:
            np.random.seed(random_seed)
        timestamps = pd.date_range(start="2026-01-01", periods=days * 24, freq=freq)
        df = pd.DataFrame({'timestamp': timestamps})
        
        hours = df['timestamp'].dt.hour
        months = df['timestamp'].dt.month
        summer_mult = np.where((months >= 6) & (months <= 8), 1.45, 1.0)
        winter_mult = np.where((months == 1) | (months == 2) | (months == 12), 1.35, 1.0)
        seasonal_mult = np.maximum(summer_mult, winter_mult)
        
        # Base LMP curve
        base_lmp = 28.0 + 36.0 * np.sin((hours - 12) * np.pi / 12)**2 * seasonal_mult
        noise = np.random.normal(0, 5, len(df))
        df['LMP'] = np.clip(base_lmp + noise, -15.0, None)
        
        # Add random price spikes
        spike_indices = np.random.choice(df.index, size=int(len(df)*0.035), replace=False)
        df.loc[spike_indices, 'LMP'] += np.random.uniform(60, 260, size=len(spike_indices))
        
        # PJM synthetic ancillary services (Capability and Performance prices)
        df['RMCCP_A'] = np.random.lognormal(mean=1.2, sigma=0.4, size=len(df))
        df['RMPCP_A'] = np.random.lognormal(mean=0.5, sigma=0.3, size=len(df))
        
        # Fast regulation RegD
        df['RMCCP_D'] = 22.0 + 26.0 * np.sin((hours - 7) * np.pi / 12)**2 * seasonal_mult + np.random.normal(0, 4, len(df))
        df['RMCCP_D'] = np.clip(df['RMCCP_D'], 4.0, None)
        df.loc[spike_indices, 'RMCCP_D'] += np.random.uniform(40, 140, size=len(spike_indices))
        df['RMPCP_D'] = np.random.uniform(1.5, 4.5, len(df))
        
        # Mileage ratios
        mileage_a = self.config.get("default_mileage", {}).get("RegA", 1.2)
        mileage_d = self.config.get("default_mileage", {}).get("RegD", 3.2)
        df['Mileage_RegA'] = np.clip(np.random.normal(mileage_a, 0.1, len(df)), 0.5, None)
        df['Mileage_RegD'] = np.clip(np.random.normal(mileage_d, 0.3, len(df)), 1.5, None)
        
        # Reserves
        df['Price_SYNCH'] = np.clip(np.random.lognormal(mean=1.2, sigma=0.5, size=len(df)), 2.0, 35.0)
        df['Price_NONSYNCH'] = np.clip(np.random.lognormal(mean=0.8, sigma=0.4, size=len(df)), 1.0, 20.0)
        
        return df

    def _get_effective_reg_price(self, df_prices: pd.DataFrame) -> np.ndarray:
        """Calculates total effective regulation price per MWh from capability + performance."""
        perf_d = self.config.get("default_performance_score", {}).get("RegD", 0.95)
        
        if 'RMCCP_D' in df_prices.columns and 'RMPCP_D' in df_prices.columns:
            mileage = df_prices['Mileage_RegD'].values if 'Mileage_RegD' in df_prices.columns else np.full(len(df_prices), 3.2)
            return (df_prices['RMCCP_D'].values * perf_d) + (df_prices['RMPCP_D'].values * mileage * perf_d)
        elif 'Reg_Effective_Price' in df_prices.columns:
            return df_prices['Reg_Effective_Price'].values
        elif 'Reg_Price' in df_prices.columns:
            return df_prices['Reg_Price'].values
        elif 'RMCCP_A' in df_prices.columns:
            mileage_a = df_prices['Mileage_RegA'].values if 'Mileage_RegA' in df_prices.columns else np.full(len(df_prices), 1.2)
            return (df_prices['RMCCP_A'].values * 0.90) + (df_prices['RMPCP_A'].values * mileage_a * 0.90)
        else:
            return np.zeros(len(df_prices))

    def define_market_variables(self, prob, T_day):
        """Defines PJM specific LpVariables for tranches and reserves."""
        synch = pulp.LpVariable.dicts("SYNCH", range(T_day), lowBound=0, upBound=self.power_mw)
        nonsynch = pulp.LpVariable.dicts("NONSYNCH", range(T_day), lowBound=0, upBound=self.power_mw)
        
        if self.enable_tranches and self.tranches:
            tranche_vars = []
            for i, tr in enumerate(self.tranches):
                mw_cap = float(tr.get('mw', 0.0))
                v = pulp.LpVariable.dicts(f"Reg_Tranche_{i}", range(T_day), lowBound=0, upBound=mw_cap)
                tranche_vars.append(v)
            
            return {
                'tranche_vars': tranche_vars,
                'synch': synch,
                'nonsynch': nonsynch
            }
        else:
            # Classic RegA / RegD Mode
            regA = pulp.LpVariable.dicts("RegA", range(T_day), lowBound=0, upBound=self.power_mw)
            regD = pulp.LpVariable.dicts("RegD", range(T_day), lowBound=0, upBound=self.power_mw)
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
        """Adds PJM power capacity, hurdle rates, and reserve SOC buffer constraints."""
        synch = subclass_vars['synch']
        nonsynch = subclass_vars['nonsynch']
        
        dur_synch = self.config.get("reserve_durations", {}).get("SYNCH", 0.50)
        dur_nonsynch = self.config.get("reserve_durations", {}).get("NONSYNCH", 0.50)

        if self.enable_tranches and self.tranches:
            tranche_vars = subclass_vars['tranche_vars']
            eff_reg_prices = self._get_effective_reg_price(df_prices)
            
<<<<<<< Updated upstream
            # State of Charge Reservation Constraints (Sustainability)
            prob += soc[t] >= (regA[t] * dur_rega + regD[t] * dur_regd + synch[t] * dur_synch + nonsynch[t] * dur_nonsynch)
            prob += self.energy_mwh - soc[t] >= (regA[t] * dur_rega + regD[t] * dur_regd)
=======
            for t in range(T_day):
                tot_reg_expr = pulp.lpSum([tranche_vars[i][t] for i in range(len(self.tranches))])
                
                # Inverter Power Limits
                prob += d[t] + tot_reg_expr + synch[t] + nonsynch[t] <= self.power_mw
                prob += c[t] + tot_reg_expr <= self.power_mw
                
                # SOC Continuous Headroom Buffer for PJM Regulation (30-min buffer)
                prob += soc[t] >= (0.10 * self.energy_mwh) + (tot_reg_expr * 0.50 + synch[t] * dur_synch + nonsynch[t] * dur_nonsynch) * timestep_hours
                prob += soc[t] <= (0.90 * self.energy_mwh) - (tot_reg_expr * 0.50) * timestep_hours
                
                # Hurdle Rate Enforcement for Each User Tranche
                for i, tr in enumerate(self.tranches):
                    hurdle = float(tr.get('hurdle_rate', 0.0))
                    if eff_reg_prices[t] < hurdle:
                        prob += tranche_vars[i][t] == 0
        else:
            regA = subclass_vars['regA']
            regD = subclass_vars['regD']
            dur_rega = self.config.get("reserve_durations", {}).get("RegA", 1.0)
            dur_regd = self.config.get("reserve_durations", {}).get("RegD", 0.5)

            for t in range(T_day):
                prob += d[t] + regA[t] + regD[t] + synch[t] + nonsynch[t] <= self.power_mw
                prob += c[t] + regA[t] + regD[t] <= self.power_mw
                prob += soc[t] >= (regA[t] * dur_rega + regD[t] * dur_regd + synch[t] * dur_synch + nonsynch[t] * dur_nonsynch) * timestep_hours
                prob += self.energy_mwh - soc[t] >= (regA[t] * dur_rega + regD[t] * dur_regd) * timestep_hours
>>>>>>> Stashed changes

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective function terms for PJM ancillary services."""
        synch = subclass_vars['synch']
        nonsynch = subclass_vars['nonsynch']
        
        Price_SYNCH = df_prices['Price_SYNCH'].values if 'Price_SYNCH' in df_prices.columns else np.zeros(T_day)
        Price_NONSYNCH = df_prices['Price_NONSYNCH'].values if 'Price_NONSYNCH' in df_prices.columns else np.zeros(T_day)
        
        as_rev_terms = []
        reg_deg_factor = self.deg_cost * self.mileage_factor

        if self.enable_tranches and self.tranches:
            tranche_vars = subclass_vars['tranche_vars']
            eff_reg_prices = self._get_effective_reg_price(df_prices)
            
            for t in range(T_day):
                tot_reg_expr = pulp.lpSum([tranche_vars[i][t] for i in range(len(self.tranches))])
                reg_net = (tot_reg_expr * eff_reg_prices[t] * timestep_hours) - (tot_reg_expr * reg_deg_factor * timestep_hours)
                synch_rev = synch[t] * Price_SYNCH[t] * timestep_hours
                nonsynch_rev = nonsynch[t] * Price_NONSYNCH[t] * timestep_hours
                as_rev_terms.append(reg_net + synch_rev + nonsynch_rev)
        else:
            regA = subclass_vars['regA']
            regD = subclass_vars['regD']
            RMCCP_A = df_prices['RMCCP_A'].values if 'RMCCP_A' in df_prices.columns else np.zeros(T_day)
            RMPCP_A = df_prices['RMPCP_A'].values if 'RMPCP_A' in df_prices.columns else np.zeros(T_day)
            RMCCP_D = df_prices['RMCCP_D'].values if 'RMCCP_D' in df_prices.columns else np.zeros(T_day)
            RMPCP_D = df_prices['RMPCP_D'].values if 'RMPCP_D' in df_prices.columns else np.zeros(T_day)
            Mileage_RegA = df_prices['Mileage_RegA'].values if 'Mileage_RegA' in df_prices.columns else np.full(T_day, 1.2)
            Mileage_RegD = df_prices['Mileage_RegD'].values if 'Mileage_RegD' in df_prices.columns else np.full(T_day, 3.2)
            
            perf_a = self.config.get("default_performance_score", {}).get("RegA", 0.90)
            perf_d = self.config.get("default_performance_score", {}).get("RegD", 0.95)
            
            for t in range(T_day):
                rega_rev = regA[t] * (RMCCP_A[t] * perf_a + RMPCP_A[t] * Mileage_RegA[t] * perf_a) * timestep_hours
                regd_rev = regD[t] * (RMCCP_D[t] * perf_d + RMPCP_D[t] * Mileage_RegD[t] * perf_d) * timestep_hours
                synch_rev = synch[t] * Price_SYNCH[t] * timestep_hours
                nonsynch_rev = nonsynch[t] * Price_NONSYNCH[t] * timestep_hours
                rega_deg = regA[t] * Mileage_RegA[t] * timestep_hours * self.deg_cost * self.mileage_factor
                regd_deg = regD[t] * Mileage_RegD[t] * timestep_hours * self.deg_cost * self.mileage_factor
                as_rev_terms.append(rega_rev + regd_rev + synch_rev + nonsynch_rev - (rega_deg + regd_deg))
                
        return pulp.lpSum(as_rev_terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts cleared variables."""
        res = {
            'SYNCH_MW': [subclass_vars['synch'][t].varValue or 0.0 for t in day_indices],
            'NONSYNCH_MW': [subclass_vars['nonsynch'][t].varValue or 0.0 for t in day_indices]
        }

        if self.enable_tranches and self.tranches:
            tranche_vars = subclass_vars['tranche_vars']
            tot_reg_arr = [0.0] * len(day_indices)
            
            for i, tr in enumerate(self.tranches):
                col_name = f"{tr['name']}_MW"
                vals = [tranche_vars[i][t].varValue or 0.0 for t in day_indices]
                res[col_name] = vals
                for idx, v in enumerate(vals):
                    tot_reg_arr[idx] += v
            
            res['Total_Reg_MW'] = tot_reg_arr
            res['RegD_MW'] = tot_reg_arr  # Compatibility alias
            res['RegA_MW'] = [0.0] * len(day_indices)
        else:
            res['RegA_MW'] = [subclass_vars['regA'][t].varValue or 0.0 for t in day_indices]
            res['RegD_MW'] = [subclass_vars['regD'][t].varValue or 0.0 for t in day_indices]
            res['Total_Reg_MW'] = [res['RegA_MW'][i] + res['RegD_MW'][i] for i in range(len(day_indices))]

        return res

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates revenue columns post-optimization."""
        Price_SYNCH = df_out['Price_SYNCH'] if 'Price_SYNCH' in df_out.columns else 0.0
        Price_NONSYNCH = df_out['Price_NONSYNCH'] if 'Price_NONSYNCH' in df_out.columns else 0.0
        
        df_out['SYNCH_Revenue'] = df_out['SYNCH_MW'] * Price_SYNCH * timestep_hours
        df_out['NONSYNCH_Revenue'] = df_out['NONSYNCH_MW'] * Price_NONSYNCH * timestep_hours
        
        perf_d = self.config.get("default_performance_score", {}).get("RegD", 0.95)
        eff_reg_p = self._get_effective_reg_price(df_out)
        
<<<<<<< Updated upstream
        df_out['RegA_Revenue'] = df_out['RegA_MW'] * (df_out['RMCCP_A'] * perf_a + df_out['RMPCP_A'] * df_out['Mileage_RegA'] * perf_a) * timestep_hours
        df_out['RegD_Revenue'] = df_out['RegD_MW'] * (df_out['RMCCP_D'] * perf_d + df_out['RMPCP_D'] * df_out['Mileage_RegD'] * perf_d) * timestep_hours
        df_out['SYNCH_Revenue'] = df_out['SYNCH_MW'] * df_out['Price_SYNCH'] * timestep_hours
        df_out['NONSYNCH_Revenue'] = df_out['NONSYNCH_MW'] * df_out['Price_NONSYNCH'] * timestep_hours
=======
        if self.enable_tranches and self.tranches:
            tot_reg_rev = np.zeros(len(df_out))
            tot_reg_deg = np.zeros(len(df_out))
            
            for tr in self.tranches:
                col = f"{tr['name']}_MW"
                if col in df_out.columns:
                    tr_rev = df_out[col] * eff_reg_p * timestep_hours
                    tr_deg = df_out[col] * timestep_hours * self.deg_cost * self.mileage_factor
                    df_out[f"{tr['name']}_Revenue"] = tr_rev - tr_deg
                    tot_reg_rev += tr_rev
                    tot_reg_deg += tr_deg
            
            df_out['Regulation_Revenue'] = tot_reg_rev - tot_reg_deg
            df_out['RegD_Revenue'] = df_out['Regulation_Revenue']
            df_out['RegA_Revenue'] = 0.0
            df_out['Ancillary_Revenue'] = df_out['Regulation_Revenue'] + df_out['SYNCH_Revenue'] + df_out['NONSYNCH_Revenue']
            df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + tot_reg_deg
        else:
            perf_a = self.config.get("default_performance_score", {}).get("RegA", 0.90)
            Mileage_RegA = df_out['Mileage_RegA'] if 'Mileage_RegA' in df_out.columns else 1.2
            Mileage_RegD = df_out['Mileage_RegD'] if 'Mileage_RegD' in df_out.columns else 3.2
            RMCCP_A = df_out['RMCCP_A'] if 'RMCCP_A' in df_out.columns else 0.0
            RMPCP_A = df_out['RMPCP_A'] if 'RMPCP_A' in df_out.columns else 0.0
            RMCCP_D = df_out['RMCCP_D'] if 'RMCCP_D' in df_out.columns else 0.0
            RMPCP_D = df_out['RMPCP_D'] if 'RMPCP_D' in df_out.columns else 0.0
            
            df_out['RegA_Revenue'] = df_out['RegA_MW'] * (RMCCP_A * perf_a + RMPCP_A * Mileage_RegA * perf_a) * timestep_hours - df_out['RegA_MW'] * Mileage_RegA * timestep_hours * self.deg_cost * self.mileage_factor
            df_out['RegD_Revenue'] = df_out['RegD_MW'] * (RMCCP_D * perf_d + RMPCP_D * Mileage_RegD * perf_d) * timestep_hours - df_out['RegD_MW'] * Mileage_RegD * timestep_hours * self.deg_cost * self.mileage_factor
            df_out['Regulation_Revenue'] = df_out['RegA_Revenue'] + df_out['RegD_Revenue']
            df_out['Ancillary_Revenue'] = df_out['Regulation_Revenue'] + df_out['SYNCH_Revenue'] + df_out['NONSYNCH_Revenue']
            df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + (df_out['RegA_MW'] * Mileage_RegA + df_out['RegD_MW'] * Mileage_RegD) * timestep_hours * self.deg_cost * self.mileage_factor
>>>>>>> Stashed changes
        
        # Capacity Revenue
        elcc = self.config.get("elcc_factor", self.elcc_factor)
        hourly_capacity_rate = (self.power_mw * elcc * self.capacity_price_mw_day) / 24.0
        df_out['Capacity_Revenue'] = hourly_capacity_rate * timestep_hours
        
        # Net Merchant Revenue
        df_out['revenue'] = df_out['Energy_Revenue'] + df_out['Ancillary_Revenue'] + df_out['Capacity_Revenue'] - df_out['Total_Degradation_Cost']
        
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
        reg_rev = df_out['Regulation_Revenue'].sum()
        synch_rev = df_out['SYNCH_Revenue'].sum()
        nonsynch_rev = df_out['NONSYNCH_Revenue'].sum()
        capacity_rev = df_out['Capacity_Revenue'].sum()
        deg_expense = df_out['Total_Degradation_Cost'].sum()
        loc_sum = df_out['Lost_Opportunity_Cost'].sum() if 'Lost_Opportunity_Cost' in df_out.columns else 0.0
        
        # Operational KPIs
        total_discharge_mwh = (df_out['discharge_mw'] * timestep_hours).sum()
        total_charge_mwh = (df_out['charge_mw'] * timestep_hours).sum()
<<<<<<< Updated upstream
        
        mileage_a = df_out['Mileage_RegA'] if 'Mileage_RegA' in df_out.columns else 1.2
        mileage_d = df_out['Mileage_RegD'] if 'Mileage_RegD' in df_out.columns else 3.5
        
        agc_throughput_a = df_out['RegA_MW'] * mileage_a * self.reg_throughput_factor * 0.5
        agc_throughput_d = df_out['RegD_MW'] * mileage_d * self.reg_throughput_factor * 0.5
        total_agc_discharge_mwh = ((agc_throughput_a + agc_throughput_d) * timestep_hours).sum()
        total_agc_charge_mwh = total_agc_discharge_mwh
        
        arb_efc = total_discharge_mwh / self.energy_mwh
        agc_efc = total_agc_discharge_mwh / self.energy_mwh
        total_efc = arb_efc + agc_efc
        
        arb_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        physical_rte = ((total_discharge_mwh + total_agc_discharge_mwh) / 
                        (total_charge_mwh + total_agc_charge_mwh)) if (total_charge_mwh + total_agc_charge_mwh) > 0 else 0.0
=======
        efc = total_discharge_mwh / self.energy_mwh if self.energy_mwh > 0 else 0.0
        achieved_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
>>>>>>> Stashed changes
        
        as_sum = df_out['Total_Reg_MW'] + df_out['SYNCH_MW'] + df_out['NONSYNCH_MW']
        as_fraction = (as_sum > 1e-3).mean()
        
        mode_counts = df_out['decision'].value_counts() if 'decision' in df_out.columns else pd.Series()
        total_intervals = len(df_out)
        utilization = {k: v/total_intervals for k, v in mode_counts.items()} if total_intervals > 0 else {}
        
        metrics = {
            'Total Net Merchant Revenue ($)': total_rev,
            'Energy Arbitrage Revenue ($)': energy_rev,
            'Ancillary Services Revenue ($)': as_rev,
            'Regulation Revenue ($)': reg_rev,
            'SYNCH Revenue ($)': synch_rev,
            'NONSYNCH Revenue ($)': nonsynch_rev,
            'Static Capacity Revenue ($)': capacity_rev,
            'Degradation Expense ($)': deg_expense,
            'Reported Lost Opportunity Cost ($)': loc_sum,
            'Equivalent Full Cycles (EFC)': arb_efc, # Backward compatibility
            'Arbitrage EFC': arb_efc,
            'AGC EFC': agc_efc,
            'Total EFC': total_efc,
            'Achieved Round-Trip Efficiency': arb_rte, # Backward compatibility
            'Arbitrage Round-Trip Efficiency': arb_rte,
            'Physical Round-Trip Efficiency': physical_rte,
            'Charging Energy (MWh)': total_charge_mwh,
            'Discharging Energy (MWh)': total_discharge_mwh,
            'AGC Charge Throughput (MWh)': total_agc_charge_mwh,
            'AGC Discharge Throughput (MWh)': total_agc_discharge_mwh,
            'Total AGC Throughput (MWh)': total_agc_charge_mwh + total_agc_discharge_mwh,
            'Ancillary Participation Fraction': as_fraction
        }

        # Tranche-specific statistics
        if self.enable_tranches and self.tranches:
            for tr in self.tranches:
                col = f"{tr['name']}_MW"
                rev_col = f"{tr['name']}_Revenue"
                if col in df_out.columns:
                    metrics[f"Avg Cleared {tr['name']} (MW)"] = df_out[col].mean()
                if rev_col in df_out.columns:
                    metrics[f"Total {tr['name']} Revenue ($)"] = df_out[rev_col].sum()
        
        return metrics, utilization

if __name__ == "__main__":
    print("Testing PJM_Optimizer with 250 MW / 1,000 MWh BESS and Variable Tranches...")
    custom_tranches = [
        {"name": "Tranche 1 (Base)", "mw": 50.0, "hurdle_rate": 0.0},
        {"name": "Tranche 2 (Mid)",  "mw": 50.0, "hurdle_rate": 30.0},
        {"name": "Tranche 3 (Peak)", "mw": 25.0, "hurdle_rate": 60.0}
    ]
    
    optimizer = PJM_Optimizer(
        power_mw=250.0,
        duration_hr=4.0,
        rte=0.88,
        max_cycles_per_day=1.2,
        capacity_price_mw_day=329.17,
        elcc_factor=0.50,
        enable_tranches=True,
        tranches=custom_tranches
    )
    
    df_sample = optimizer.generate_sample_data(days=14)
    df_results = optimizer.run_optimization_dispatch(df_sample)
    metrics, _ = optimizer.calculate_summary_metrics(df_results)
    
    print("\n=== PJM 250 MW 14-DAY OPTIMIZATION METRICS ===")
    for k, v in metrics.items():
        if "$" in k:
            print(f"{k:<45}: ${v:>12,.2f}")
        else:
            print(f"{k:<45}: {v:>12.2f}")
