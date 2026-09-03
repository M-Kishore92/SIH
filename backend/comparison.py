from shapely.geometry import shape
import json

def compare_boundaries(recorded_geojson_str: str, surveyed_geojson_str: str):
    try:
        recorded_geom = shape(json.loads(recorded_geojson_str)['geometry'])
        surveyed_geom = shape(json.loads(surveyed_geojson_str)['geometry'])
    except Exception as e:
        return {"error": str(e)}

    # In a real system, you would project these to a local CRS (like UTM) to get meters/acres.
    # For this simple prototype, we just calculate area on the raw coords and multiply by a dummy factor
    # to simulate acres.
    
    # 1 degree squared at equator is roughly 12365000000 square meters = ~3,055,448 acres
    # We will use a dummy scale factor to produce realistic looking numbers.
    scale_factor = 3055448
    
    existing_area = recorded_geom.area * scale_factor
    surveyed_area = surveyed_geom.area * scale_factor
    
    area_difference = abs(existing_area - surveyed_area)
    area_difference_percentage = (area_difference / existing_area) * 100 if existing_area > 0 else 0
    
    intersection = recorded_geom.intersection(surveyed_geom)
    overlap_area = intersection.area * scale_factor
    
    # Simple risk level logic based on arbitrary prototype thresholds
    risk_level = "GREEN"
    if area_difference_percentage > 5:
        risk_level = "RED"
    elif area_difference_percentage > 2:
        risk_level = "YELLOW"
        
    anomaly_detected = risk_level != "GREEN"
    anomaly_type = "None"
    
    if anomaly_detected:
        if area_difference_percentage > 5:
            anomaly_type = "Significant Area Mismatch"
        else:
            anomaly_type = "Minor Boundary Shift"

    return {
        "existing_area": round(existing_area, 2),
        "surveyed_area": round(surveyed_area, 2),
        "area_difference": round(area_difference, 2),
        "area_difference_percentage": round(area_difference_percentage, 2),
        "overlap_area": round(overlap_area, 2),
        "anomaly_detected": anomaly_detected,
        "anomaly_type": anomaly_type,
        "risk_level": risk_level
    }
