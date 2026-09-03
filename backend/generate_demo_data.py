import json
from sqlalchemy.orm import Session
import models, database

def create_demo_data(db: Session):
    # Ensure tables exist
    models.Base.metadata.create_all(bind=database.engine)
    
    # Check if data exists
    if db.query(models.Parcel).count() > 0:
        return
        
    print("Generating demo data...")
    
    # Base coordinate somewhere in Chennai roughly
    base_lat = 13.0827
    base_lon = 80.2707

    def make_square(lat, lon, size=0.001):
        # returns simple geojson polygon
        coords = [
            [lon, lat],
            [lon + size, lat],
            [lon + size, lat + size],
            [lon, lat + size],
            [lon, lat]
        ]
        return json.dumps({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords]
            }
        })

    demo_parcels = [
        # Demo parcel with significant mismatch explicitly defined later
        models.Parcel(
            id="DEMO-001", survey_number="101/1", village="Madipakkam", district="Chennai",
            recorded_area=2.10, owner_reference="R. Sharma", status="pending",
            boundary_geojson=make_square(base_lat, base_lon, 0.0015)
        ),
        models.Parcel(
            id="DEMO-002", survey_number="101/2", village="Madipakkam", district="Chennai",
            recorded_area=1.50, owner_reference="S. Kumar", status="verified", risk_level="GREEN",
            boundary_geojson=make_square(base_lat, base_lon + 0.002)
        ),
        models.Parcel(
            id="DEMO-003", survey_number="102/1", village="Madipakkam", district="Chennai",
            recorded_area=0.75, owner_reference="K. Natarajan", status="under_survey",
            boundary_geojson=make_square(base_lat + 0.002, base_lon)
        ),
        # Add more parcels to reach 10
    ]
    
    for i in range(4, 11):
        demo_parcels.append(
            models.Parcel(
                id=f"DEMO-{i:03d}", survey_number=f"105/{i}", village="Madipakkam", district="Chennai",
                recorded_area=1.0 + (i * 0.1), owner_reference=f"Owner {i}", status="pending",
                boundary_geojson=make_square(base_lat + (i*0.001), base_lon - (i*0.001))
            )
        )

    for p in demo_parcels:
        db.add(p)
    db.commit()
    print("Demo data generated successfully.")

if __name__ == "__main__":
    db = database.SessionLocal()
    create_demo_data(db)
    db.close()
