# market_pjm.py
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base
from typing import List, Dict, Optional

class PJM_Optimizer(BESS_Simulator_Base):
    """
    PJM Market Optimizer for BESS assets.
    Implements unified PJM regulation pricing (RMCCP, RMPCP, Mileage) and variable tranche bidding.
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
        is_tolling: bool = False,
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
            reg_throughput_factor=reg_throughput_factor,
            is_tolling=is_tolling
        )
        
        self.capacity_price_mw_day = capacity_price_mw_day
        self.elcc_factor = elcc_factor
        self.enable_tranches = enable_tranches
        
        # Configure variable regulation tranches
        if tranches is None and enable_tranches:
            self.tranches = [
                {"name": "Tranche 1 (Base)", "mw": min(50.0, power_mw * 0.25), "hurdle_rate": 0.0},
                {"name": "Tranche 2 (Mid)",  "mw": min(50.0, power_mw * 0.25), "hurdle_rate": 30.0},
                {"name": "Tranche 3 (Peak)", "mw": min(25.0, power_mw * 0.15), "hurdle_rate": 60.0}
            ]
        elif tranches is not None:
            self.tranches = tranches
        else:
            self.tranches = [{"name": "Regulation", "mw": power_mw, "hurdle_rate": 0.0}]

    def get_market_soc_impact(self, subclass_vars, t, timestep_hours, is_value=False):
        """Calculates SOC depletion from regulation AGC round-trip losses."""
        tot_reg = 0.0
        for i in range(len(self.tranches)):
            v = subclass_vars['tranche_vars'][i][t]
            val = v.varValue if is_value else v
            if val is not None:
                tot_reg += val
        return tot_reg * self.reg_throughput_factor * (self.eff_c - 1.0 / self.eff_d) * timestep_hours

    def generate_sample_data(self, days: int = 365, freq: str = '1h') -> pd.DataFrame:
        """Generates unified synthetic PJM prices (RMCCP, RMPCP, Mileage, SYNCH, NONSYNCH)."""
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
        
        # Price spikes
        spike_indices = np.random.choice(df.index, size=int(len(df)*0.035), replace=False)
        df.loc[spike_indices, 'LMP'] += np.random.uniform(60, 260, size=len(spike_indices))
        
        # Unified Regulation Capability Price (RMCCP) & Performance Price (RMPCP)
        df['RMCCP'] = 22.0 + 26.0 * np.sin((hours - 7) * np.pi / 12)**2 * seasonal_mult + np.random.normal(0, 4, len(df))
        df['RMCCP'] = np.clip(df['RMCCP'], 4.0, None)
        df.loc[spike_indices, 'RMCCP'] += np.random.uniform(40, 140, size=len(spike_indices))
        df['RMPCP'] = np.random.uniform(1.5, 4.5, len(df))
        
        # Fast storage mileage ratio
        df['Mileage'] = np.clip(np.random.normal(3.2, 0.3, len(df)), 1.5, None)
        
        # Unified Effective Regulation Price = RMCCP * 0.95 + RMPCP * Mileage * 0.95
        df['Reg_Effective_Price'] = (df['RMCCP'] * 0.95) + (df['RMPCP'] * df['Mileage'] * 0.95)
        
        # Reserves
        df['Price_SYNCH'] = np.clip(np.random.lognormal(mean=1.2, sigma=0.5, size=len(df)), 2.0, 35.0)
        df['Price_NONSYNCH'] = np.clip(np.random.lognormal(mean=0.8, sigma=0.4, size=len(df)), 1.0, 20.0)
        
        return df

    def _get_effective_reg_price(self, df_prices: pd.DataFrame) -> np.ndarray:
        """Calculates total effective regulation price per MWh from unified capability + performance."""
        perf_score = self.config.get("default_performance_score", {}).get("Reg", 0.95)
        
        if 'Reg_Effective_Price' in df_prices.columns:
            return df_prices['Reg_Effective_Price'].values
        elif 'Reg_Price' in df_prices.columns:
            return df_prices['Reg_Price'].values
        elif 'RMCCP' in df_prices.columns and 'RMPCP' in df_prices.columns:
            mileage = df_prices['Mileage'].values if 'Mileage' in df_prices.columns else np.full(len(df_prices), 3.2)
            return (df_prices['RMCCP'].values * perf_score) + (df_prices['RMPCP'].values * mileage * perf_score)
        elif 'RMCCP_D' in df_prices.columns:
            mileage_d = df_prices['Mileage_RegD'].values if 'Mileage_RegD' in df_prices.columns else np.full(len(df_prices), 3.2)
            rmpcp_d = df_prices['RMPCP_D'].values if 'RMPCP_D' in df_prices.columns else np.full(len(df_prices), 2.5)
            return (df_prices['RMCCP_D'].values * perf_score) + (rmpcp_d * mileage_d * perf_score)
        else:
            return np.full(len(df_prices), 30.0)

    def define_market_variables(self, prob, T_day):
        """Defines PJM specific LpVariables for tranches and reserves."""
        synch = pulp.LpVariable.dicts("SYNCH", range(T_day), lowBound=0, upBound=self.power_mw)
        nonsynch = pulp.LpVariable.dicts("NONSYNCH", range(T_day), lowBound=0, upBound=self.power_mw)
        
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

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Adds PJM power capacity, hurdle rates, and reserve SOC buffer constraints."""
        synch = subclass_vars['synch']
        nonsynch = subclass_vars['nonsynch']
        tranche_vars = subclass_vars['tranche_vars']
        
        dur_synch = self.config.get("reserve_durations", {}).get("SYNCH", 0.50)
        dur_nonsynch = self.config.get("reserve_durations", {}).get("NONSYNCH", 0.50)
        eff_reg_prices = self._get_effective_reg_price(df_prices)
        
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

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective function terms for PJM ancillary services."""
        synch = subclass_vars['synch']
        nonsynch = subclass_vars['nonsynch']
        tranche_vars = subclass_vars['tranche_vars']
        
        Price_SYNCH = df_prices['Price_SYNCH'].values if 'Price_SYNCH' in df_prices.columns else np.zeros(T_day)
        Price_NONSYNCH = df_prices['Price_NONSYNCH'].values if 'Price_NONSYNCH' in df_prices.columns else np.zeros(T_day)
        
        as_rev_terms = []
        reg_deg_factor = self.deg_cost * self.mileage_factor
        eff_reg_prices = self._get_effective_reg_price(df_prices)
        
        for t in range(T_day):
            tot_reg_expr = pulp.lpSum([tranche_vars[i][t] for i in range(len(self.tranches))])
            reg_net = (tot_reg_expr * eff_reg_prices[t] * timestep_hours) - (tot_reg_expr * reg_deg_factor * timestep_hours)
            synch_rev = synch[t] * Price_SYNCH[t] * timestep_hours
            nonsynch_rev = nonsynch[t] * Price_NONSYNCH[t] * timestep_hours
            as_rev_terms.append(reg_net + synch_rev + nonsynch_rev)
                
        return pulp.lpSum(as_rev_terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts cleared variables."""
        res = {
            'SYNCH_MW': [subclass_vars['synch'][t].varValue or 0.0 for t in day_indices],
            'NONSYNCH_MW': [subclass_vars['nonsynch'][t].varValue or 0.0 for t in day_indices]
        }

        tranche_vars = subclass_vars['tranche_vars']
        tot_reg_arr = [0.0] * len(day_indices)
        
        for i, tr in enumerate(self.tranches):
            col_name = f"{tr['name']}_MW"
            vals = [tranche_vars[i][t].varValue or 0.0 for t in day_indices]
            res[col_name] = vals
            for idx, v in enumerate(vals):
                tot_reg_arr[idx] += v
        
        res['Total_Reg_MW'] = tot_reg_arr
        return res

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates revenue columns post-optimization."""
        Price_SYNCH = df_out['Price_SYNCH'] if 'Price_SYNCH' in df_out.columns else 0.0
        Price_NONSYNCH = df_out['Price_NONSYNCH'] if 'Price_NONSYNCH' in df_out.columns else 0.0
        
        df_out['SYNCH_Revenue'] = df_out['SYNCH_MW'] * Price_SYNCH * timestep_hours
        df_out['NONSYNCH_Revenue'] = df_out['NONSYNCH_MW'] * Price_NONSYNCH * timestep_hours
        
        eff_reg_p = self._get_effective_reg_price(df_out)
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
        df_out['Ancillary_Revenue'] = df_out['Regulation_Revenue'] + df_out['SYNCH_Revenue'] + df_out['NONSYNCH_Revenue']
        df_out['Total_Degradation_Cost'] = df_out['Energy_Degradation_Cost'] + tot_reg_deg
        
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
        efc = total_discharge_mwh / self.energy_mwh if self.energy_mwh > 0 else 0.0
        achieved_rte = (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        
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
            'Equivalent Full Cycles (EFC)': efc,
            'Achieved Round-Trip Efficiency': achieved_rte,
            'Charging Energy (MWh)': total_charge_mwh,
            'Discharging Energy (MWh)': total_discharge_mwh,
            'Ancillary Participation Fraction': as_fraction
        }

        # Tranche-specific statistics
        for tr in self.tranches:
            col = f"{tr['name']}_MW"
            rev_col = f"{tr['name']}_Revenue"
            if col in df_out.columns:
                metrics[f"Avg Cleared {tr['name']} (MW)"] = df_out[col].mean()
            if rev_col in df_out.columns:
                metrics[f"Total {tr['name']} Revenue ($)"] = df_out[rev_col].sum()
        
        return metrics, utilization

if __name__ == "__main__":
    print("Testing Unified PJM_Optimizer (250 MW / 1,000 MWh BESS)...")
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
        tranches=custom_tranches
    )
    
    df_sample = optimizer.generate_sample_data(days=7)
    df_results = optimizer.run_optimization_dispatch(df_sample)
    metrics, _ = optimizer.calculate_summary_metrics(df_results)
    
    print("\n=== PJM 250 MW 7-DAY UNIFIED OPTIMIZATION METRICS ===")
    for k, v in metrics.items():
        if "$" in k:
            print(f"{k:<45}: ${v:>12,.2f}")
        else:
            print(f"{k:<45}: {v:>12.2f}")
