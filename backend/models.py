from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Float, DateTime
from sqlalchemy.orm import relationship
import datetime
from database import Base

class Parcel(Base):
    __tablename__ = "parcels"
    id = Column(String, primary_key=True, index=True) # e.g., "DEMO-001"
    survey_number = Column(String, index=True)
    village = Column(String, index=True)
    district = Column(String)
    recorded_area = Column(Float) # in acres
    surveyed_area = Column(Float, nullable=True) # in acres
    owner_reference = Column(String)
    status = Column(String, default="pending") # pending, under_survey, verified, disputed
    risk_level = Column(String, default="GREEN") # GREEN, YELLOW, RED
    boundary_geojson = Column(String) # Storing as GeoJSON string for simplicity
    
    surveys = relationship("Survey", back_populates="parcel")
    land_record = relationship("LandRecord", back_populates="parcel", uselist=False)

class Survey(Base):
    __tablename__ = "surveys"
    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(String, ForeignKey("parcels.id"))
    surveyor = Column(String)
    survey_date = Column(DateTime, default=datetime.datetime.utcnow)
    survey_method = Column(String) # GNSS, Drone
    surveyed_polygon_geojson = Column(String, nullable=True)
    area = Column(Float, nullable=True)
    perimeter = Column(Float, nullable=True)
    status = Column(String, default="completed")
    
    parcel = relationship("Parcel", back_populates="surveys")
    anomalies = relationship("BoundaryAnomaly", back_populates="survey")
    verification = relationship("VerificationRecord", back_populates="survey", uselist=False)

class BoundaryAnomaly(Base):
    __tablename__ = "boundary_anomalies"
    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"))
    anomaly_type = Column(String) # Area Mismatch, Boundary Displacement, Encroachment
    description = Column(String)
    severity = Column(String) # YELLOW, RED
    
    survey = relationship("Survey", back_populates="anomalies")

class VerificationRecord(Base):
    __tablename__ = "verification_records"
    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"))
    method = Column(String) # RTK, Manual
    status = Column(String) # verified, disputed
    notes = Column(String)
    verification_date = Column(DateTime, default=datetime.datetime.utcnow)
    
    survey = relationship("Survey", back_populates="verification")

class LandRecord(Base):
    __tablename__ = "land_records"
    id = Column(Integer, primary_key=True, index=True)
    parcel_id = Column(String, ForeignKey("parcels.id"))
    last_updated = Column(DateTime, default=datetime.datetime.utcnow)
    timeline_events = Column(String) # JSON string of timeline events
    
    parcel = relationship("Parcel", back_populates="land_record")
