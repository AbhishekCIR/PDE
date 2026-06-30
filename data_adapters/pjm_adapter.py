# pjm_adapter.py
from data_adapters.base_adapter import BaseDataAdapter

class PJMDataAdapter(BaseDataAdapter):
    def __init__(self):
        expected_cols = [
            'LMP', 'RMCCP_A', 'RMPCP_A', 'RMCCP_D', 'RMPCP_D',
            'Mileage_RegA', 'Mileage_RegD', 'Price_SYNCH', 'Price_NONSYNCH'
        ]
        column_mapping = {
            'LMP': ['LMP', 'lmp', 'settlementpointprice', 'price'],
            'RMCCP_A': ['RMCCP_A', 'rmccp_a', 'rmccp a', 'regA capability price', 'capability price rega'],
            'RMPCP_A': ['RMPCP_A', 'rmpcp_a', 'rmpcp a', 'regA performance price', 'performance price rega'],
            'RMCCP_D': ['RMCCP_D', 'rmccp_d', 'rmccp d', 'regD capability price', 'capability price regd'],
            'RMPCP_D': ['RMPCP_D', 'rmpcp_d', 'rmpcp d', 'regD performance price', 'performance price regd'],
            'Mileage_RegA': ['Mileage_RegA', 'mileage_rega', 'rega mileage', 'mileage a', 'mileageratio_rega'],
            'Mileage_RegD': ['Mileage_RegD', 'mileage_regd', 'regd mileage', 'mileage d', 'mileageratio_regd'],
            'Price_SYNCH': ['Price_SYNCH', 'price_synch', 'synch', 'synchronized reserve price', 'synch price'],
            'Price_NONSYNCH': ['Price_NONSYNCH', 'price_nonsynch', 'nonsynch', 'non-synchronized reserve price', 'non-synch price']
        }
        super().__init__('PJM', expected_cols, column_mapping)
