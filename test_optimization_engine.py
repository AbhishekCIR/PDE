# test_optimization_engine.py
import unittest
import pandas as pd
import numpy as np
from core_optimizer import BESS_Simulator_Base
from market_generic import Generic_Optimizer
from market_ercot import ERCOT_Optimizer
from market_miso import MISO_Optimizer
from market_pjm import PJM_Optimizer
from forecast_engine.persistence_forecast import PersistenceForecastEngine

class TestBESSOptimizer(unittest.TestCase):
    def setUp(self):
        # Default testing battery parameters (100MW, 400MWh, 90% RTE)
        self.power_mw = 100.0
        self.duration_hr = 4.0
        self.energy_mwh = 400.0
        self.rte = 0.90
        self.eff_c = np.sqrt(0.90)
        self.eff_d = np.sqrt(0.90)
        self.max_cycles = 1.0
        
        # Initalize generic optimizer for base tests
        self.optimizer = Generic_Optimizer(
            power_mw=self.power_mw, duration_hr=self.duration_hr, rte=self.rte,
            max_cycles_per_day=self.max_cycles, initial_soc_pct=0.5,
            degradation_cost_per_mwh=5.0, mileage_factor=0.10
        )

    def test_energy_balance_and_physical_limits(self):
        """Verifies that the state of charge tracks energy balance and remains within bounds [0, Emax]."""
        df = self.optimizer.generate_sample_data(days=5) # Run 5 days
        df_opt = self.optimizer.run_optimization_dispatch(df)
        
        T = len(df_opt)
        timestep_hours = 1.0
        
        current_soc = self.optimizer.initial_soc
        
        for t in range(T):
            charge = df_opt['charge_mw'].iloc[t]
            discharge = df_opt['discharge_mw'].iloc[t]
            soc = df_opt['soc_mwh'].iloc[t]
            
            # 1. Check physical limits
            self.assertTrue(charge >= 0.0 and charge <= self.power_mw + 1e-5)
            self.assertTrue(discharge >= 0.0 and discharge <= self.power_mw + 1e-5)
            self.assertTrue(soc >= -1e-5 and soc <= self.energy_mwh + 1e-5)
            
            # 2. Check simultaneous charging and discharging
            self.assertFalse(charge > 1e-3 and discharge > 1e-3, f"Simultaneous charge/discharge detected at index {t}")
            
            # 3. Check state of charge dynamics
            expected_soc = current_soc + charge * self.eff_c * timestep_hours - (discharge / self.eff_d) * timestep_hours
            self.assertAlmostEqual(soc, expected_soc, places=3, msg=f"SoC mismatch at index {t}")
            
            current_soc = soc

    def test_cycle_limits(self):
        """Verifies that the energy throughput does not exceed max daily cycles."""
        df = self.optimizer.generate_sample_data(days=10)
        df_opt = self.optimizer.run_optimization_dispatch(df)
        
        # Group by date and check cycles
        df_opt['date'] = df_opt['timestamp'].dt.date
        daily_discharges = df_opt.groupby('date')['discharge_mw'].sum() # Since dt = 1.0h, sum(MW) = MWh
        
        for date, discharge_mwh in daily_discharges.items():
            daily_cycles = discharge_mwh / self.energy_mwh
            self.assertTrue(daily_cycles <= self.max_cycles + 1e-3, 
                            f"Daily cycles {daily_cycles} exceeded max cycles limit of {self.max_cycles} on {date}")

    def test_negative_prices_charging(self):
        """Verifies that the battery charges when energy prices are negative."""
        # Create a 24 hour dataframe with negative prices at HE 3-5
        timestamps = pd.date_range(start="2026-01-01", periods=24, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [30.0] * 24,
            'Reg_Price': [5.0] * 24
        })
        # Set negative prices
        df.loc[2:4, 'LMP'] = -20.0  # Hours index 2, 3 (HE 3, 4)
        # Set high price later to discharge
        df.loc[18:19, 'LMP'] = 150.0 # HE 19, 20
        
        df_opt = self.optimizer.run_optimization_dispatch(df)
        
        # Verify it charged during the negative price hours
        charge_during_neg = df_opt['charge_mw'].iloc[2:5].sum()
        self.assertTrue(charge_during_neg > 0.0, "Battery failed to charge during negative prices")

    def test_vpp_operating_mode(self):
        """Verifies that the VPP contract mode reserves capacity during specified hours."""
        df = self.optimizer.generate_sample_data(days=2)
        
        # Restrict capacity by 40 MW during VPP HE 17-21
        df['CAP_LIMIT'] = self.power_mw
        hours = df['timestamp'].dt.hour
        vpp_mask = (hours >= 17) & (hours <= 21)
        df.loc[vpp_mask, 'CAP_LIMIT'] = self.power_mw - 40.0 # Curtailed to 60MW
        
        df['Charge_LMP'] = df['LMP']
        
        df_opt = self.optimizer.run_optimization_dispatch(df)
        
        # Verify charge and discharge do not exceed curtailed capacity during VPP hours
        for t in df_opt[vpp_mask].index:
            self.assertTrue(df_opt['charge_mw'].iloc[t] <= 60.0 + 1e-3)
            self.assertTrue(df_opt['discharge_mw'].iloc[t] <= 60.0 + 1e-3)

    def test_tolling_agreement_mode(self):
        """Verifies that tolling agreement ignores charging costs (pass-through charging)."""
        # Create a dataset where charging cost is high, but later discharging is slightly higher
        # In normal merchant mode, the margin might not cover efficiency loss + deg cost.
        # Under tolling agreement (free charging), the battery should charge.
        timestamps = pd.date_range(start="2026-01-01", periods=24, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [50.0] * 24, # High charging cost
            'Reg_Price': [0.0] * 24
        })
        df.loc[2:4, 'LMP'] = 50.0
        df.loc[18:19, 'LMP'] = 60.0 # Slightly higher
        
        # 1. Normal merchant mode (should stay idle because efficiency loss is 10% and margin is small)
        df['Charge_LMP'] = df['LMP']
        df_opt_merchant = self.optimizer.run_optimization_dispatch(df)
        self.assertEqual(df_opt_merchant['charge_mw'].sum(), 0.0, "Normal merchant BESS charged with sub-optimal margin")
        
        # 2. Tolling Agreement mode (charging is free, so it should charge and discharge)
        df['Charge_LMP'] = 0.0
        df_opt_toller = self.optimizer.run_optimization_dispatch(df)
        self.assertTrue(df_opt_toller['charge_mw'].sum() > 0.0, "Tolling BESS failed to charge when charging is free")

    def test_pjm_co_optimization_and_soc_limits(self):
        """Verifies PJM's dual RegA/RegD co-optimization, mutual exclusivity, and reserve SOC bounds."""
        pjm_opt = PJM_Optimizer(
            power_mw=self.power_mw, duration_hr=self.duration_hr, rte=self.rte,
            max_cycles_per_day=self.max_cycles, initial_soc_pct=0.5
        )
        
        df = pjm_opt.generate_sample_data(days=3)
        df_opt = pjm_opt.run_optimization_dispatch(df)
        
        T = len(df_opt)
        for t in range(T):
            rega = df_opt['RegA_MW'].iloc[t]
            regd = df_opt['RegD_MW'].iloc[t]
            soc = df_opt['soc_mwh'].iloc[t]
            
            # 1. Check mutual exclusivity
            self.assertFalse(rega > 1e-3 and regd > 1e-3, f"RegA and RegD both cleared simultaneously at index {t}")
            
            # 2. Check SOC sustainability constraints
            # RegA requires 1.0 hour, RegD requires 0.5 hours
            dur_rega = pjm_opt.config.get("reserve_durations", {}).get("RegA", 1.0)
            dur_regd = pjm_opt.config.get("reserve_durations", {}).get("RegD", 0.5)
            
            min_soc_req = (rega * dur_rega + regd * dur_regd) * 1.0 # timestep = 1.0h
            self.assertTrue(soc >= min_soc_req - 1e-3, f"PJM SOC footroom constraint violated at index {t}: soc={soc}, req={min_soc_req}")
            
            max_soc_limit = self.energy_mwh - (rega * dur_rega + regd * dur_regd) * 1.0
            self.assertTrue(soc <= max_soc_limit + 1e-3, f"PJM SOC headroom constraint violated at index {t}: soc={soc}, limit={max_soc_limit}")

    def test_rolling_horizon_dispatch(self):
        """Verifies that the rolling-horizon simulation resolves sequential hourly optimization successfully."""
        forecaster = PersistenceForecastEngine(market_name="Generic", method="naive")
        
        df = self.optimizer.generate_sample_data(days=3)
        
        # Run rolling-horizon with a 24-hour look-ahead window and naive persistence
        df_opt = self.optimizer.run_optimization_dispatch(
            df=df,
            forecast_engine=forecaster,
            forecast_horizon_hrs=24
        )
        
        # Verify output dataframe size and columns
        self.assertEqual(len(df_opt), len(df))
        self.assertTrue('charge_mw' in df_opt.columns)
        self.assertTrue('discharge_mw' in df_opt.columns)
        self.assertTrue('soc_mwh' in df_opt.columns)

if __name__ == '__main__':
    unittest.main()
