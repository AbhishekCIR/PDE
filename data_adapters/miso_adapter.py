# miso_adapter.py
from data_adapters.base_adapter import BaseDataAdapter

class MISODataAdapter(BaseDataAdapter):
    def __init__(self):
        expected_cols = ['LMP', 'REG_CAP', 'REG_MIL', 'SPIN', 'SUPP']
        column_mapping = {
            'LMP': ['LMP', 'lmp', 'settlementpointprice', 'settlement point price', 'price'],
            'REG_CAP': ['REG_CAP', 'reg_cap', 'regcap', 'regulation_capacity', 'reg capacity'],
            'REG_MIL': ['REG_MIL', 'reg_mil', 'regmil', 'regulation_mileage', 'reg mileage'],
            'SPIN': ['SPIN', 'spin', 'spinning reserve', 'spinning'],
            'SUPP': ['SUPP', 'supp', 'supplemental reserve', 'supplemental']
        }
        super().__init__('MISO', expected_cols, column_mapping)
