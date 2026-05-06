import pandas as pd
import numpy as np

class BESS_Simulator_Base:
    def __init__(self, power_mw=100, duration_hr=4, rte=0.9, max_cycles_per_day=1, initial_soc_pct=0.5, degradation_cost_per_mwh=0, mileage_factor=0.1):
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

    def run_optimization_dispatch(self, df, progress_callback=None):
        """
        To be overridden by market-specific subclasses.
        """
        raise NotImplementedError("Subclasses must implement this method")

    def calculate_summary_metrics(self, df_out):
        """
        To be overridden or customized by subclasses based on their revenue columns.
        """
        raise NotImplementedError("Subclasses must implement this method")
