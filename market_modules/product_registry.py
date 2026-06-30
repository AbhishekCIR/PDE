# product_registry.py
# Contains all ancillary service definitions, operational metadata, and market settlement rules.

PRODUCT_REGISTRY = {
    # --- ERCOT PRODUCTS ---
    "REGUP": {
        "market": "ERCOT",
        "direction": "up",
        "duration_hrs": 1.0,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Hourly bid capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 15.0,
        "clearing_interval": "RTM",
        "price_index": "REGUP",
        "degradation_multiplier": 1.0
    },
    "REGDN": {
        "market": "ERCOT",
        "direction": "down",
        "duration_hrs": 1.0,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Hourly bid capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 15.0,
        "clearing_interval": "RTM",
        "price_index": "REGDN",
        "degradation_multiplier": 1.0
    },
    "RRS": {
        "market": "ERCOT",
        "direction": "up",
        "duration_hrs": 1.0,
        "response_time_mins": 0.25,  # instantaneous (primary frequency response / UFR)
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Hourly bid capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 15.0,
        "clearing_interval": "RTM",
        "price_index": "RRS",
        "degradation_multiplier": 0.0  # minimal baseline degradation (droop reserve standby)
    },
    "NSPIN": {
        "market": "ERCOT",
        "direction": "up",
        "duration_hrs": 1.0,
        "response_time_mins": 30.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Hourly bid capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 15.0,
        "clearing_interval": "RTM",
        "price_index": "NSPIN",
        "degradation_multiplier": 0.0
    },
    "ECRS": {
        "market": "ERCOT",
        "direction": "up",
        "duration_hrs": 2.0,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Hourly bid capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 15.0,
        "clearing_interval": "RTM",
        "price_index": "ECRS",
        "degradation_multiplier": 0.0
    },

    # --- MISO PRODUCTS ---
    "REG": {
        "market": "MISO",
        "direction": "bidirectional",
        "duration_hrs": 1.0,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Bidirectional capacity commitment",
        "payment_type": "capability_and_performance",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "REG_CAP",  # performance maps to REG_MIL
        "degradation_multiplier": 1.0
    },
    "SPIN": {
        "market": "MISO",
        "direction": "up",
        "duration_hrs": 1.0,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Standby capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "SPIN",
        "degradation_multiplier": 0.0
    },
    "SUPP": {
        "market": "MISO",
        "direction": "up",
        "duration_hrs": 1.0,
        "response_time_mins": 30.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Standby capacity limits",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "SUPP",
        "degradation_multiplier": 0.0
    },

    # --- PJM PRODUCTS ---
    "RegA": {
        "market": "PJM",
        "direction": "bidirectional",
        "duration_hrs": 1.0,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 2.0,
        "bidding_rules": "Hourly offer capacity rules",
        "payment_type": "capability_and_performance",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "RMCCP_A",  # performance maps to RMPCP_A
        "degradation_multiplier": 1.2  # based on default RegA mileage
    },
    "RegD": {
        "market": "PJM",
        "direction": "bidirectional",
        "duration_hrs": 0.5,
        "response_time_mins": 5.0,
        "telemetry_frequency_secs": 2.0,
        "bidding_rules": "Hourly offer capacity rules",
        "payment_type": "capability_and_performance",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "RMCCP_D",  # performance maps to RMPCP_D
        "degradation_multiplier": 3.5  # based on default RegD mileage
    },
    "SYNCH": {
        "market": "PJM",
        "direction": "up",
        "duration_hrs": 0.5,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Tier 2 reserve offer rules",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "Price_SYNCH",
        "degradation_multiplier": 0.0
    },
    "NONSYNCH": {
        "market": "PJM",
        "direction": "up",
        "duration_hrs": 0.5,
        "response_time_mins": 10.0,
        "telemetry_frequency_secs": 4.0,
        "bidding_rules": "Standby capacity rules",
        "payment_type": "capacity_only",
        "settlement_frequency_mins": 5.0,
        "clearing_interval": "RTM",
        "price_index": "Price_NONSYNCH",
        "degradation_multiplier": 0.0
    }
}

def get_market_products(market_name):
    """Returns a dictionary containing all registered products for a given market."""
    return {k: v for k, v in PRODUCT_REGISTRY.items() if v["market"] == market_name}
