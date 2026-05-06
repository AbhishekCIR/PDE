import pandas as pd
import numpy as np
import pulp
from tqdm import tqdm
from core_optimizer import BESS_Simulator_Base

class Generic_Optimizer(BESS_Simulator_Base):
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

    def run_optimization_dispatch(self, df, progress_callback=None):
        """
        Runs rigorous MILP co-optimization day-by-day.
        Solves 365 separate 24h problems handling continuous optimization.
        """
        T_total = len(df)
        df_out = df.copy()
        
        # Preallocate result columns
        charge_mw_arr = np.zeros(T_total)
        discharge_mw_arr = np.zeros(T_total)
        reg_mw_arr = np.zeros(T_total)
        soc_mwh_arr = np.zeros(T_total)
        
        LMP_all = df['LMP'].values
        RegPrice_all = df['Reg_Price'].values
        
        if T_total > 1:
            timestep_hours = (df['timestamp'].iloc[1] - df['timestamp'].iloc[0]).total_seconds() / 3600.0
            if timestep_hours == 0:
                timestep_hours = 1.0
        else:
            timestep_hours = 1.0
            
        current_soc = self.initial_soc
        
        dates = df['timestamp'].dt.date.unique()
        
        for i, date_val in enumerate(tqdm(dates, desc='Solving Daily Optimization')):
            if progress_callback:
                progress_callback(i, len(dates))
            
            day_mask = (df['timestamp'].dt.date == date_val)
            day_indices = df.index[day_mask].tolist()
            T_day = len(day_indices)
            
            LMP = LMP_all[day_indices]
            RegPrice = RegPrice_all[day_indices]
            
            prob = pulp.LpProblem(f"BESS_Dispatch_{date_val}", pulp.LpMaximize)
            
            c = pulp.LpVariable.dicts("Charge", range(T_day), lowBound=0, upBound=self.power_mw)
            d = pulp.LpVariable.dicts("Discharge", range(T_day), lowBound=0, upBound=self.power_mw)
            r = pulp.LpVariable.dicts("Reg", range(T_day), lowBound=0, upBound=self.power_mw)
            soc = pulp.LpVariable.dicts("SoC", range(T_day), lowBound=0, upBound=self.energy_mwh)
            
            # Binary variables for charging and discharging states to prevent simultaneous charge/discharge
            u_c = pulp.LpVariable.dicts("u_C", range(T_day), cat='Binary')
            u_d = pulp.LpVariable.dicts("u_D", range(T_day), cat='Binary')
            
            # Objective
            prob += pulp.lpSum([
                (d[t] - c[t]) * LMP[t] * timestep_hours + 
                r[t] * RegPrice[t] * timestep_hours -
                (r[t] * timestep_hours * self.deg_cost * self.mileage_factor)
                for t in range(T_day)
            ])
            
            # Constraints
            for t in range(T_day):
                # Binary state linkages
                prob += c[t] <= self.power_mw * u_c[t]
                prob += d[t] <= self.power_mw * u_d[t]
                prob += u_c[t] + u_d[t] <= 1  # Cannot charge and discharge simultaneously
                
                # Power and SoC tracking
                prob += d[t] + r[t] <= self.power_mw
                prob += c[t] + r[t] <= self.power_mw
                prob += soc[t] >= r[t] * timestep_hours
                prob += soc[t] <= self.energy_mwh - r[t] * timestep_hours
                
                if t == 0:
                    prob += soc[t] == current_soc + c[t] * self.eff_c * timestep_hours - (d[t] / self.eff_d) * timestep_hours
                else:
                    prob += soc[t] == soc[t-1] + c[t] * self.eff_c * timestep_hours - (d[t] / self.eff_d) * timestep_hours
            
            # Energy Throughput Constraint (to not exceed self.max_cycles of energy per day)
            prob += pulp.lpSum([d[t] * timestep_hours for t in range(T_day)]) <= self.max_cycles * self.energy_mwh
            
            prob.solve(pulp.PULP_CBC_CMD(msg=0))
            
            for i, global_t in enumerate(day_indices):
                charge_mw_arr[global_t] = c[i].varValue or 0
                discharge_mw_arr[global_t] = d[i].varValue or 0
                reg_mw_arr[global_t] = r[i].varValue or 0
                soc_mwh_arr[global_t] = soc[i].varValue or 0
                
            current_soc = soc_mwh_arr[day_indices[-1]]
            
        df_out['charge_mw'] = charge_mw_arr
        df_out['discharge_mw'] = discharge_mw_arr
        df_out['reg_mw'] = reg_mw_arr
        df_out['soc_mwh'] = soc_mwh_arr
        
        df_out['energy_revenue'] = (df_out['discharge_mw'] - df_out['charge_mw']) * df['LMP'] * timestep_hours
        reg_deg_cost = df_out['reg_mw'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['reg_revenue'] = (df_out['reg_mw'] * df['Reg_Price'] * timestep_hours) - reg_deg_cost
        
        df_out['revenue'] = df_out['energy_revenue'] + df_out['reg_revenue']
        
        decisions = []
        for t in range(T_total):
            c_val = charge_mw_arr[t]
            d_val = discharge_mw_arr[t]
            r_val = reg_mw_arr[t]
            
            if r_val > 1e-3 and (c_val > 1e-3 or d_val > 1e-3):
                decisions.append('Mixed')
            elif r_val > 1e-3:
                decisions.append('Regulation')
            elif c_val > 1e-3:
                decisions.append('Charge')
            elif d_val > 1e-3:
                decisions.append('Discharge')
            else:
                decisions.append('Idle')
        df_out['decision'] = decisions
        
        return df_out

    def calculate_summary_metrics(self, df_out):
        total_revenue = df_out['revenue'].sum()
        total_energy_rev = df_out['energy_revenue'].sum()
        total_reg_rev = df_out['reg_revenue'].sum()
        
        mode_counts = df_out['decision'].value_counts()
        total_intervals = len(df_out)
        utilization = {k: v/total_intervals for k, v in mode_counts.items()}
        
        timestep_hours = 1.0
        if len(df_out) > 1:
            td = (df_out['timestamp'].iloc[1] - df_out['timestamp'].iloc[0]).total_seconds() / 3600.0
            if td != 0:
                timestep_hours = td
            
        mwh_discharged = (df_out['discharge_mw'] * timestep_hours).sum()
        cycles_per_year = (mwh_discharged / self.energy_mwh) * (8760 / (total_intervals * timestep_hours))
        
        metrics = {
            'Total Revenue ($)': total_revenue,
            'Energy Revenue ($)': total_energy_rev,
            'Regulation Revenue ($)': total_reg_rev,
            'Cycles / Year (Annualized)': cycles_per_year,
        }
        return metrics, utilization
