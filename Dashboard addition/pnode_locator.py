import os
import pandas as pd
import logging
from pathlib import Path
from geopy.distance import geodesic
import config

logger = logging.getLogger("miso_pipeline.pnode_locator")

# Default pricing nodes database to populate if the CSV does not exist.
# This contains typical pricing nodes in MISO (Alliant Energy West, Ameren Illinois).
DEFAULT_PNODES = [
    {"pnode": "ALTW.AMESWIND", "node_id": "10001", "latitude": 42.0308, "longitude": -93.6319, "zone": "Zone 1"},
    {"pnode": "ALTW.AMES", "node_id": "10002", "latitude": 42.0340, "longitude": -93.6140, "zone": "Zone 1"},
    {"pnode": "AMIL.PARIS", "node_id": "20001", "latitude": 39.6111, "longitude": -87.6961, "zone": "Zone 3"},
    {"pnode": "AMIL.HUTSONVILLE", "node_id": "20002", "latitude": 39.1128, "longitude": -87.6586, "zone": "Zone 3"},
    {"pnode": "AMIL.NEWTON", "node_id": "20003", "latitude": 38.9839, "longitude": -88.1634, "zone": "Zone 3"},
    {"pnode": "AMIL.COFFEEN", "node_id": "20004", "latitude": 39.0837, "longitude": -89.3904, "zone": "Zone 3"},
    {"pnode": "AMIL.CLINTON", "node_id": "20005", "latitude": 40.1542, "longitude": -88.9601, "zone": "Zone 3"}
]

def init_pnode_database(db_path: Path = config.PNODE_DB_PATH):
    """
    Initializes the Pnode reference database CSV if it does not already exist.
    """
    if not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(DEFAULT_PNODES)
        df.to_csv(db_path, index=False)
        logger.info(f"Initialized default pricing node database at: {db_path}")
    else:
        logger.debug(f"Pricing node database already exists at: {db_path}")

def find_nearest_pnode(lat: float, lon: float, db_path: Path = config.PNODE_DB_PATH) -> dict:
    """
    Finds the closest pricing node in the database to the specified coordinates.
    
    Args:
        lat (float): Latitude of project coordinates.
        lon (float): Longitude of project coordinates.
        db_path (Path): Path to the pricing node CSV database.
        
    Returns:
        dict: Dict containing 'pnode', 'node_id', 'distance', and 'zone'.
    """
    init_pnode_database(db_path)
    
    try:
        df = pd.read_csv(db_path)
    except Exception as e:
        logger.error(f"Failed to read pricing node database {db_path}: {e}")
        raise
        
    if df.empty:
        raise ValueError(f"Pricing node database {db_path} is empty.")
        
    target_coord = (lat, lon)
    closest_node = None
    min_distance = float('inf')
    
    for _, row in df.iterrows():
        node_coord = (row['latitude'], row['longitude'])
        # Compute geodesic distance in miles
        dist = geodesic(target_coord, node_coord).miles
        if dist < min_distance:
            min_distance = dist
            closest_node = {
                "pnode": row['pnode'],
                "node_id": str(row['node_id']),
                "distance": round(dist, 4),
                "zone": row['zone']
            }
            
    logger.info(f"Nearest pricing node identified: {closest_node['pnode']} "
                f"({closest_node['node_id']}) at a distance of {closest_node['distance']} miles.")
    return closest_node

if __name__ == "__main__":
    # Test execution
    logging.basicConfig(level=logging.INFO)
    print("Testing Pnode search using project coordinates in config...")
    closest = find_nearest_pnode(config.LATITUDE, config.LONGITUDE)
    print("Closest node details:")
    print(closest)
