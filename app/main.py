#app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.routes_patients import router as patients_router
from app.api.routes_cases import router as cases_router
from app.api.routes_reports import router as reports_router
from app.api.routes_share import router as share_router


app = FastAPI(title="QRMA SaaS MVP")

from app.db.base import Base
from app.db.session import engine

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(patients_router)
app.include_router(cases_router)
app.include_router(reports_router)
app.include_router(share_router)

@app.get("/")
def root():
    return {"status": "QRMA engine running"}