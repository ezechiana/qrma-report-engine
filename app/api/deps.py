#app/api/deps.py

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import User


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# MVP placeholder
def get_current_user(db: Session = Depends(get_db)):
    from app.db.models import User

    user = db.query(User).first()

    if not user:
        # Auto-create a default user for MVP
        user = User(
            email="test@local",
            password_hash="dev",
            full_name="Test User",
            clinic_name="Test Clinic",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user