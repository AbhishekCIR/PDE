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
        current_time = pd.to_datetime(current_time)
        forecast_times = pd.date_range(start=current_time, periods=forecast_horizon_hrs, freq='h')
        forecast_df = pd.DataFrame(index=forecast_times)
        
        # Ensure df_history index is a DatetimeIndex
        if not df_history.empty and not isinstance(df_history.index, pd.DatetimeIndex):
            df_history.index = pd.to_datetime(df_history.index)

        if future_actual_df is not None and not future_actual_df.empty and not isinstance(future_actual_df.index, pd.DatetimeIndex):
            future_actual_df.index = pd.to_datetime(future_actual_df.index)
        
        # Identify price columns
        price_cols = [c for c in df_history.columns if c != 'timestamp']
        if not price_cols and future_actual_df is not None:
            price_cols = [c for c in future_actual_df.columns if c != 'timestamp']
        
        for col in price_cols:
            if self.method == "noisy_actual" and future_actual_df is not None:
                # Extract actual future prices and add noise
                actual_subset = future_actual_df.loc[forecast_times, col].values
                forecast_df[col] = self.add_forecast_noise(actual_subset, self.mape)
                
            elif self.method == "rolling_average":
                # If history is empty at initial step (t=0), fallback gracefully
                if df_history.empty or len(df_history) == 0:
                    if future_actual_df is not None and col in future_actual_df.columns:
                        vals = future_actual_df[col].iloc[:forecast_horizon_hrs].tolist()
                        if len(vals) < forecast_horizon_hrs:
                            vals += [vals[-1] if vals else 30.0] * (forecast_horizon_hrs - len(vals))
                        forecast_df[col] = vals
                    else:
                        forecast_df[col] = 30.0
                    continue

                # Fast vectorized rolling average by hour-of-day
                recent_tail = df_history.iloc[-self.rolling_days * 24:]
                if col in recent_tail.columns and len(recent_tail) > 0:
                    hour_means = recent_tail.groupby(recent_tail.index.hour)[col].mean()
                    target_hours = forecast_times.hour
                    vals = target_hours.map(hour_means)
                    if vals.isna().any():
                        fallback_val = recent_tail[col].iloc[-1]
                        vals = vals.fillna(fallback_val)
                    forecast_df[col] = vals.values
                else:
                    forecast_df[col] = 30.0
                
            else:  # "naive"
                # If history is empty at step 0, fallback to future actual or default
                if df_history.empty or len(df_history) == 0:
                    if future_actual_df is not None and col in future_actual_df.columns:
                        vals = future_actual_df[col].iloc[:forecast_horizon_hrs].tolist()
                        if len(vals) < forecast_horizon_hrs:
                            vals += [vals[-1] if vals else 30.0] * (forecast_horizon_hrs - len(vals))
                        forecast_df[col] = vals
                    else:
                        forecast_df[col] = 30.0
                    continue

                last_mask = df_history.index < current_time
                if not last_mask.any():
                    if future_actual_df is not None and col in future_actual_df.columns:
                        forecast_df[col] = future_actual_df[col].iloc[:forecast_horizon_hrs].values
                    else:
                        forecast_df[col] = 30.0
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
