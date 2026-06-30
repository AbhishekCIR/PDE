# base_forecast.py
import pandas as pd
import numpy as np

class BaseForecastEngine:
    def __init__(self, market_name):
        self.market_name = market_name

    def generate_forecast(self, df_history, current_time, forecast_horizon_hrs):
        """
        Generates price forecasts for the given time horizon.
        
        Parameters:
        - df_history (pd.DataFrame): Historical data including columns like LMP, REGUP, etc.
        - current_time (pd.Timestamp): The current simulation time step.
        - forecast_horizon_hrs (int): Length of look-ahead forecast window in hours.
        
        Returns:
        - dict: A dictionary of forecasted price vectors (LMP, and relevant AS prices).
        """
        raise NotImplementedError("Subclasses must implement this method")

    def add_forecast_noise(self, actual_prices, mape):
        """
        Adds Gaussian noise to the actual price series to simulate forecast error.
        
        Parameters:
        - actual_prices (np.ndarray): The actual price array.
        - mape (float): Target Mean Absolute Percentage Error (e.g. 0.1 for 10% MAPE).
        
        Returns:
        - np.ndarray: Noise-laden forecast prices.
        """
        if mape <= 0:
            return actual_prices.copy()
        
        # Calculate standard deviation to achieve target MAPE
        # Mean Absolute Percentage Error = E[|noise| / price]
        # For normal noise with mean 0 and std dev sigma: E[|noise|] = sigma * sqrt(2/pi)
        # So MAPE = (sigma * sqrt(2/pi)) / mean(price)
        # Therefore: sigma = mape * mean(price) / sqrt(2/pi)
        mean_val = np.mean(np.abs(actual_prices))
        if mean_val == 0:
            mean_val = 1.0
        
        sigma = (mape * mean_val) / np.sqrt(2.0 / np.pi)
        noise = np.random.normal(0, sigma, len(actual_prices))
        
        forecast = actual_prices + noise
        # Ensure prices don't drop to unreasonable negatives unless raw prices were negative
        min_allowed = min(0.0, np.min(actual_prices))
        return np.clip(forecast, a_min=min_allowed, a_max=None)
