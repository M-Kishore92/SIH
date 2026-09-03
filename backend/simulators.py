import math
import random
import time
from typing import Dict, Any, List

class SimulatedGNSSDevice:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.base_lat = 13.082680
        self.base_lon = 80.270718
        self.status = "DISCONNECTED"

    def connect(self):
        self.status = "CONNECTED"
        return {"status": "CONNECTED"}

    def disconnect(self):
        self.status = "DISCONNECTED"
        return {"status": "DISCONNECTED"}

    def get_reading(self) -> Dict[str, Any]:
        # Add random walk noise to base coordinates
        self.base_lat += random.uniform(-0.00001, 0.00001)
        self.base_lon += random.uniform(-0.00001, 0.00001)
        
        return {
            "device_id": self.device_id,
            "timestamp": time.time(),
            "latitude": self.base_lat,
            "longitude": self.base_lon,
            "altitude": 14.2 + random.uniform(-0.5, 0.5),
            "accuracy": random.uniform(1.0, 3.0),
            "satellites": random.randint(12, 22),
            "fix_type": "GNSS"
        }

class SimulatedDroneDevice:
    def __init__(self, drone_id: str):
        self.drone_id = drone_id
        self.status = "IDLE"
        self.battery = 100
        self.altitude = 0

    def start_mission(self):
        self.status = "FLYING"
        self.altitude = 50.0

    def get_telemetry(self) -> Dict[str, Any]:
        if self.status == "FLYING":
            self.battery = max(0, self.battery - 0.1)
        return {
            "drone_id": self.drone_id,
            "status": self.status,
            "battery": self.battery,
            "altitude": self.altitude,
            "latitude": 13.082680 + random.uniform(-0.001, 0.001),
            "longitude": 80.270718 + random.uniform(-0.001, 0.001)
        }

class SimulatedRTKDevice:
    def __init__(self, device_id: str):
        self.device_id = device_id

    def verify_point(self, lat: float, lon: float) -> Dict[str, Any]:
        # Provide a highly accurate verification point slightly offset by error
        return {
            "device_id": self.device_id,
            "latitude": lat + random.uniform(-0.000002, 0.000002),
            "longitude": lon + random.uniform(-0.000002, 0.000002),
            "accuracy": random.uniform(0.01, 0.05),
            "fix_type": "FIXED",
            "status": "VERIFIED"
        }
