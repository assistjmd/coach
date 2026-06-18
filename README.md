# coach

Multi-tracker fitness aggregator with an AI coach. Pulls workouts, sleep, HRV, and recovery data from Apple HealthKit, Oura (and later Strava, Whoop, Garmin, TrainingPeaks, Wahoo Elemnt) into a unified model. Claude generates daily readiness briefs, training prescriptions, and answers questions over the user's own data.

## Status

Milestone 1 scaffold — not yet deployable. See `/Users/gurthang/.claude/plans/hi-there-i-am-calm-lighthouse.md` for the full plan.

## Layout

```
ios/        SwiftUI iOS app (xcodegen)
backend/    FastAPI + Postgres + Claude
docs/       Architecture notes
```

## Quickstart (backend, local dev)

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in secrets
alembic upgrade head
uvicorn app.main:app --reload
```

## Quickstart (iOS)

```sh
cd ios
brew install xcodegen
xcodegen generate
open Coach.xcodeproj
```

You will need to add a `GoogleService-Info.plist` (for Google Sign-In) and configure signing under your Apple Team. The HealthKit + background-delivery entitlements are declared in `ios/App/App.entitlements`.

## Adding a new provider

1. Create `backend/app/providers/<slug>.py` implementing the `Provider` protocol from `providers/base.py`.
2. Register it in `backend/app/providers/registry.py`.
3. Add OAuth credentials to env: `<SLUG>_CLIENT_ID`, `<SLUG>_CLIENT_SECRET`.
4. iOS Connections screen auto-renders new tiles from the `providers` list in `GET /v1/me`.

No core changes required.
