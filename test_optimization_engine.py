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
            reg = df_opt['reg_mw'].iloc[t] if 'reg_mw' in df_opt.columns else 0.0
            soc_impact = reg * self.optimizer.reg_throughput_factor * (self.eff_c - 1.0 / self.eff_d) * timestep_hours
            expected_soc = current_soc + charge * self.eff_c * timestep_hours - (discharge / self.eff_d) * timestep_hours + soc_impact
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

    def test_miso_regulation_throughput_and_charging(self):
        """Verifies that with a positive regulation throughput factor, the battery is forced to charge
        from the grid to replenish RTE losses during MISO regulation participation.
        """
        miso_opt_0 = MISO_Optimizer(
            power_mw=150.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.25,
            degradation_cost_per_mwh=5.0, mileage_factor=0.10,
            capacity_price_mw_day=0.0, reg_throughput_factor=0.0
        )
        miso_opt_15 = MISO_Optimizer(
            power_mw=150.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.25,
            degradation_cost_per_mwh=5.0, mileage_factor=0.10,
            capacity_price_mw_day=0.0, reg_throughput_factor=0.15
        )
        
        # Create 48 hours dataset with high regulation prices and positive LMPs
        # Batteries will want to clear regulation.
        timestamps = pd.date_range(start="2026-01-01", periods=48, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [40.0] * 48,
            'REG_CAP': [30.0] * 48,
            'REG_MIL': [2.0] * 48,
            'SPIN': [0.0] * 48,
            'SUPP': [0.0] * 48
        })
        
        # 1. Solve with throughput factor = 0.0 (baseline) -> Should stay idle with 0 charging
        df_opt_0 = miso_opt_0.run_optimization_dispatch(df)
        self.assertEqual(df_opt_0['charge_mw'].sum(), 0.0, "Battery charged in MISO with 0.0 throughput factor")
        self.assertAlmostEqual(df_opt_0['soc_mwh'].iloc[-1], miso_opt_0.initial_soc, places=3, msg="SoC drifted even with 0.0 throughput")
        
        # 2. Solve with throughput factor = 0.15 -> Should have charging cycles to replenish RTE losses
        df_opt_15 = miso_opt_15.run_optimization_dispatch(df)
        self.assertTrue(df_opt_15['charge_mw'].sum() > 0.0, "Battery failed to charge to replenish regulation RTE losses")
        
        # Verify that the physical SOC constraint (soc_mwh >= REG_MW * dur_reg) is met in every hour
        for t in range(len(df_opt_15)):
            soc = df_opt_15['soc_mwh'].iloc[t]
            reg = df_opt_15['REG_MW'].iloc[t]
            self.assertTrue(soc >= reg - 1e-3, f"SOC {soc} went below REG_MW {reg} at index {t}")

    def test_gross_revenue_reporting_and_no_double_subtraction(self):
        """Verifies that product revenues are gross (no O&M/degradation cost subtracted)
        and that Net Merchant Revenue = Gross Revenues - Total Degradation Cost.
        """
        pjm_opt = PJM_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.5,
            degradation_cost_per_mwh=10.0, mileage_factor=0.20
        )
        df = pjm_opt.generate_sample_data(days=1)
        df_opt = pjm_opt.run_optimization_dispatch(df)
        
        # Verify that the net merchant revenue equals energy + ancillary + capacity - total degradation
        for t in range(len(df_opt)):
            energy_rev = df_opt['Energy_Revenue'].iloc[t]
            anc_rev = df_opt['Ancillary_Revenue'].iloc[t]
            cap_rev = df_opt['Capacity_Revenue'].iloc[t]
            deg_cost = df_opt['Total_Degradation_Cost'].iloc[t]
            net_rev = df_opt['revenue'].iloc[t]
            
            # Check relation: net_rev == energy_rev + anc_rev + cap_rev - deg_cost
            self.assertAlmostEqual(net_rev, energy_rev + anc_rev + cap_rev - deg_cost, places=3)
            
            # Check that individual RegA/RegD revenues do not subtract degradation internally (they are gross)
            rega_mw = df_opt['RegA_MW'].iloc[t]
            if rega_mw > 1e-3:
                perf_a = pjm_opt.config.get("default_performance_score", {}).get("RegA", 0.90)
                gross_rega = rega_mw * (df_opt['RMCCP_A'].iloc[t] * perf_a + df_opt['RMPCP_A'].iloc[t] * df_opt['Mileage_RegA'].iloc[t] * perf_a)
                self.assertAlmostEqual(df_opt['RegA_Revenue'].iloc[t], gross_rega, places=3)

    def test_miso_degradation_scaling_scaled_by_m_to_c_ratio(self):
        """Verifies that MISO regulation degradation cost is properly scaled by self.m_to_c_ratio (7.2)."""
        miso_opt = MISO_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.5,
            degradation_cost_per_mwh=10.0, mileage_factor=0.20
        )
        df = miso_opt.generate_sample_data(days=1)
        df_opt = miso_opt.run_optimization_dispatch(df)
        
        for t in range(len(df_opt)):
            reg_mw = df_opt['REG_MW'].iloc[t]
            if reg_mw > 1e-3:
                expected_reg_deg = reg_mw * miso_opt.m_to_c_ratio * 1.0 * miso_opt.deg_cost * miso_opt.mileage_factor
                total_deg = df_opt['Total_Degradation_Cost'].iloc[t]
                energy_deg = df_opt['Energy_Degradation_Cost'].iloc[t]
                self.assertAlmostEqual(total_deg - energy_deg, expected_reg_deg, places=3)

    def test_tolling_agreement_energy_revenue_calculation(self):
        """Verifies that in tolling agreement mode, Energy_Revenue does not subtract charging costs."""
        # Create a day of data where we charge at positive prices and discharge at higher prices
        timestamps = pd.date_range(start="2026-01-01", periods=24, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [40.0] * 24,
            'Reg_Price': [0.0] * 24
        })
        df.loc[2:4, 'LMP'] = 20.0  # Charge here
        df.loc[18:19, 'LMP'] = 100.0 # Discharge here
        
        toller = Generic_Optimizer(
            power_mw=self.power_mw, duration_hr=self.duration_hr, rte=self.rte,
            max_cycles_per_day=self.max_cycles, initial_soc_pct=0.5,
            degradation_cost_per_mwh=5.0, mileage_factor=0.10, is_tolling=True
        )
        df_opt = toller.run_optimization_dispatch(df)
        
        # Verify Energy_Revenue = discharge_mw * LMP (charging cost is not subtracted)
        total_discharge_rev = (df_opt['discharge_mw'] * df_opt['LMP']).sum()
        total_energy_rev = df_opt['Energy_Revenue'].sum()
        self.assertAlmostEqual(total_energy_rev, total_discharge_rev, places=3)
        self.assertTrue(df_opt['charge_mw'].sum() > 0.0) # Confirms BESS did charge

    def test_multiple_timestep_resolutions(self):
        """Verifies optimization, SOC bounds, and SOC reserve duration requirements
        across multiple sub-hourly and hourly resolutions (5-min, 15-min, 30-min, 60-min).
        """
        pjm_opt = PJM_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.5,
            degradation_cost_per_mwh=5.0, mileage_factor=0.10
        )
        
        resolutions = [
            ('5min', 12 * 24),   # 5-minute data
            ('15min', 4 * 24),   # 15-minute data
            ('30min', 2 * 24),   # 30-minute data
            ('h', 24)            # Hourly data
        ]
        
        for freq, periods in resolutions:
            timestamps = pd.date_range(start="2026-01-01", periods=periods, freq=freq)
            dt = (timestamps[1] - timestamps[0]).total_seconds() / 3600.0
            
            # Simple price profile with a high-price spike to trigger discharging
            df = pd.DataFrame({
                'timestamp': timestamps,
                'LMP': [30.0] * periods,
                'RMCCP_A': [10.0] * periods,
                'RMPCP_A': [2.0] * periods,
                'RMCCP_D': [0.0] * periods,
                'RMPCP_D': [0.0] * periods,
                'Mileage_RegA': [1.2] * periods,
                'Mileage_RegD': [0.0] * periods,
                'Price_SYNCH': [0.0] * periods,
                'Price_NONSYNCH': [0.0] * periods
            })
            # Make HE 18 spike to 200.0 for discharging
            spike_idx = int(18 / dt)
            df.loc[spike_idx:spike_idx+1, 'LMP'] = 200.0
            
            df_opt = pjm_opt.run_optimization_dispatch(df)
            
            # 1. Verify SOC balance holds exactly for this dt
            current_soc = pjm_opt.initial_soc
            eff_c = pjm_opt.eff_c
            eff_d = pjm_opt.eff_d
            
            for t in range(len(df_opt)):
                charge = df_opt['charge_mw'].iloc[t]
                discharge = df_opt['discharge_mw'].iloc[t]
                soc = df_opt['soc_mwh'].iloc[t]
                rega = df_opt['RegA_MW'].iloc[t]
                
                soc_impact = rega * pjm_opt.reg_throughput_factor * (eff_c - 1.0 / eff_d) * dt
                expected_soc = current_soc + charge * eff_c * dt - (discharge / eff_d) * dt + soc_impact
                
                self.assertAlmostEqual(soc, expected_soc, places=2, 
                                       msg=f"SOC mismatch at index {t} for resolution {freq}")
                current_soc = soc
                
                # 2. Verify sub-hourly SOC reserve duration constraint:
                # RegA has duration 1.0h, so minimum SOC req is 10.0 * 1.0 = 10.0 MWh.
                # It should NOT be scaled down by dt (which would be 0.83 MWh for 5-min or 2.5 MWh for 15-min).
                if rega > 1e-3:
                    self.assertTrue(soc >= rega * 1.0 - 1e-3, 
                                    f"Sub-hourly SOC constraint failed for {freq}: soc={soc}, rega={rega}")

    def test_revenue_objective_reconciliation(self):
        """Validates that reported net revenue exactly reconciles to the sum of
        Energy_Revenue + Ancillary_Revenue + Capacity_Revenue - Total_Degradation_Cost.
        """
        pjm_opt = PJM_Optimizer(
            power_mw=5.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.5, initial_soc_pct=0.5,
            degradation_cost_per_mwh=8.0, mileage_factor=0.15,
            capacity_price_mw_day=100.0
        )
        df = pjm_opt.generate_sample_data(days=2)
        df_opt = pjm_opt.run_optimization_dispatch(df)
        
        # Verify net hourly revenue column equals component sum in every single interval
        for t in range(len(df_opt)):
            comp_net = (df_opt['Energy_Revenue'].iloc[t] + 
                        df_opt['Ancillary_Revenue'].iloc[t] + 
                        df_opt['Capacity_Revenue'].iloc[t] - 
                        df_opt['Total_Degradation_Cost'].iloc[t])
            self.assertAlmostEqual(df_opt['revenue'].iloc[t], comp_net, places=4)

    def test_deterministic_benchmark_scenario(self):
        """Verifies optimization logic against a simple deterministic benchmark scenario
        with a known mathematically optimal solution (100% RTE, no degradation).
        """
        # 10 MW BESS with 40 MWh capacity (4h duration), 100% RTE, 0 degradation
        benchmark_opt = Generic_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=1.0,
            max_cycles_per_day=1.0, initial_soc_pct=0.0,  # Start empty
            degradation_cost_per_mwh=0.0, mileage_factor=0.0
        )
        
        timestamps = pd.date_range(start="2026-01-01", periods=24, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [30.0] * 24,
            'Reg_Price': [0.0] * 24
        })
        # Set exact low price to charge
        df.loc[8, 'LMP'] = 10.0   # HE 9: Price is $10 -> Charge
        # Set exact high price to discharge
        df.loc[17, 'LMP'] = 150.0 # HE 18: Price is $150 -> Discharge
        
        df_opt = benchmark_opt.run_optimization_dispatch(df)
        
        # Verify perfect foresight dispatch matches the exact known optimal schedule
        # Charge at HE 9 (index 8): 10 MW
        self.assertAlmostEqual(df_opt['charge_mw'].iloc[8], 10.0, places=3)
        # Discharge at HE 18 (index 17): 10 MW
        self.assertAlmostEqual(df_opt['discharge_mw'].iloc[17], 10.0, places=3)
        # Net revenue should be exactly 10 MW * 1h * ($150 - $10) = $1,400.0
        self.assertAlmostEqual(df_opt['revenue'].sum(), 1400.0, places=3)

    def test_stress_testing_flat_prices(self):
        """Verifies BESS remains idle when prices are flat and degradation/losses are present."""
        flat_opt = Generic_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.0,
            degradation_cost_per_mwh=5.0, mileage_factor=0.10
        )
        timestamps = pd.date_range(start="2026-01-01", periods=24, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [30.0] * 24, # Flat LMP
            'Reg_Price': [0.0] * 24 # No Ancillary service payments
        })
        df_opt = flat_opt.run_optimization_dispatch(df)
        
        # BESS should stay completely idle because arbitrage cannot cover RTE loss and degradation
        self.assertAlmostEqual(df_opt['charge_mw'].sum(), 0.0, places=3)
        self.assertAlmostEqual(df_opt['discharge_mw'].sum(), 0.0, places=3)

    def test_stress_testing_negative_prices(self):
        """Verifies that BESS charges during negative prices even if there is no high discharge price,
        as charging at a negative price is directly profitable.
        """
        neg_opt = Generic_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.0,  # Start empty
            degradation_cost_per_mwh=5.0, mileage_factor=0.10
        )
        timestamps = pd.date_range(start="2026-01-01", periods=24, freq='h')
        df = pd.DataFrame({
            'timestamp': timestamps,
            'LMP': [0.0] * 24,
            'Reg_Price': [0.0] * 24
        })
        # Set negative prices during HE 3-6 (index 2-5)
        df.loc[2:5, 'LMP'] = -50.0
        
        df_opt = neg_opt.run_optimization_dispatch(df)
        
        # Battery should charge during negative prices (making profit directly by charging)
        charge_neg = df_opt['charge_mw'].iloc[2:6].sum()
        self.assertTrue(charge_neg > 0.0)
        
        # Verify that it got paid for charging
        energy_rev = df_opt['Energy_Revenue'].sum()
        self.assertTrue(energy_rev > 0.0) # Net energy revenue must be positive since we charged at -$50

    def test_market_rule_enforcement_rega_regd_exclusivity(self):
        """Verifies PJM-specific market rule: RegA and RegD are mutually exclusive."""
        pjm_opt = PJM_Optimizer(
            power_mw=10.0, duration_hr=4.0, rte=0.90,
            max_cycles_per_day=1.0, initial_soc_pct=0.5
        )
        df = pjm_opt.generate_sample_data(days=5)
        df_opt = pjm_opt.run_optimization_dispatch(df)
        
        # Verify that in no hour did the battery clear both RegA and RegD simultaneously
        for t in range(len(df_opt)):
            rega = df_opt['RegA_MW'].iloc[t]
            regd = df_opt['RegD_MW'].iloc[t]
            self.assertFalse(rega > 1e-3 and regd > 1e-3, 
                             f"RegA ({rega} MW) and RegD ({regd} MW) cleared simultaneously at index {t}")

if __name__ == '__main__':
    unittest.main()
