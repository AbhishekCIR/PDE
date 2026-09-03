# data_adapters/pjm_adapter.py
from data_adapters.base_adapter import BaseDataAdapter
import pandas as pd
import numpy as np

class PJMDataAdapter(BaseDataAdapter):
    """
    Standardized Data Adapter for PJM Market Data.
    Supports both:
      1. Modern Unified PJM Regulation format (RMCCP, RMPCP, Mileage / Reg_Price)
      2. Legacy dual-signal format (RMCCP_D, RMPCP_D, Mileage_RegD / RMCCP_A, RMPCP_A)
    """
    def __init__(self):
        expected_cols = [
            'LMP', 'RMCCP', 'RMPCP', 'Mileage', 'Price_SYNCH', 'Price_NONSYNCH', 'Reg_Effective_Price'
        ]
        column_mapping = {
            'LMP': ['LMP', 'lmp', 'settlementpointprice', 'energy_price', 'price', 'da_lmp', 'rt_lmp'],
            'RMCCP': ['RMCCP', 'rmccp', 'rmccp_d', 'rmccp d', 'reg_capability_price', 'capability price', 'reg capability price', 'RMCCP_A', 'rmccp_a'],
            'RMPCP': ['RMPCP', 'rmpcp', 'rmpcp_d', 'rmpcp d', 'reg_performance_price', 'performance price', 'reg performance price', 'RMPCP_A', 'rmpcp_a'],
            'Mileage': ['Mileage', 'mileage', 'mileage_regd', 'regd mileage', 'mileage d', 'mileageratio_regd', 'mileage ratio', 'Mileage_RegA', 'mileage_rega'],
            'Price_SYNCH': ['Price_SYNCH', 'price_synch', 'synch', 'synchronized reserve price', 'synch price', 'spin_price', 'srs'],
            'Price_NONSYNCH': ['Price_NONSYNCH', 'price_nonsynch', 'nonsynch', 'non-synchronized reserve price', 'non-synch price', 'nsrs'],
            'Reg_Effective_Price': ['Reg_Effective_Price', 'reg_effective_price', 'reg_price', 'Reg_Price', 'regulation_price', 'effective_reg_price']
        }
        super().__init__('PJM', expected_cols, column_mapping)

    def process(self, file_path_or_buffer):
        """Processes and standardizes PJM telemetry data, computing effective regulation price if needed."""
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
            
        # Add legacy alias columns for backwards compatibility
        df_clean['RMCCP_D'] = df_clean['RMCCP']
        df_clean['RMPCP_D'] = df_clean['RMPCP']
        df_clean['Mileage_RegD'] = df_clean['Mileage']
        
        return df_clean, logs
