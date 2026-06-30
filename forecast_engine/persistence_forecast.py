# persistence_forecast.py
import pandas as pd
import numpy as np
from forecast_engine.base_forecast import BaseForecastEngine

class PersistenceForecastEngine(BaseForecastEngine):
    def __init__(self, market_name, method="naive", rolling_days=3, mape=0.0):
        """
        method: "naive", "rolling_average", or "noisy_actual"
        mape: Mean Absolute Percentage Error for simulated forecasting (only used if method="noisy_actual")
        """
        super().__init__(market_name)
        self.method = method
        self.rolling_days = rolling_days
        self.mape = mape

    def generate_forecast(self, df_history, current_time, forecast_horizon_hrs, future_actual_df=None):
        """
        Generates forecasts starting at current_time.
        
        Parameters:
        - df_history: Historical data containing price columns. Must have a timestamp index.
        - current_time: pd.Timestamp of current step.
        - forecast_horizon_hrs: Look-ahead window in hours.
        - future_actual_df: The actual future dataframe (if testing noisy actual forecasts).
        """
        forecast_times = pd.date_range(start=current_time, periods=forecast_horizon_hrs, freq='h')
        forecast_df = pd.DataFrame(index=forecast_times)
        
        # Identify price columns
        price_cols = [c for c in df_history.columns if c != 'timestamp']
        
        for col in price_cols:
            if self.method == "noisy_actual" and future_actual_df is not None:
                # Extract actual future prices and add noise
                actual_subset = future_actual_df.loc[forecast_times, col].values
                forecast_df[col] = self.add_forecast_noise(actual_subset, self.mape)
                
            elif self.method == "rolling_average":
                # Average of values at the same hour of day over the last N days
                target_hours = forecast_times.hour
                forecast_vals = []
                for h in target_hours:
                    # Filter history for the same hour
                    h_mask = (df_history.index.hour == h) & (df_history.index < current_time)
                    h_values = df_history.loc[h_mask, col].tail(self.rolling_days)
                    if len(h_values) > 0:
                        forecast_vals.append(h_values.mean())
                    else:
                        # Fallback to naive if not enough history
                        last_mask = df_history.index < current_time
                        forecast_vals.append(df_history.loc[last_mask, col].iloc[-1] if last_mask.any() else 0.0)
                forecast_df[col] = forecast_vals
                
            else:  # "naive"
                # naively repeat the last observed value or the last 24h profiles
                last_mask = df_history.index < current_time
                if not last_mask.any():
                    forecast_df[col] = 0.0
                    continue
                
                # Check if we can do 24h persistence profile
                history_subset = df_history.loc[last_mask, col]
                if len(history_subset) >= 24:
                    # Extract last 24 hours profile
                    profile_24 = history_subset.tail(24).values
                    forecast_vals = [profile_24[i % 24] for i in range(forecast_horizon_hrs)]
                else:
                    # Just repeat the last single value
                    forecast_vals = [history_subset.iloc[-1]] * forecast_horizon_hrs
                forecast_df[col] = forecast_vals

        return forecast_df
