# market_isone.py
"""
ISO-NE Market Optimizer for FTM BESS assets (Rhode Island / Narragansett Electric WDT).
"""
import pandas as pd
import numpy as np
import pulp
from core_optimizer import BESS_Simulator_Base
from typing import Optional
from datetime import date


class ISONE_Optimizer(BESS_Simulator_Base):
    """
    ISO-NE Market Optimizer for FTM BESS assets.

    Revenue Streams:
      - Energy Arbitrage (DA-LMP / RT-LMP)
      - Forward Capacity Market (FCM) with ELCC derate
      - Regulation Up/Down (Capacity + Mileage)
      - Spinning & Non-Spinning Reserves (optional)

    Cost Streams:
      - WDT Demand Charges (Narragansett Electric / Rhode Island Energy
        Electric Tariff No. 3, Sections 4 & 5):
          On-Peak (weekdays 14:00-19:00):  $10.55/kW-month
          Mid-Peak (weekdays 13-14, 19-20; weekends/holidays 13-20): $1.35/kW-month
          Off-Peak (all other):  $0.53/kW-month
          Customer Charge: $32.82/month
      - Battery Degradation ($/MWh throughput)

    CRITICAL CONSTRAINTS:
      1. Charging is PROHIBITED during weekday on-peak hours (14:00-19:00)
         to avoid the $10.55/kW-month demand charge (when apply_wdt_charges=True).
      2. All dispatch is computed at 15-minute resolution. Hourly input data
         is upsampled to 15-minute intervals before optimization.
    """

    # WDT Tariff Rates (Narragansett Electric, Electric Tariff No. 3)
    ONPEAK_RATE = 10.55      # $/kW-month
    MIDPEAK_RATE = 1.35      # $/kW-month
    OFFPEAK_RATE = 0.53      # $/kW-month
    CUSTOMER_CHARGE = 32.82  # $/month
    DAYS_PER_MONTH = 30.44   # average for $/kW-month → hourly conversion

    @staticmethod
    def get_mri_accreditation(duration_hr: float) -> float:
        """
        ISO-NE Capacity Auction Reform (CAR 2028+) Marginal Reliability Impact (MRI)
        duration-based accreditation factor for BESS:
          - 2-hr: ~22%
          - 3-hr: ~31%
          - 4-hr: ~40%
          - 6-hr: ~68%
          - 8-hr+: ~85%
        """
        durations = [2.0, 3.0, 4.0, 6.0, 8.0]
        factors = [0.22, 0.31, 0.40, 0.68, 0.85]
        if duration_hr <= durations[0]:
            return factors[0]
        if duration_hr >= durations[-1]:
            return factors[-1]
        return float(np.interp(duration_hr, durations, factors))

    def __init__(
        self,
        power_mw: float = 4.5,
        duration_hr: float = 3.0,
        rte: float = 0.90,
        max_cycles_per_day: float = 1.0,
        initial_soc_pct: float = 0.5,
        degradation_cost_per_mwh: float = 5.0,
        mileage_factor: float = 4.0,
        capacity_price_kw_month: float = 3.50,
        reg_throughput_factor: float = 0.15,
        is_tolling: bool = False,
        elcc_factor: float = None,
        reg_cap_pct: float = 100.0,
        enable_reserves: bool = True,
        apply_wdt_charges: bool = True,
        # CAR 2028+ Reform & Seasonal Parameters
        capacity_pricing_mode: str = "Seasonal",  # "Seasonal" or "Annual Flat"
        summer_capacity_price_kw_month: float = 4.50,
        winter_capacity_price_kw_month: float = 2.50,
        enable_pfp_protection: bool = True,
        pfp_min_soc_pct: float = 0.15,
        **kwargs
    ):
        super().__init__(
            power_mw=power_mw,
            duration_hr=duration_hr,
            rte=rte,
            max_cycles_per_day=max_cycles_per_day,
            initial_soc_pct=initial_soc_pct,
            degradation_cost_per_mwh=degradation_cost_per_mwh,
            mileage_factor=mileage_factor,
            market_name="ISO-NE",
            reg_throughput_factor=reg_throughput_factor,
            is_tolling=is_tolling,
        )

        self.capacity_pricing_mode = str(
            kwargs.get("capacity_pricing_mode", capacity_pricing_mode)
        )
        self.summer_capacity_price_kw_month = float(
            kwargs.get("summer_capacity_price_kw_month", summer_capacity_price_kw_month)
        )
        self.winter_capacity_price_kw_month = float(
            kwargs.get("winter_capacity_price_kw_month", winter_capacity_price_kw_month)
        )
        self.capacity_price_kw_month = float(
            kwargs.get("capacity_price_kw_month", capacity_price_kw_month)
        )
        
        # If elcc_factor is not specified, derive from MRI accreditation curve
        raw_elcc = kwargs.get("elcc_factor", elcc_factor)
        if raw_elcc is None:
            self.elcc_factor = self.get_mri_accreditation(self.duration_hr)
        else:
            self.elcc_factor = float(raw_elcc)
        self.config["elcc_factor"] = self.elcc_factor

        self.enable_pfp_protection = bool(
            kwargs.get("enable_pfp_protection", enable_pfp_protection)
        )
        self.pfp_min_soc_pct = float(
            kwargs.get("pfp_min_soc_pct", pfp_min_soc_pct)
        )

        # Weighted $/MW-day for dashboard compatibility with other markets
        if self.capacity_pricing_mode == "Seasonal":
            avg_monthly = (self.summer_capacity_price_kw_month * 6.0 + self.winter_capacity_price_kw_month * 6.0) / 12.0
            self.capacity_price_mw_day = avg_monthly * 1000.0 / self.DAYS_PER_MONTH
        else:
            self.capacity_price_mw_day = (
                self.capacity_price_kw_month * 1000.0 / self.DAYS_PER_MONTH
            )
        self.reg_cap_pct = float(kwargs.get("reg_cap_pct", reg_cap_pct))
        self.enable_reserves = bool(kwargs.get("enable_reserves", enable_reserves))
        self.apply_wdt_charges = bool(kwargs.get("apply_wdt_charges", apply_wdt_charges))
        self.max_reg_mw = max(0.0, (self.reg_cap_pct / 100.0) * self.power_mw)
        self._wdt_monthly_df = None
        self._holiday_cache = {}

    # ------------------------------------------------------------------
    # Holidays & WDT period classification
    # ------------------------------------------------------------------
    def _nth_weekday(self, year: int, month: int, weekday: int, n: int) -> date:
        """Return the nth occurrence of weekday (Mon=0) in month."""
        d = date(year, month, 1)
        # Advance to first matching weekday
        offset = (weekday - d.weekday()) % 7
        d = date(year, month, 1 + offset)
        d = date(year, month, d.day + 7 * (n - 1))
        return d

    def _last_weekday(self, year: int, month: int, weekday: int) -> date:
        if month == 12:
            d = date(year + 1, 1, 1)
        else:
            d = date(year, month + 1, 1)
        d = d.toordinal() - 1
        d = date.fromordinal(d)
        while d.weekday() != weekday:
            d = date.fromordinal(d.toordinal() - 1)
        return d

    def _us_holidays_for_year(self, year: int) -> set:
        """ISO-NE / federal holidays used for WDT peak classification."""
        if year in self._holiday_cache:
            return self._holiday_cache[year]

        holidays = {
            date(year, 1, 1),                                          # New Year's Day
            self._nth_weekday(year, 1, 0, 3),                          # MLK Day (3rd Mon Jan)
            self._nth_weekday(year, 2, 0, 3),                          # Presidents' Day
            self._last_weekday(year, 5, 0),                            # Memorial Day
            date(year, 6, 19),                                         # Juneteenth
            date(year, 7, 4),                                          # Independence Day
            self._nth_weekday(year, 9, 0, 1),                          # Labor Day
            self._nth_weekday(year, 10, 0, 2),                         # Columbus / Indigenous Peoples'
            date(year, 11, 11),                                        # Veterans Day
            self._nth_weekday(year, 11, 3, 4),                         # Thanksgiving (4th Thu Nov)
            date(year, 12, 25),                                        # Christmas
        }
        self._holiday_cache[year] = holidays
        return holidays

    def _is_holiday(self, ts) -> bool:
        d = ts.date() if hasattr(ts, "date") else ts
        return d in self._us_holidays_for_year(d.year)

    def _classify_period(self, ts) -> str:
        """Classify a timestamp into 'onpeak', 'midpeak', or 'offpeak'."""
        hour = ts.hour
        if self._is_holiday(ts):
            if 13 <= hour < 20:
                return "midpeak"
            return "offpeak"
        if ts.weekday() < 5:  # Mon–Fri
            if 14 <= hour < 19:
                return "onpeak"
            if hour == 13 or hour == 19:
                return "midpeak"
        else:  # Weekend
            if 13 <= hour < 20:
                return "midpeak"
        return "offpeak"

    def _is_onpeak_no_charge(self, ts) -> bool:
        """Weekday non-holiday 14:00–19:00 — hard no-charge window."""
        return self._classify_period(ts) == "onpeak"

    # ------------------------------------------------------------------
    # 15-minute resolution enforcement
    # ------------------------------------------------------------------
    def ensure_15min_resolution(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Upsample hourly data to 15-minute intervals so WDT demand peaks
        are computed on true 15-min charging decisions.
        """
        df = df.copy()
        if "timestamp" not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex) or df.index.name == "timestamp":
                df = df.reset_index().rename(columns={"index": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        if len(df) < 2:
            return df

        dt_hours = (
            df["timestamp"].iloc[1] - df["timestamp"].iloc[0]
        ).total_seconds() / 3600.0

        if abs(dt_hours - 0.25) < 0.02:
            return df

        if abs(dt_hours - 1.0) < 0.05:
            rows = []
            price_cols = [
                c for c in df.columns
                if c != "timestamp" and pd.api.types.is_numeric_dtype(df[c])
            ]
            for _, row in df.iterrows():
                base_ts = row["timestamp"]
                for k in range(4):
                    new_row = row.copy()
                    new_row["timestamp"] = base_ts + pd.Timedelta(minutes=15 * k)
                    rows.append(new_row)
            out = pd.DataFrame(rows).reset_index(drop=True)
            # Forward-fill prices across sub-intervals (hourly flat within the hour)
            for col in price_cols:
                out[col] = out[col].astype(float)
            return out

        # Already sub-hourly but not exactly 15-min: resample to 15-min mean
        if dt_hours < 1.0:
            df = df.set_index("timestamp")
            numeric = df.select_dtypes(include=[np.number]).resample("15min").mean()
            numeric = numeric.ffill().bfill()
            numeric = numeric.reset_index()
            return numeric

        return df

    def run_optimization_dispatch(self, df, enforce_15min: bool = True, **kwargs):
        """
        If enforce_15min is True (default), automatically upsamples hourly data 
        to 15-minute resolution to model RIE WDT 15-minute billing demand peaks.
        If enforce_15min is False, runs directly at the data's native resolution (e.g. 1-hour).
        """
        if enforce_15min:
            df_proc = self.ensure_15min_resolution(df)
        else:
            df_proc = df.copy()
            if "timestamp" not in df_proc.columns and (isinstance(df_proc.index, pd.DatetimeIndex) or df_proc.index.name == "timestamp"):
                df_proc = df_proc.reset_index().rename(columns={"index": "timestamp"})
            df_proc["timestamp"] = pd.to_datetime(df_proc["timestamp"])
            df_proc = df_proc.sort_values("timestamp").reset_index(drop=True)
        return super().run_optimization_dispatch(df_proc, **kwargs)

    # ------------------------------------------------------------------
    # Sample data
    # ------------------------------------------------------------------
    def generate_sample_data(self, days: int = 365, freq: str = "15min") -> pd.DataFrame:
        """Generate synthetic ISO-NE prices at 15-minute resolution."""
        if freq in ("1h", "H", "h"):
            freq = "15min"
        periods = int(days * 24 * 4)  # 15-min intervals
        timestamps = pd.date_range(start="2026-01-01", periods=periods, freq="15min")
        df = pd.DataFrame({"timestamp": timestamps})

        hours = df["timestamp"].dt.hour + df["timestamp"].dt.minute / 60.0
        months = df["timestamp"].dt.month
        winter_mult = np.where((months == 1) | (months == 2) | (months == 12), 1.40, 1.0)
        summer_mult = np.where((months >= 6) & (months <= 8), 1.25, 1.0)
        seasonal_mult = np.maximum(winter_mult, summer_mult)

        base_lmp = 32.0 + 38.0 * np.sin((hours - 12) * np.pi / 12) ** 2 * seasonal_mult
        noise = np.random.normal(0, 4.5, len(df))
        df["LMP"] = np.clip(base_lmp + noise, -20.0, None)

        spike_indices = np.random.choice(df.index, size=max(1, int(len(df) * 0.03)), replace=False)
        df.loc[spike_indices, "LMP"] += np.random.uniform(50, 220, size=len(spike_indices))

        df["Charge_LMP"] = df["LMP"].copy()

        # Regulation capacity prices (RMCCP-equivalent)
        df["Price_RegUp"] = np.clip(
            18.0 + 22.0 * np.sin((hours - 8) * np.pi / 12) ** 2 * seasonal_mult
            + np.random.normal(0, 3, len(df)),
            2.0,
            None,
        )
        df["Price_RegDn"] = np.clip(
            16.0 + 20.0 * np.sin((hours - 8) * np.pi / 12) ** 2 * seasonal_mult
            + np.random.normal(0, 3, len(df)),
            2.0,
            None,
        )
        df.loc[spike_indices, "Price_RegUp"] += np.random.uniform(30, 100, size=len(spike_indices))
        df.loc[spike_indices, "Price_RegDn"] += np.random.uniform(25, 90, size=len(spike_indices))

        df["Price_Reg_Service"] = np.clip(np.random.uniform(1.0, 4.0, len(df)), 0.5, None)
        df["Price_Spin"] = np.clip(np.random.lognormal(mean=1.1, sigma=0.45, size=len(df)), 1.5, 40.0)
        df["Price_NonSpin"] = np.clip(np.random.lognormal(mean=0.7, sigma=0.4, size=len(df)), 0.8, 25.0)
        df["CAP_LIMIT"] = self.power_mw

        return df

    # ------------------------------------------------------------------
    # LP hooks
    # ------------------------------------------------------------------
    def get_market_soc_impact(self, subclass_vars, t, timestep_hours, is_value=False):
        """SoC impact from regulation AGC: (reg_dn - reg_up) * throughput * dt."""
        reg_up = subclass_vars["reg_up"][t]
        reg_dn = subclass_vars["reg_dn"][t]
        up_val = reg_up.varValue if is_value else reg_up
        dn_val = reg_dn.varValue if is_value else reg_dn
        if up_val is None:
            up_val = 0.0
        if dn_val is None:
            dn_val = 0.0
        return (dn_val - up_val) * self.reg_throughput_factor * timestep_hours

    def define_market_variables(self, prob, T_day):
        """Create ISO-NE regulation and reserve LP variables."""
        reg_up = {
            t: pulp.LpVariable(f"RegUp_{t}", lowBound=0, upBound=self.max_reg_mw)
            for t in range(T_day)
        }
        reg_dn = {
            t: pulp.LpVariable(f"RegDn_{t}", lowBound=0, upBound=self.max_reg_mw)
            for t in range(T_day)
        }
        spin_ub = self.power_mw if self.enable_reserves else 0.0
        nonspin_ub = self.power_mw if self.enable_reserves else 0.0
        spin = {
            t: pulp.LpVariable(f"Spin_{t}", lowBound=0, upBound=spin_ub)
            for t in range(T_day)
        }
        nonspin = {
            t: pulp.LpVariable(f"NonSpin_{t}", lowBound=0, upBound=nonspin_ub)
            for t in range(T_day)
        }
        return {
            "reg_up": reg_up,
            "reg_dn": reg_dn,
            "spin": spin,
            "nonspin": nonspin,
        }

    def add_market_constraints(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Power headroom, SoC buffers, and hard on-peak no-charge constraint."""
        reg_up = subclass_vars["reg_up"]
        reg_dn = subclass_vars["reg_dn"]
        spin = subclass_vars["spin"]
        nonspin = subclass_vars["nonspin"]

        # Timestamps from current day window or rolling forecast (handles both column and DatetimeIndex)
        timestamps = None
        for candidate in [getattr(self, "current_df", None), df_prices]:
            if candidate is not None:
                if "timestamp" in candidate.columns:
                    timestamps = pd.to_datetime(candidate["timestamp"].values)
                    break
                elif isinstance(candidate.index, pd.DatetimeIndex) or candidate.index.name == "timestamp":
                    timestamps = pd.to_datetime(candidate.index.values)
                    break

        dur_spin = self.config.get("reserve_durations", {}).get("Spin", 0.50)
        dur_nonspin = self.config.get("reserve_durations", {}).get("NonSpin", 0.50)
        dur_reg = self.config.get("reserve_durations", {}).get("Reg", 0.50)

        for t in range(T_day):
            # Regulation capacity ceiling (combined up+dn)
            prob += reg_up[t] + reg_dn[t] <= self.max_reg_mw

            # Shared inverter / power headroom
            prob += (
                d[t] + reg_up[t] + spin[t] + nonspin[t] <= self.power_mw
            )
            prob += c[t] + reg_dn[t] <= self.power_mw
            prob += (
                spin[t] + nonspin[t] + reg_up[t] + reg_dn[t] <= self.power_mw
            )

            # SoC headroom for regulation & reserves
            as_energy = (
                (reg_up[t] + reg_dn[t]) * dur_reg
                + spin[t] * dur_spin
                + nonspin[t] * dur_nonspin
            )

            # Pay-for-Performance (PfP) Scarcity Safeguard ($9,337/MWh scarcity penalty protection):
            # During peak risk hours (weekdays 12:00 - 20:00), maintain an emergency SoC buffer
            # (default 15% SoC) so the asset never sits empty during an ISO-NE Capacity Scarcity Condition (CSC).
            ts_t = pd.Timestamp(timestamps[t]) if (timestamps is not None and t < len(timestamps)) else None
            is_pfp_risk = False
            if ts_t is not None:
                if ts_t.weekday() < 5 and (12 <= ts_t.hour < 20):
                    is_pfp_risk = True

            target_floor_pct = self.pfp_min_soc_pct if (self.enable_pfp_protection and is_pfp_risk) else 0.10
            prob += soc[t] >= (target_floor_pct * self.energy_mwh) + as_energy * timestep_hours
            prob += soc[t] <= (0.90 * self.energy_mwh) - (
                (reg_up[t] + reg_dn[t]) * dur_reg * timestep_hours
            )

            # HARD CONSTRAINT: no charging during weekday on-peak (WDT)
            if self.apply_wdt_charges and timestamps is not None and t < len(timestamps):
                if self._is_onpeak_no_charge(pd.Timestamp(timestamps[t])):
                    prob += c[t] == 0

            if not self.enable_reserves:
                prob += spin[t] == 0
                prob += nonspin[t] == 0

    def get_objective_expression(self, prob, c, d, soc, subclass_vars, df_prices, T_day, timestep_hours):
        """Net regulation + reserve revenue (capacity + mileage − reg degradation)."""
        reg_up = subclass_vars["reg_up"]
        reg_dn = subclass_vars["reg_dn"]
        spin = subclass_vars["spin"]
        nonspin = subclass_vars["nonspin"]

        price_reg_up = (
            df_prices["Price_RegUp"].values
            if "Price_RegUp" in df_prices.columns
            else np.zeros(T_day)
        )
        # ISO-NE regulation is a single symmetric capacity clearing price (RMCCP)
        if "Price_RegDn" in df_prices.columns and (df_prices["Price_RegDn"].values > 0).any():
            price_reg_dn = df_prices["Price_RegDn"].values
        else:
            price_reg_dn = price_reg_up
        price_reg_svc = (
            df_prices["Price_Reg_Service"].values
            if "Price_Reg_Service" in df_prices.columns
            else np.zeros(T_day)
        )
        price_spin = (
            df_prices["Price_Spin"].values
            if "Price_Spin" in df_prices.columns
            else np.zeros(T_day)
        )
        price_nonspin = (
            df_prices["Price_NonSpin"].values
            if "Price_NonSpin" in df_prices.columns
            else np.zeros(T_day)
        )

        terms = []
        for t in range(T_day):
            reg_cap_rev = (
                reg_up[t] * price_reg_up[t] + reg_dn[t] * price_reg_dn[t]
            ) * timestep_hours
            reg_mileage = (
                (reg_up[t] + reg_dn[t])
                * price_reg_svc[t]
                * self.mileage_factor
                * timestep_hours
            )
            reg_deg = (
                (reg_up[t] + reg_dn[t])
                * self.reg_throughput_factor
                * self.deg_cost
                * timestep_hours
            )
            reserve_rev = (
                spin[t] * price_spin[t] + nonspin[t] * price_nonspin[t]
            ) * timestep_hours
            terms.append(reg_cap_rev + reg_mileage - reg_deg + reserve_rev)

        return pulp.lpSum(terms)

    def extract_market_results(self, subclass_vars, day_indices):
        """Extract cleared AS awards after solve."""
        reg_up = [subclass_vars["reg_up"][t].varValue or 0.0 for t in day_indices]
        reg_dn = [subclass_vars["reg_dn"][t].varValue or 0.0 for t in day_indices]
        spin = [subclass_vars["spin"][t].varValue or 0.0 for t in day_indices]
        nonspin = [subclass_vars["nonspin"][t].varValue or 0.0 for t in day_indices]
        total_reg = [u + d for u, d in zip(reg_up, reg_dn)]
        return {
            "RegUp_MW": reg_up,
            "RegDn_MW": reg_dn,
            "Spin_MW": spin,
            "NonSpin_MW": nonspin,
            "Total_Reg_MW": total_reg,
        }

    # ------------------------------------------------------------------
    # WDT demand charges
    # ------------------------------------------------------------------
    def calculate_wdt_demand_charges(self, df_out: pd.DataFrame) -> pd.DataFrame:
        """
        Compute monthly WDT demand charges from 15-min charging peaks per period.
        """
        df = df_out.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if "period" not in df.columns:
            df["period"] = df["timestamp"].apply(self._classify_period)
        df["year_month"] = df["timestamp"].dt.to_period("M")

        monthly_charges = []
        for ym, grp in df.groupby("year_month"):
            def _peak_kw(period_name):
                mask = grp["period"] == period_name
                if not mask.any():
                    return 0.0
                return float(grp.loc[mask, "charge_mw"].max()) * 1000.0

            onpeak_max_kw = _peak_kw("onpeak")
            midpeak_max_kw = _peak_kw("midpeak")
            offpeak_max_kw = _peak_kw("offpeak")

            charge = (
                self.CUSTOMER_CHARGE
                + onpeak_max_kw * self.ONPEAK_RATE
                + midpeak_max_kw * self.MIDPEAK_RATE
                + offpeak_max_kw * self.OFFPEAK_RATE
            )
            monthly_charges.append(
                {
                    "year_month": str(ym),
                    "OnPeak_Max_kW": onpeak_max_kw,
                    "MidPeak_Max_kW": midpeak_max_kw,
                    "OffPeak_Max_kW": offpeak_max_kw,
                    "WDT_Demand_Charge": charge,
                }
            )

        result = pd.DataFrame(monthly_charges)
        self._wdt_monthly_df = result
        return result

    def calculate_market_revenues(self, df_out, timestep_hours):
        """Post-optimization revenue columns including FCM and WDT."""
        df_out = df_out.copy()
        df_out["timestamp"] = pd.to_datetime(df_out["timestamp"])
        df_out["period"] = df_out["timestamp"].apply(self._classify_period)

        price_reg_up = (
            df_out["Price_RegUp"] if "Price_RegUp" in df_out.columns else 0.0
        )
        # ISO-NE regulation is a single symmetric capacity clearing price (RMCCP)
        if "Price_RegDn" in df_out.columns and (isinstance(df_out["Price_RegDn"], (pd.Series, np.ndarray)) and (df_out["Price_RegDn"] > 0).any()):
            price_reg_dn = df_out["Price_RegDn"]
        else:
            price_reg_dn = price_reg_up
        price_reg_svc = (
            df_out["Price_Reg_Service"]
            if "Price_Reg_Service" in df_out.columns
            else 0.0
        )
        price_spin = df_out["Price_Spin"] if "Price_Spin" in df_out.columns else 0.0
        price_nonspin = (
            df_out["Price_NonSpin"] if "Price_NonSpin" in df_out.columns else 0.0
        )

        reg_up = df_out["RegUp_MW"] if "RegUp_MW" in df_out.columns else 0.0
        reg_dn = df_out["RegDn_MW"] if "RegDn_MW" in df_out.columns else 0.0
        spin = df_out["Spin_MW"] if "Spin_MW" in df_out.columns else 0.0
        nonspin = df_out["NonSpin_MW"] if "NonSpin_MW" in df_out.columns else 0.0

        if "Total_Reg_MW" not in df_out.columns:
            df_out["Total_Reg_MW"] = reg_up + reg_dn

        reg_cap_rev = (reg_up * price_reg_up + reg_dn * price_reg_dn) * timestep_hours
        reg_mileage = (
            (reg_up + reg_dn) * price_reg_svc * self.mileage_factor * timestep_hours
        )
        reg_deg = (
            (reg_up + reg_dn) * self.reg_throughput_factor * self.deg_cost * timestep_hours
        )
        df_out["Regulation_Revenue"] = reg_cap_rev + reg_mileage - reg_deg

        spin_rev = spin * price_spin * timestep_hours
        nonspin_rev = nonspin * price_nonspin * timestep_hours
        df_out["Spin_Revenue"] = spin_rev
        df_out["NonSpin_Revenue"] = nonspin_rev
        df_out["Reserve_Revenue"] = spin_rev + nonspin_rev
        df_out["Ancillary_Revenue"] = (
            df_out["Regulation_Revenue"] + df_out["Reserve_Revenue"]
        )

        energy_deg = (
            df_out["Energy_Degradation_Cost"]
            if "Energy_Degradation_Cost" in df_out.columns
            else 0.0
        )
        df_out["Total_Degradation_Cost"] = energy_deg + reg_deg

        # FCM: flat $/hr from ELCC-derated capacity × $/kW-month
        # FCM: Capacity revenue with CAR 2028+ Seasonal split
        elcc = self.config.get("elcc_factor", self.elcc_factor)
        effective_mw = self.power_mw * elcc
        months = df_out["timestamp"].dt.month
        # Summer is May 1 - Oct 31 (months 5, 6, 7, 8, 9, 10)
        is_summer = months.isin([5, 6, 7, 8, 9, 10])

        if self.capacity_pricing_mode == "Seasonal":
            seasonal_rate = np.where(is_summer, self.summer_capacity_price_kw_month, self.winter_capacity_price_kw_month)
            hourly_capacity_revenue = (
                effective_mw * seasonal_rate * 1000.0
            ) / (self.DAYS_PER_MONTH * 24.0)
            df_out["Capacity_Revenue"] = hourly_capacity_revenue * timestep_hours
            df_out["Summer_Capacity_Revenue"] = np.where(is_summer, df_out["Capacity_Revenue"], 0.0)
            df_out["Winter_Capacity_Revenue"] = np.where(~is_summer, df_out["Capacity_Revenue"], 0.0)
        else:
            hourly_capacity_revenue = (
                effective_mw * self.capacity_price_kw_month * 1000.0
            ) / (self.DAYS_PER_MONTH * 24.0)
            df_out["Capacity_Revenue"] = hourly_capacity_revenue * timestep_hours
            df_out["Summer_Capacity_Revenue"] = np.where(is_summer, df_out["Capacity_Revenue"], 0.0)
            df_out["Winter_Capacity_Revenue"] = np.where(~is_summer, df_out["Capacity_Revenue"], 0.0)

        # WDT demand charges (monthly peaks → prorated per interval)
        if self.apply_wdt_charges:
            wdt_df = self.calculate_wdt_demand_charges(df_out)
            df_out["year_month"] = df_out["timestamp"].dt.to_period("M").astype(str)
            wdt_map = dict(zip(wdt_df["year_month"], wdt_df["WDT_Demand_Charge"]))
            counts = df_out["year_month"].value_counts().to_dict()
            df_out["WDT_Demand_Cost"] = df_out["year_month"].map(
                lambda ym: wdt_map.get(ym, 0.0) / max(counts.get(ym, 1), 1)
            )
            df_out.drop(columns=["year_month"], inplace=True, errors="ignore")
        else:
            df_out["WDT_Demand_Cost"] = 0.0
            self._wdt_monthly_df = pd.DataFrame(
                columns=[
                    "year_month",
                    "OnPeak_Max_kW",
                    "MidPeak_Max_kW",
                    "OffPeak_Max_kW",
                    "WDT_Demand_Charge",
                ]
            )

        df_out["revenue"] = (
            df_out["Energy_Revenue"]
            + df_out["Ancillary_Revenue"]
            + df_out["Capacity_Revenue"]
            - df_out["Total_Degradation_Cost"]
            - df_out["WDT_Demand_Cost"]
        )
        return df_out

    def calculate_summary_metrics(self, df_out):
        """Financial and operational KPIs for ISO-NE dashboard."""
        timestep_hours = 0.25
        if len(df_out) > 1:
            td = (
                df_out["timestamp"].iloc[1] - df_out["timestamp"].iloc[0]
            ).total_seconds() / 3600.0
            if td != 0:
                timestep_hours = td

        total_rev = float(df_out["revenue"].sum())
        energy_rev = float(df_out["Energy_Revenue"].sum())
        as_rev = float(df_out["Ancillary_Revenue"].sum())
        reg_rev = float(df_out["Regulation_Revenue"].sum())
        spin_rev = float(df_out["Spin_Revenue"].sum()) if "Spin_Revenue" in df_out.columns else 0.0
        nonspin_rev = (
            float(df_out["NonSpin_Revenue"].sum())
            if "NonSpin_Revenue" in df_out.columns
            else 0.0
        )
        capacity_rev = float(df_out["Capacity_Revenue"].sum())
        deg_expense = float(df_out["Total_Degradation_Cost"].sum())
        wdt_total = (
            float(df_out["WDT_Demand_Cost"].sum())
            if "WDT_Demand_Cost" in df_out.columns
            else 0.0
        )

        total_discharge_mwh = float((df_out["discharge_mw"] * timestep_hours).sum())
        total_charge_mwh = float((df_out["charge_mw"] * timestep_hours).sum())
        efc = total_discharge_mwh / self.energy_mwh if self.energy_mwh > 0 else 0.0
        achieved_rte = (
            (total_discharge_mwh / total_charge_mwh) if total_charge_mwh > 0 else 0.0
        )

        as_sum = (
            df_out.get("Total_Reg_MW", 0)
            + df_out.get("Spin_MW", 0)
            + df_out.get("NonSpin_MW", 0)
        )
        as_fraction = float((as_sum > 1e-3).mean()) if len(df_out) else 0.0

        if "period" not in df_out.columns:
            periods = pd.to_datetime(df_out["timestamp"]).apply(self._classify_period)
        else:
            periods = df_out["period"]
        onpeak_charge_hours = float(
            (
                (periods == "onpeak") & (df_out["charge_mw"] > 1e-3)
            ).sum()
            * timestep_hours
        )

        mode_counts = (
            df_out["decision"].value_counts()
            if "decision" in df_out.columns
            else pd.Series(dtype=int)
        )
        total_intervals = len(df_out)
        utilization = (
            {k: v / total_intervals for k, v in mode_counts.items()}
            if total_intervals > 0
            else {}
        )

        summer_cap_rev = float(df_out["Summer_Capacity_Revenue"].sum()) if "Summer_Capacity_Revenue" in df_out.columns else 0.0
        winter_cap_rev = float(df_out["Winter_Capacity_Revenue"].sum()) if "Winter_Capacity_Revenue" in df_out.columns else 0.0
        elcc = self.config.get("elcc_factor", self.elcc_factor)

        metrics = {
            "Total Net Merchant Revenue ($)": total_rev,
            "Energy Arbitrage Revenue ($)": energy_rev,
            "Ancillary Services Revenue ($)": as_rev,
            "Regulation Revenue ($)": reg_rev,
            "Spinning Reserve Revenue ($)": spin_rev,
            "Non-Spin Reserve Revenue ($)": nonspin_rev,
            "Static Capacity Revenue (FCM) ($)": capacity_rev,
            "Static Capacity Revenue ($)": capacity_rev,  # app.py compatibility
            "Summer Capacity Revenue ($)": summer_cap_rev,
            "Winter Capacity Revenue ($)": winter_cap_rev,
            "Capacity Market Framework": "Seasonal (2028+ CAR Reform)" if self.capacity_pricing_mode == "Seasonal" else "Annual Flat (Pre-2028 Legacy)",
            "Accreditation Derate (MRI/ELCC)": f"{elcc * 100.0:.1f}%",
            "PfP Scarcity Safeguard": "Active (15% SoC Buffer)" if self.enable_pfp_protection else "Disabled",
            "WDT Annual Demand Charges ($)": wdt_total,
            "Degradation Expense ($)": deg_expense,
            "Equivalent Full Cycles (EFC)": efc,
            "Achieved Round-Trip Efficiency": achieved_rte,
            "Charging Energy (MWh)": total_charge_mwh,
            "Discharging Energy (MWh)": total_discharge_mwh,
            "Ancillary Participation Fraction": as_fraction,
            "On-Peak Charging Hours": onpeak_charge_hours,
        }
        return metrics, utilization


if __name__ == "__main__":
    print(
        "Testing ISONE_Optimizer (4.5 MW / 13.5 MWh FTM BESS - Blue Sky Utility, Rhode Island)..."
    )
    optimizer = ISONE_Optimizer(
        power_mw=4.5,
        duration_hr=3.0,
        rte=0.90,
        max_cycles_per_day=1.0,
        capacity_price_kw_month=3.50,
        elcc_factor=0.50,
        reg_cap_pct=20.0,
        enable_reserves=True,
        apply_wdt_charges=True,
    )
    df_sample = optimizer.generate_sample_data(days=7)
    print(f"Sample rows: {len(df_sample)} | timestep check: "
          f"{(df_sample['timestamp'].iloc[1] - df_sample['timestamp'].iloc[0])}")
    df_results = optimizer.run_optimization_dispatch(df_sample)
    metrics, _ = optimizer.calculate_summary_metrics(df_results)

    print("\n=== ISO-NE 4.5 MW FTM BESS - 7-DAY OPTIMIZATION METRICS ===")
    for k, v in metrics.items():
        if isinstance(v, (int, float)):
            if "$" in k:
                print(f"{k:<50}: ${v:>12,.2f}")
            else:
                print(f"{k:<50}: {v:>12.4f}")
        else:
            print(f"{k:<50}: {str(v):>12}")

    onpeak_charge = df_results[
        (df_results["period"] == "onpeak") & (df_results["charge_mw"] > 1e-3)
    ]
    assert len(onpeak_charge) == 0, "FAIL: Battery charged during on-peak hours!"
    print("\nPASS: Zero on-peak charging confirmed.")
    print(
        f"WDT Demand Charges (period): ${metrics['WDT Annual Demand Charges ($)']:,.2f}"
    )
