# Audit of the existing code

## Congestion index (congestion.py)
- Main function, parameters and returned value:
- Scale and thresholds of the levels:
- YOLO weights, confidence threshold and vehicle classes used:
- How the peak density is defined:
- Is the video analyzed at each page load or cached?

## Zones (locations.py)
- Zone keys (exact names):
- Video associated with each zone:
- GPS coordinates:

## Database
- Engine used (SQLite file or PostgreSQL):
- Tables used by db_utils.py:
- Are traffic statistics timestamped?
- Role of db_utils_postgres.py and schema_postgres.sql (used or unused):

## Routing (routing.py)
- Function comparing the direct and the alternative routes:
- Where the congestion index is read:

## API
- Is there a FastAPI backend? (yes or no):
- Decision for the endpoints /accidents and /congestion/predict: