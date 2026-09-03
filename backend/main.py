from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import models, schemas, database, comparison

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Smart Land Survey API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For prototype/hackathon
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Welcome to Smart Land Survey API"}

@app.get("/api/parcels", response_model=list[schemas.Parcel])
def get_parcels(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    parcels = db.query(models.Parcel).offset(skip).limit(limit).all()
    return parcels

@app.get("/api/parcels/{parcel_id}", response_model=schemas.Parcel)
def get_parcel(parcel_id: str, db: Session = Depends(database.get_db)):
    parcel = db.query(models.Parcel).filter(models.Parcel.id == parcel_id).first()
    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found")
    return parcel

@app.post("/api/surveys", response_model=schemas.Survey)
def create_survey(survey: schemas.SurveyCreate, db: Session = Depends(database.get_db)):
    db_survey = models.Survey(**survey.model_dump())
    db.add(db_survey)
    db.commit()
    db.refresh(db_survey)
    
    # Update parcel status
    parcel = db.query(models.Parcel).filter(models.Parcel.id == survey.parcel_id).first()
    if parcel:
        parcel.status = "under_survey"
        db.commit()
        
    return db_survey

# Simulator endpoint placeholder
@app.post("/api/gnss/point")
def receive_gnss_point(point: schemas.GNSSPoint):
    # In a real app this would broadcast via WebSocket or save to DB
    return {"status": "received", "point": point.model_dump()}


@app.post("/api/parcels/{parcel_id}/compare", response_model=schemas.CompareResponse)
def compare_parcel_boundaries(
    parcel_id: str,
    req: schemas.CompareRequest,
    db: Session = Depends(database.get_db)
):
    """Run Shapely comparison between recorded and newly surveyed boundaries."""
    parcel = db.query(models.Parcel).filter(models.Parcel.id == parcel_id).first()
    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found")

    result = comparison.compare_boundaries(
        parcel.boundary_geojson,
        req.surveyed_geojson
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Persist results back to the parcel
    parcel.risk_level = result["risk_level"]
    parcel.surveyed_area = result["surveyed_area"]
    if result["anomaly_detected"]:
        parcel.status = "disputed"
    else:
        parcel.status = "verified"
    db.commit()

    return result


@app.patch("/api/parcels/{parcel_id}/verify", response_model=schemas.Parcel)
def rtk_verify_parcel(
    parcel_id: str,
    db: Session = Depends(database.get_db)
):
    """RTK precision override — marks parcel as verified GREEN."""
    parcel = db.query(models.Parcel).filter(models.Parcel.id == parcel_id).first()
    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found")

    parcel.status = "verified"
    parcel.risk_level = "GREEN"
    db.commit()
    db.refresh(parcel)
    return parcel
