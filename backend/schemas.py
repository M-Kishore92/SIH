from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import json

class ParcelBase(BaseModel):
    id: str
    survey_number: str
    village: str
    district: str
    recorded_area: float
    owner_reference: str
    status: str
    risk_level: str
    boundary_geojson: str

class ParcelCreate(ParcelBase):
    pass

class Parcel(ParcelBase):
    surveyed_area: Optional[float] = None
    
    class Config:
        from_attributes = True

class SurveyBase(BaseModel):
    parcel_id: str
    surveyor: str
    survey_method: str

class SurveyCreate(SurveyBase):
    pass

class Survey(SurveyBase):
    id: int
    survey_date: datetime
    surveyed_polygon_geojson: Optional[str] = None
    area: Optional[float] = None
    perimeter: Optional[float] = None
    status: str

    class Config:
        from_attributes = True

class GNSSPoint(BaseModel):
    latitude: float
    longitude: float
    altitude: float
    accuracy: float
    satellites: int
    fix_type: str
    timestamp: str


class CompareRequest(BaseModel):
    surveyed_geojson: str


class CompareResponse(BaseModel):
    existing_area: float
    surveyed_area: float
    area_difference: float
    area_difference_percentage: float
    overlap_area: float
    anomaly_detected: bool
    anomaly_type: str
    risk_level: str
