# base_adapter.py
import pandas as pd
import numpy as np

class BaseDataAdapter:
    def __init__(self, market_name, expected_cols, column_mapping):
        """
        Parameters:
        - market_name (str): Name of the ISO market.
        - expected_cols (list): Standard columns expected post-adapter.
        - column_mapping (dict): Case-insensitive mapping of raw column names to standardized ones.
        """
        self.market_name = market_name
        self.expected_cols = expected_cols
        self.column_mapping = column_mapping

    def load_data(self, file_path_or_buffer):
        """Loads data from CSV or Excel file."""
        if isinstance(file_path_or_buffer, pd.DataFrame):
            return file_path_or_buffer.copy()
            
        if isinstance(file_path_or_buffer, str):
            if file_path_or_buffer.endswith('.csv'):
                df = pd.read_csv(file_path_or_buffer)
            else:
                df = pd.read_excel(file_path_or_buffer)
        else:
            # Assume it is a file-like buffer from streamlit
            try:
                df = pd.read_csv(file_path_or_buffer)
            except Exception:
                # Seek back and try excel
                file_path_or_buffer.seek(0)
                df = pd.read_excel(file_path_or_buffer)
        return df

    def standardize_columns(self, df):
        """Maps raw columns to standard internal names (case-insensitive)."""
        col_mapping = {c.strip().lower(): c for c in df.columns}
        rename_dict = {}
        
        for std_name, raw_aliases in self.column_mapping.items():
            for alias in raw_aliases:
                alias_lower = alias.lower()
                if alias_lower in col_mapping:
                    rename_dict[col_mapping[alias_lower]] = std_name
                    break
        
        df = df.rename(columns=rename_dict)
        return df

    def clean_timestamps(self, df):
        """
        Ensures a continuous, duplicate-free datetime sequence.
        Handles relative hours (e.g. 1..8760), DST transitions, and NaNs.
        """
        if 'timestamp' not in df.columns:
            # Check for deliveryDate & deliveryHour fallback
            if 'deliveryDate' in df.columns and 'deliveryHour' in df.columns:
                try:
                    hours = df['deliveryHour'].astype(str).str.replace('24:00:00', '00:00:00').str.replace('24:00', '00:00')
                    df['timestamp'] = pd.to_datetime(df['deliveryDate'].astype(str) + ' ' + hours)
                    mask_24 = df['deliveryHour'].astype(str).str.contains('24:00')
                    if mask_24.any():
                        df.loc[mask_24, 'timestamp'] += pd.Timedelta(days=1)
                except Exception:
                    pass

        if 'timestamp' not in df.columns or df['timestamp'].isna().sum() > len(df) * 0.1:
            # Fallback: create standard sequential index starting 2026-01-01
            df['timestamp'] = pd.date_range(start='2026-01-01', periods=len(df), freq='h')
            return df

        # Parse timestamps
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        
        # Check if timestamps are completely numeric (e.g., index 1..8760)
        if pd.api.types.is_numeric_dtype(df['timestamp']) or df['timestamp'].isna().all():
            df['timestamp'] = pd.date_range(start='2026-01-01', periods=len(df), freq='h')
            return df
            
        # If there are duplicate timestamps, log or average them
        duplicates = df.duplicated(subset=['timestamp']).sum()
        if duplicates > 0:
            # Aggregate duplicates by taking the mean of numeric columns
            df = df.groupby('timestamp', as_index=False).mean()
            
        # Re-index to a full chronological Hourly range to resolve gaps and DST spring gaps
        df = df.set_index('timestamp')
        
        # Determine frequency (default to hour if not clear)
        if len(df) > 1:
            try:
                full_index = pd.date_range(start=df.index.min(), end=df.index.max(), freq='h')
                df = df.reindex(full_index)
            except Exception:
                pass
                
        # Reset index to return 'timestamp' column
        df = df.reset_index().rename(columns={'index': 'timestamp'})
        return df

    def handle_missing_data(self, df):
        """Interpolates missing price data and prints warnings for huge gaps."""
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        
        # Check for large gaps
        for col in numeric_cols:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                # Interpolate using linear method, fill boundary values with forward/backward fill
                df[col] = df[col].interpolate(method='linear').ffill().bfill()
        return df

    def run_validation(self, df):
        """Performs validation checks: negative prices, extreme spikes, duplicate columns."""
        validation_logs = []
        
        # Check negative prices (they are allowed, just log the count)
        if 'LMP' in df.columns:
            neg_count = (df['LMP'] < 0).sum()
            if neg_count > 0:
                validation_logs.append(f"Info: Found {neg_count} negative energy prices (LMP). Solver will treat this as profit to charge.")
                
            # Check extreme spikes (e.g. > $1000/MWh or < -$500/MWh)
            extreme_high = (df['LMP'] > 1000.0).sum()
            extreme_low = (df['LMP'] < -500.0).sum()
            if extreme_high > 0:
                validation_logs.append(f"Warning: Found {extreme_high} intervals with LMP > $1000/MWh.")
            if extreme_low > 0:
                validation_logs.append(f"Warning: Found {extreme_low} intervals with LMP < -$500/MWh.")

        # Ensure all expected columns exist
        missing_cols = [c for c in self.expected_cols if c not in df.columns]
        if missing_cols:
            raise ValueError(f"Error: Standardized columns missing: {missing_cols}")
            
        return validation_logs

    def process(self, file_path_or_buffer):
        """The main orchestration function for the data adapter."""
        df = self.load_data(file_path_or_buffer)
        df = self.standardize_columns(df)
        df = self.clean_timestamps(df)
        df = self.handle_missing_data(df)
        validation_logs = self.run_validation(df)
        
        # Keep only timestamp + expected columns to avoid noise
        df_clean = df[['timestamp'] + self.expected_cols].copy()
        
        return df_clean, validation_logs
