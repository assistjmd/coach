"""One-off: collapse duplicate workouts for every account."""
from app.analytics.dedupe import collapse_existing
from app.analytics.load import daily_loads
from app.db import SessionLocal
from app.models import User, Workout

with SessionLocal() as db:
    users = db.query(User).all()
    print(f"{len(users)} accounts")
    total = 0
    for u in users:
        before = db.query(Workout).filter(Workout.user_id == u.id).count()
        removed = collapse_existing(db, u.id)
        after = db.query(Workout).filter(Workout.user_id == u.id).count()
        total += removed
        if removed:
            print(f"  {str(u.id)[:8]}: {before} -> {after} workouts ({removed} merged)")
    print(f"removed {total} duplicate workouts")
