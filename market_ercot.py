import pandas as pd
import numpy as np
import pulp
from tqdm import tqdm
from core_optimizer import BESS_Simulator_Base

class ERCOT_Optimizer(BESS_Simulator_Base):
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
        df['LMP'] = df['LMP'].clip(lower=0)
        
        spike_indices = np.random.choice(df.index, size=int(len(df)*0.03), replace=False)
        df.loc[spike_indices, 'LMP'] += np.random.uniform(50, 200, size=len(spike_indices))
        
        # Synthetic ancillary services
        df['REGUP'] = np.random.lognormal(mean=1.5, sigma=0.5, size=len(df))
        df['REGDN'] = np.random.lognormal(mean=1.2, sigma=0.5, size=len(df))
        df['RRS'] = np.random.lognormal(mean=1.8, sigma=0.6, size=len(df))
        df['NSPIN'] = np.random.lognormal(mean=1.0, sigma=0.5, size=len(df))
        df['ECRS'] = np.random.lognormal(mean=1.3, sigma=0.6, size=len(df))
        
        return df

    def run_optimization_dispatch(self, df, progress_callback=None):
        """
        Runs rigorous MILP co-optimization for ERCOT day-by-day.
        """
        T_total = len(df)
        df_out = df.copy()
        
        # Output arrays
        charge_arr = np.zeros(T_total)
        discharge_arr = np.zeros(T_total)
        regup_arr = np.zeros(T_total)
        regdn_arr = np.zeros(T_total)
        rrs_arr = np.zeros(T_total)
        nspin_arr = np.zeros(T_total)
        ecrs_arr = np.zeros(T_total)
        soc_arr = np.zeros(T_total)
        
        LMP_all = df['LMP'].values
        REGUP_all = df['REGUP'].values
        REGDN_all = df['REGDN'].values
        RRS_all = df['RRS'].values
        NSPIN_all = df['NSPIN'].values
        ECRS_all = df['ECRS'].values
        
        if T_total > 1:
            timestep_hours = (df['timestamp'].iloc[1] - df['timestamp'].iloc[0]).total_seconds() / 3600.0
            if timestep_hours == 0:
                timestep_hours = 1.0
        else:
            timestep_hours = 1.0
            
        current_soc = self.initial_soc
        dates = df['timestamp'].dt.date.unique()
        
        for i, date_val in enumerate(tqdm(dates, desc='Solving ERCOT Optimization')):
            if progress_callback:
                progress_callback(i, len(dates))
            
            day_mask = (df['timestamp'].dt.date == date_val)
            day_indices = df.index[day_mask].tolist()
            T_day = len(day_indices)
            
            LMP = LMP_all[day_indices]
            REGUP = REGUP_all[day_indices]
            REGDN = REGDN_all[day_indices]
            RRS = RRS_all[day_indices]
            NSPIN = NSPIN_all[day_indices]
            ECRS = ECRS_all[day_indices]
            
            prob = pulp.LpProblem(f"ERCOT_Dispatch_{date_val}", pulp.LpMaximize)
            
            c = pulp.LpVariable.dicts("Charge", range(T_day), lowBound=0, upBound=self.power_mw)
            d = pulp.LpVariable.dicts("Discharge", range(T_day), lowBound=0, upBound=self.power_mw)
            regup = pulp.LpVariable.dicts("REGUP", range(T_day), lowBound=0, upBound=self.power_mw)
            regdn = pulp.LpVariable.dicts("REGDN", range(T_day), lowBound=0, upBound=self.power_mw)
            rrs = pulp.LpVariable.dicts("RRS", range(T_day), lowBound=0, upBound=self.power_mw)
            nspin = pulp.LpVariable.dicts("NSPIN", range(T_day), lowBound=0, upBound=self.power_mw)
            ecrs = pulp.LpVariable.dicts("ECRS", range(T_day), lowBound=0, upBound=self.power_mw)
            soc = pulp.LpVariable.dicts("SoC", range(T_day), lowBound=0, upBound=self.energy_mwh)
            
            u_c = pulp.LpVariable.dicts("u_C", range(T_day), cat='Binary')
            u_d = pulp.LpVariable.dicts("u_D", range(T_day), cat='Binary')
            
            # Objective Function
            prob += pulp.lpSum([
                (d[t] - c[t]) * LMP[t] * timestep_hours +
                regup[t] * REGUP[t] * timestep_hours +
                regdn[t] * REGDN[t] * timestep_hours +
                rrs[t] * RRS[t] * timestep_hours +
                nspin[t] * NSPIN[t] * timestep_hours +
                ecrs[t] * ECRS[t] * timestep_hours -
                ((regup[t] + regdn[t]) * timestep_hours * self.deg_cost * self.mileage_factor)  # Assuming degradation mostly hits regulation
                for t in range(T_day)
            ])
            
            # Constraints
            for t in range(T_day):
                # Binary state linkages
                prob += c[t] <= self.power_mw * u_c[t]
                prob += d[t] <= self.power_mw * u_d[t]
                prob += u_c[t] + u_d[t] <= 1  # Cannot charge and discharge simultaneously
                
                # ERCOT Power Constraints
                prob += d[t] + regup[t] + rrs[t] + nspin[t] + ecrs[t] <= self.power_mw
                prob += c[t] + regdn[t] <= self.power_mw
                
                # ERCOT Energy Constraints (Simplified Reserve Durations)
                # ECRS: 2 hours, others: 1 hour.
                prob += soc[t] >= (ecrs[t] * 2 + (regup[t] + rrs[t] + nspin[t]) * 1) * timestep_hours
                prob += self.energy_mwh - soc[t] >= regdn[t] * 1 * timestep_hours
                
                # SoC Balance Equation
                if t == 0:
                    prob += soc[t] == current_soc + c[t] * self.eff_c * timestep_hours - (d[t] / self.eff_d) * timestep_hours
                else:
                    prob += soc[t] == soc[t-1] + c[t] * self.eff_c * timestep_hours - (d[t] / self.eff_d) * timestep_hours
            
            # Energy Throughput Constraint
            prob += pulp.lpSum([d[t] * timestep_hours for t in range(T_day)]) <= self.max_cycles * self.energy_mwh
            
            prob.solve(pulp.PULP_CBC_CMD(msg=0))
            
            for i, global_t in enumerate(day_indices):
                charge_arr[global_t] = c[i].varValue or 0
                discharge_arr[global_t] = d[i].varValue or 0
                regup_arr[global_t] = regup[i].varValue or 0
                regdn_arr[global_t] = regdn[i].varValue or 0
                rrs_arr[global_t] = rrs[i].varValue or 0
                nspin_arr[global_t] = nspin[i].varValue or 0
                ecrs_arr[global_t] = ecrs[i].varValue or 0
                soc_arr[global_t] = soc[i].varValue or 0
                
            current_soc = soc_arr[day_indices[-1]]
            
        df_out['charge_mw'] = charge_arr
        df_out['discharge_mw'] = discharge_arr
        df_out['REGUP_MW'] = regup_arr
        df_out['REGDN_MW'] = regdn_arr
        df_out['RRS_MW'] = rrs_arr
        df_out['NSPIN_MW'] = nspin_arr
        df_out['ECRS_MW'] = ecrs_arr
        df_out['soc_mwh'] = soc_arr
        
        # Revenue Calcs
        df_out['Energy_Revenue'] = (df_out['discharge_mw'] - df_out['charge_mw']) * df['LMP'] * timestep_hours
        
        regup_deg_cost = df_out['REGUP_MW'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['REGUP_Revenue'] = (df_out['REGUP_MW'] * df['REGUP'] * timestep_hours) - regup_deg_cost
        
        regdn_deg_cost = df_out['REGDN_MW'] * timestep_hours * self.deg_cost * self.mileage_factor
        df_out['REGDN_Revenue'] = (df_out['REGDN_MW'] * df['REGDN'] * timestep_hours) - regdn_deg_cost
        
        df_out['RRS_Revenue'] = df_out['RRS_MW'] * df['RRS'] * timestep_hours
        df_out['NSPIN_Revenue'] = df_out['NSPIN_MW'] * df['NSPIN'] * timestep_hours
        df_out['ECRS_Revenue'] = df_out['ECRS_MW'] * df['ECRS'] * timestep_hours
        
        df_out['revenue'] = (df_out['Energy_Revenue'] + df_out['REGUP_Revenue'] + 
                             df_out['REGDN_Revenue'] + df_out['RRS_Revenue'] + 
                             df_out['NSPIN_Revenue'] + df_out['ECRS_Revenue'])
        
        decisions = []
        for t in range(T_total):
            c_val = charge_arr[t]
            d_val = discharge_arr[t]
            as_val = regup_arr[t] + regdn_arr[t] + rrs_arr[t] + nspin_arr[t] + ecrs_arr[t]
            
            if as_val > 1e-3 and (c_val > 1e-3 or d_val > 1e-3):
                decisions.append('Mixed')
            elif as_val > 1e-3:
                decisions.append('Ancillary')
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
        total_energy_rev = df_out['Energy_Revenue'].sum()
        total_regup_rev = df_out['REGUP_Revenue'].sum()
        total_regdn_rev = df_out['REGDN_Revenue'].sum()
        total_rrs_rev = df_out['RRS_Revenue'].sum()
        total_nspin_rev = df_out['NSPIN_Revenue'].sum()
        total_ecrs_rev = df_out['ECRS_Revenue'].sum()
        
        total_as_rev = total_regup_rev + total_regdn_rev + total_rrs_rev + total_nspin_rev + total_ecrs_rev
        
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
            'Ancillary Revenue ($)': total_as_rev,
            'Cycles / Year (Annualized)': cycles_per_year,
            'REGUP Revenue ($)': total_regup_rev,
            'REGDN Revenue ($)': total_regdn_rev,
            'RRS Revenue ($)': total_rrs_rev,
            'NSPIN Revenue ($)': total_nspin_rev,
            'ECRS Revenue ($)': total_ecrs_rev
        }
        return metrics, utilization
