# data_adapters/pjm_adapter.py
from data_adapters.base_adapter import BaseDataAdapter
import pandas as pd
import numpy as np

class PJMDataAdapter(BaseDataAdapter):
    """
    Standardized Data Adapter for Unified PJM Market Data.
    Requires strictly unified PJM columns: LMP, RMCCP, RMPCP, Mileage, Price_SYNCH, Price_NONSYNCH
    """
    def __init__(self):
        expected_cols = [
            'LMP', 'RMCCP', 'RMPCP', 'Mileage', 'Price_SYNCH', 'Price_NONSYNCH', 'Reg_Effective_Price'
        ]
        column_mapping = {
            'LMP': ['LMP', 'lmp', 'settlementpointprice', 'energy_price', 'price', 'da_lmp', 'rt_lmp'],
            'RMCCP': ['RMCCP', 'rmccp', 'reg_capability_price', 'capability price', 'reg capability price', 'capability_price', 'RMCCP_D', 'rmccp_d'],
            'RMPCP': ['RMPCP', 'rmpcp', 'reg_performance_price', 'performance price', 'reg performance price', 'performance_price', 'RMPCP_D', 'rmpcp_d'],
            'Mileage': ['Mileage', 'mileage', 'mileageratio', 'mileage ratio', 'reg_mileage', 'Mileage_RegD', 'mileage_regd'],
            'Price_SYNCH': ['Price_SYNCH', 'price_synch', 'synch', 'synchronized reserve price', 'synch price', 'spin_price', 'srs'],
            'Price_NONSYNCH': ['Price_NONSYNCH', 'price_nonsynch', 'nonsynch', 'non-synchronized reserve price', 'non-synch price', 'nsrs'],
            'Reg_Effective_Price': ['Reg_Effective_Price', 'reg_effective_price', 'reg_price', 'Reg_Price', 'regulation_price', 'effective_reg_price']
        }
        super().__init__('PJM', expected_cols, column_mapping)

    def process(self, file_path_or_buffer):
        """Processes and standardizes unified PJM telemetry data."""
        df_clean, logs = super().process(file_path_or_buffer)
        
        # Fill missing values with reasonable defaults
        if 'Mileage' not in df_clean.columns or df_clean['Mileage'].isna().all():
            df_clean['Mileage'] = 3.2
            logs.append("Defaulted storage Mileage Ratio to 3.2 (fast dynamic regulation response)")
            
        if 'RMPCP' not in df_clean.columns or df_clean['RMPCP'].isna().all():
            df_clean['RMPCP'] = 2.5
            logs.append("Defaulted RMPCP (performance price) to $2.50/mileage-MW")
            
        if 'RMCCP' not in df_clean.columns or df_clean['RMCCP'].isna().all():
            if 'Reg_Effective_Price' in df_clean.columns:
                df_clean['RMCCP'] = df_clean['Reg_Effective_Price'] * 0.70
            else:
                df_clean['RMCCP'] = 25.0
            logs.append("Initialized RMCCP (capability price)")

        if 'Price_SYNCH' not in df_clean.columns or df_clean['Price_SYNCH'].isna().all():
            df_clean['Price_SYNCH'] = 4.0
            logs.append("Defaulted Synchronized Reserve price to $4.00/MW")
            
        if 'Price_NONSYNCH' not in df_clean.columns or df_clean['Price_NONSYNCH'].isna().all():
            df_clean['Price_NONSYNCH'] = 2.0
            logs.append("Defaulted Non-Synchronized Reserve price to $2.00/MW")

        # Calculate Unified Effective Regulation Price
        perf_score = 0.95
        if 'Reg_Effective_Price' not in df_clean.columns or df_clean['Reg_Effective_Price'].isna().all():
            df_clean['Reg_Effective_Price'] = (df_clean['RMCCP'] * perf_score) + (df_clean['RMPCP'] * df_clean['Mileage'] * perf_score)
            logs.append("Computed unified 'Reg_Effective_Price' = (RMCCP * 0.95) + (RMPCP * Mileage * 0.95)")
            
        return df_clean, logs
