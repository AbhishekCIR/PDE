# core_optimizer.py
import pandas as pd
import numpy as np
import pulp
from tqdm import tqdm
import json
import os

class BESS_Simulator_Base:
    def __init__(self, power_mw=100.0, duration_hr=4.0, rte=0.90, max_cycles_per_day=1.0, 
                 initial_soc_pct=0.5, degradation_cost_per_mwh=5.0, mileage_factor=0.10,
                 market_name="Generic"):
        self.power_mw = power_mw
        self.duration_hr = duration_hr
        self.energy_mwh = power_mw * duration_hr
        self.rte = rte
        self.eff_c = np.sqrt(rte)
        self.eff_d = np.sqrt(rte)
        self.max_cycles = max_cycles_per_day
        self.initial_soc = initial_soc_pct * self.energy_mwh
        self.deg_cost = degradation_cost_per_mwh
        self.mileage_factor = mileage_factor
        self.market_name = market_name
        
        # Load external market config
        self.config = self.load_market_config()

    def load_market_config(self):
        """Loads configuration from external market_config.json."""
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'market_config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                cfg = json.load(f)
            return cfg.get(self.market_name, {})
        else:
            # Fallback hardcoded defaults if config doesn't exist yet
            return {
                "market_version": "1.0",
                "reserve_durations": {},
                "default_mileage_factor": 0.10,
                "elcc_factor": 1.0
            }

    # --- Abstract Hooks to be implemented by subclasses ---
    def generate_sample_data(self, days=365, freq='1h'):
        raise NotImplementedError("Subclasses must implement generate_sample_data")

    def define_market_variables(self, prob, T_day):
        """Returns a dict of PuLP LpVariable lists/dicts for market products."""
        return {}

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Adds market-specific power capacity & SOC reservation constraints."""
        pass

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Returns objective sum terms for market products (excluding energy & deg cost)."""
        return 0.0

    def extract_market_results(self, subclass_vars, day_indices):
        """Extracts decision variable values from subclass variables."""
        return {}

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Calculates specific revenue streams post-optimization."""
        return df_out

    def calculate_summary_metrics(self, df_out):
        """Returns summary metrics and utilization statistics."""
        raise NotImplementedError("Subclasses must implement calculate_summary_metrics")

    # --- Main Optimization Dispatch Engine ---
    def run_optimization_dispatch(self, df, progress_callback=None, forecast_engine=None, 
                                  forecast_horizon_hrs=48, forecast_mape=0.0):
        """
        Runs co-optimization dispatch under either Perfect Foresight or Rolling Horizon.
        """
        T_total = len(df)
        df_out = df.copy()
        
        # Determine timestep resolution in hours
        if T_total > 1:
            timestep_hours = (df['timestamp'].iloc[1] - df['timestamp'].iloc[0]).total_seconds() / 3600.0
            if timestep_hours == 0:
                timestep_hours = 1.0
        else:
            timestep_hours = 1.0

        # Preallocate base result arrays
        charge_mw_arr = np.zeros(T_total)
        discharge_mw_arr = np.zeros(T_total)
        soc_mwh_arr = np.zeros(T_total)
        
        # Subclass preallocation setup
        market_res_dicts = {}

        current_soc = self.initial_soc
        
        # --- Mode A: Rolling Horizon (Forecasting) ---
        if forecast_engine is not None:
            # Setup history tracker (we index by timestamp)
            df_history = df.set_index('timestamp')
            
            for t in tqdm(range(T_total), desc=f'Solving Rolling Horizon ({self.market_name})'):
                if progress_callback:
                    progress_callback(t, T_total)
                    
                current_time = df['timestamp'].iloc[t]
                
                # 1. Generate price forecasts using the forecast engine
                # Pass historical data before current_time, and future actuals if using noisy_actual mode
                hist_subset = df.iloc[:t].set_index('timestamp')
                # If hist_subset is empty, initialize with a dummy row to avoid issues
                if len(hist_subset) == 0:
                    hist_subset = pd.DataFrame(columns=df.columns).set_index('timestamp')
                    
                # Create future actuals for forecast horizon
                future_actuals = df.iloc[t:t+forecast_horizon_hrs].set_index('timestamp')
                
                forecast_df = forecast_engine.generate_forecast(
                    df_history=hist_subset,
                    current_time=current_time,
                    forecast_horizon_hrs=forecast_horizon_hrs,
                    future_actual_df=future_actuals
                )
                
                T_window = len(forecast_df)
                if T_window == 0:
                    break
                    
                # 2. Build the MILP problem for the look-ahead window
                prob = pulp.LpProblem(f"BESS_Rolling_{t}", pulp.LpMaximize)
                
                c = pulp.LpVariable.dicts("Charge", range(T_window), lowBound=0, upBound=self.power_mw)
                d = pulp.LpVariable.dicts("Discharge", range(T_window), lowBound=0, upBound=self.power_mw)
                soc = pulp.LpVariable.dicts("SoC", range(T_window), lowBound=0, upBound=self.energy_mwh)
                
                # Charging and discharging binary state variables
                u_c = pulp.LpVariable.dicts("u_C", range(T_window), cat='Binary')
                u_d = pulp.LpVariable.dicts("u_D", range(T_window), cat='Binary')
                
                # Infeasibility slacks
                s_min = pulp.LpVariable.dicts("s_min", range(T_window), lowBound=0)
                s_max = pulp.LpVariable.dicts("s_max", range(T_window), lowBound=0)
                
                subclass_vars = self.define_market_variables(prob, T_window)
                
                # Objective formulation: Energy Arbitrage + AS cleared revenues - degradation cost - infeasibility penalties
                LMP = forecast_df['LMP'].values
                Charge_LMP = forecast_df['Charge_LMP'].values if 'Charge_LMP' in forecast_df.columns else LMP
                CAP_LIMIT = forecast_df['CAP_LIMIT'].values if 'CAP_LIMIT' in forecast_df.columns else np.full(T_window, self.power_mw)
                
                # Check for other prices and fill missing if necessary
                for col in forecast_df.columns:
                    if col not in forecast_df:
                        forecast_df[col] = 0.0
                
                # Base energy objective (supporting tolling agreements via Charge_LMP)
                energy_obj = pulp.lpSum([
                    (d[w] * LMP[w] - c[w] * Charge_LMP[w]) * timestep_hours for w in range(T_window)
                ])
                
                # Degradation penalty (energy throughput-based degradation)
                # Subclasses can add AS mileage degradation to this
                deg_obj = pulp.lpSum([
                    (c[w] * self.eff_c + d[w] / self.eff_d) * timestep_hours * self.deg_cost for w in range(T_window)
                ])
                
                # Market specific co-optimization terms
                market_obj = self.get_objective_expression(prob, c, d, soc, subclass_vars, forecast_df, T_window, timestep_hours)
                
                # Infeasibility slack penalty
                slack_penalty = pulp.lpSum([
                    (s_min[w] + s_max[w]) * 1000000.0 for w in range(T_window)
                ])
                
                prob += energy_obj + market_obj - deg_obj - slack_penalty
                
                # Physical BESS Constraints with Infeasibility Slacks
                for w in range(T_window):
                    prob += c[w] <= CAP_LIMIT[w] * u_c[w]
                    prob += d[w] <= CAP_LIMIT[w] * u_d[w]
                    prob += u_c[w] + u_d[w] <= 1
                    
                    # SoC limits relaxed by slack variables
                    prob += soc[w] >= 0.0 - s_min[w]
                    prob += soc[w] <= self.energy_mwh + s_max[w]
                    
                    if w == 0:
                        prob += soc[w] == current_soc + c[w] * self.eff_c * timestep_hours - (d[w] / self.eff_d) * timestep_hours
                    else:
                        prob += soc[w] == soc[w-1] + c[w] * self.eff_c * timestep_hours - (d[w] / self.eff_d) * timestep_hours
                
                # Daily cycle constraint mapped across the look-ahead window (scaled proportionally)
                prob += pulp.lpSum([d[w] * timestep_hours for w in range(T_window)]) <= (self.max_cycles * self.energy_mwh * (T_window * timestep_hours / 24.0))
                
                # Add market-specific constraints
                self.add_market_constraints(prob, c, d, soc, subclass_vars, forecast_df, T_window, timestep_hours)
                
                # Solve using PuLP CBC Solver with gap tolerance and warm starts (sequential initialization)
                solver = pulp.PULP_CBC_CMD(msg=0, gapRel=0.005, timeLimit=10)
                prob.solve(solver)
                
                # 3. Apply the first hour's dispatch result
                charge_mw_arr[t] = c[0].varValue or 0.0
                discharge_mw_arr[t] = d[0].varValue or 0.0
                
                # Keep variables within physical limits due to slight solver numerical precision noise
                charge_mw_arr[t] = np.clip(charge_mw_arr[t], 0.0, self.power_mw)
                discharge_mw_arr[t] = np.clip(discharge_mw_arr[t], 0.0, self.power_mw)
                
                # State of charge dynamics updates based on actual executed step
                next_soc = current_soc + charge_mw_arr[t] * self.eff_c * timestep_hours - (discharge_mw_arr[t] / self.eff_d) * timestep_hours
                next_soc = np.clip(next_soc, 0.0, self.energy_mwh)
                soc_mwh_arr[t] = next_soc
                
                # Extract market decisions for the first step
                step_results = self.extract_market_results(subclass_vars, [0])
                for key, val in step_results.items():
                    if key not in market_res_dicts:
                        market_res_dicts[key] = np.zeros(T_total)
                    market_res_dicts[key][t] = val[0] or 0.0
                
                current_soc = next_soc

        # --- Mode B: Perfect Foresight Day-by-Day (Deterministic Benchmark) ---
        else:
            dates = df['timestamp'].dt.date.unique()
            
            for d_idx, date_val in enumerate(tqdm(dates, desc=f'Solving Perfect Foresight ({self.market_name})')):
                if progress_callback:
                    progress_callback(d_idx, len(dates))
                
                day_mask = (df['timestamp'].dt.date == date_val)
                day_indices = df.index[day_mask].tolist()
                T_day = len(day_indices)
                
                df_day = df.iloc[day_indices].copy()
                
                prob = pulp.LpProblem(f"BESS_Foresight_{date_val}", pulp.LpMaximize)
                
                c = pulp.LpVariable.dicts("Charge", range(T_day), lowBound=0, upBound=self.power_mw)
                d = pulp.LpVariable.dicts("Discharge", range(T_day), lowBound=0, upBound=self.power_mw)
                soc = pulp.LpVariable.dicts("SoC", range(T_day), lowBound=0, upBound=self.energy_mwh)
                
                u_c = pulp.LpVariable.dicts("u_C", range(T_day), cat='Binary')
                u_d = pulp.LpVariable.dicts("u_D", range(T_day), cat='Binary')
                
                s_min = pulp.LpVariable.dicts("s_min", range(T_day), lowBound=0)
                s_max = pulp.LpVariable.dicts("s_max", range(T_day), lowBound=0)
                
                subclass_vars = self.define_market_variables(prob, T_day)
                
                LMP = df_day['LMP'].values
                Charge_LMP = df_day['Charge_LMP'].values if 'Charge_LMP' in df_day.columns else LMP
                CAP_LIMIT = df_day['CAP_LIMIT'].values if 'CAP_LIMIT' in df_day.columns else np.full(T_day, self.power_mw)
                
                energy_obj = pulp.lpSum([
                    (d[t] * LMP[t] - c[t] * Charge_LMP[t]) * timestep_hours for t in range(T_day)
                ])
                
                deg_obj = pulp.lpSum([
                    (c[t] * self.eff_c + d[t] / self.eff_d) * timestep_hours * self.deg_cost for t in range(T_day)
                ])
                
                market_obj = self.get_objective_expression(prob, c, d, soc, subclass_vars, df_day, T_day, timestep_hours)
                
                slack_penalty = pulp.lpSum([
                    (s_min[t] + s_max[t]) * 1000000.0 for t in range(T_day)
                ])
                
                prob += energy_obj + market_obj - deg_obj - slack_penalty
                
                for t in range(T_day):
                    prob += c[t] <= CAP_LIMIT[t] * u_c[t]
                    prob += d[t] <= CAP_LIMIT[t] * u_d[t]
                    prob += u_c[t] + u_d[t] <= 1
                    
                    prob += soc[t] >= 0.0 - s_min[t]
                    prob += soc[t] <= self.energy_mwh + s_max[t]
                    
                    if t == 0:
                        prob += soc[t] == current_soc + c[t] * self.eff_c * timestep_hours - (d[t] / self.eff_d) * timestep_hours
                    else:
                        prob += soc[t] == soc[t-1] + c[t] * self.eff_c * timestep_hours - (d[t] / self.eff_d) * timestep_hours
                
                prob += pulp.lpSum([d[t] * timestep_hours for t in range(T_day)]) <= self.max_cycles * self.energy_mwh
                
                self.add_market_constraints(prob, c, d, soc, subclass_vars, df_day, T_day, timestep_hours)
                
                solver = pulp.PULP_CBC_CMD(msg=0, gapRel=0.005, timeLimit=10)
                prob.solve(solver)
                
                for t_idx, global_t in enumerate(day_indices):
                    charge_mw_arr[global_t] = c[t_idx].varValue or 0.0
                    discharge_mw_arr[global_t] = d[t_idx].varValue or 0.0
                    
                    # Clip boundaries
                    charge_mw_arr[global_t] = np.clip(charge_mw_arr[global_t], 0.0, self.power_mw)
                    discharge_mw_arr[global_t] = np.clip(discharge_mw_arr[global_t], 0.0, self.power_mw)
                    
                    if t_idx == 0:
                        soc_mwh_arr[global_t] = current_soc + charge_mw_arr[global_t] * self.eff_c * timestep_hours - (discharge_mw_arr[global_t] / self.eff_d) * timestep_hours
                    else:
                        soc_mwh_arr[global_t] = soc_mwh_arr[global_t-1] + charge_mw_arr[global_t] * self.eff_c * timestep_hours - (discharge_mw_arr[global_t] / self.eff_d) * timestep_hours
                    
                    soc_mwh_arr[global_t] = np.clip(soc_mwh_arr[global_t], 0.0, self.energy_mwh)
                
                # Extract market decisions
                day_res = self.extract_market_results(subclass_vars, range(T_day))
                for key, val in day_res.items():
                    if key not in market_res_dicts:
                        market_res_dicts[key] = np.zeros(T_total)
                    for t_idx, global_t in enumerate(day_indices):
                        market_res_dicts[key][global_t] = val[t_idx] or 0.0
                
                current_soc = soc_mwh_arr[day_indices[-1]]

        # --- Finalize Dispatch Outputs ---
        df_out['charge_mw'] = charge_mw_arr
        df_out['discharge_mw'] = discharge_mw_arr
        df_out['soc_mwh'] = soc_mwh_arr
        
        # Populate market-specific result columns
        for key, val in market_res_dicts.items():
            df_out[key] = val
            
        # 1. Base Energy Arbitrage Revenue (actual pricing)
        df_out['Energy_Revenue'] = (df_out['discharge_mw'] - df_out['charge_mw']) * df['LMP'] * timestep_hours
        
        # 2. Energy Degradation Cost (actual executed dispatch)
        df_out['Energy_Degradation_Cost'] = (df_out['charge_mw'] * self.eff_c + df_out['discharge_mw'] / self.eff_d) * timestep_hours * self.deg_cost
        
        # 3. Market Specific Revenues post-optimization hook
        df_out = self.calculate_market_revenues(df_out, timestep_hours)
        
        # 4. Lost Opportunity Cost (LOC) Reporting Metric
        # Foregone energy arbitrage margin due to reserve capacity holdback
        # Calculated post-optimization purely for analytical reporting
        as_award_sum = np.zeros(T_total)
        for key in market_res_dicts.keys():
            if key.endswith('_MW') or key in ['REGUP_MW', 'REGDN_MW', 'RRS_MW', 'NSPIN_MW', 'ECRS_MW', 'REG_MW', 'SPIN_MW', 'SUPP_MW', 'RegA_MW', 'RegD_MW', 'SYNCH_MW', 'NONSYNCH_MW']:
                as_award_sum += df_out[key].values
                
        # Shadow opportunity margin: max(0, LMP - deg_cost/eff_d)
        opportunity_margin = np.maximum(0.0, df_out['LMP'].values - (self.deg_cost / self.eff_d))
        df_out['Lost_Opportunity_Cost'] = opportunity_margin * (self.power_mw - df_out['discharge_mw'].values - as_award_sum) * timestep_hours
        # Keep LOC positive and restrict to when capacity was actually held for reserves
        as_mask = as_award_sum > 1e-3
        df_out.loc[~as_mask, 'Lost_Opportunity_Cost'] = 0.0

        # Define dynamic decisions list
        decisions = []
        for t in range(T_total):
            c_val = charge_mw_arr[t]
            d_val = discharge_mw_arr[t]
            as_val = as_award_sum[t]
            
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
