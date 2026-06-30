# ercot_adapter.py
from data_adapters.base_adapter import BaseDataAdapter

class ERCOTDataAdapter(BaseDataAdapter):
    def __init__(self):
        expected_cols = ['LMP', 'REGUP', 'REGDN', 'RRS', 'NSPIN', 'ECRS']
        column_mapping = {
            'LMP': ['LMP', 'lmp', 'settlementpointprice', 'settlement point price', 'price'],
            'REGUP': ['REGUP', 'regup', 'reg_up', 'reg-up'],
            'REGDN': ['REGDN', 'regdn', 'reg_down', 'reg-down'],
            'RRS': ['RRS', 'rrs', 'responsive reserve', 'responsive'],
            'NSPIN': ['NSPIN', 'nspin', 'nonspin', 'non-spin', 'nonspinning'],
            'ECRS': ['ECRS', 'ecrs', 'contingency reserve', 'ecr_service']
        }
        super().__init__('ERCOT', expected_cols, column_mapping)
