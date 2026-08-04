# Coach iOS

## Generate Xcode project

```sh
brew install xcodegen
cd ios
xcodegen generate
open Coach.xcodeproj
```

## Required setup before first build

1. Open the project, select the **Coach** target → **Signing & Capabilities** → set your **Team**.
2. The HealthKit and Background Delivery capabilities are already declared in `App/App.entitlements`. Verify they appear in the Capabilities pane and that your Apple Developer App ID `com.trainwithcoach.app` has HealthKit enabled.
3. For the API base URL during dev, either:
   - Edit `APIClient.swift`'s default `baseURL`, OR
   - Set the UserDefaults key `coach.api_base_url` at launch (e.g. via a launch argument: `-coach.api_base_url http://<EC2-IP>`)
4. Google Sign-In is **not yet wired in the UI** (Apple Sign-In is). To add it, drop `GoogleService-Info.plist` into `App/` and add the GoogleSignIn SPM dependency.

## What works in this scaffold

- Sign In with Apple → backend `/v1/auth/apple` → JWT stored in Keychain
- HealthKit permission flow + anchored delta queries (workouts, sleep stages, HRV-SDNN, resting HR, VO2 max, body mass) → batched POST to `/v1/ingest/healthkit`
- HKObserverQuery with background delivery and proper completion-handler discipline
- Oura OAuth connect via ASWebAuthenticationSession → backend handles the callback → returns to app via `coach://oauth/callback`
- Dashboard pulls `/v1/dashboard/today`
- Chat streams from `/v1/chat` over SSE
- Goal-setting in Profile

## Not yet implemented (post-MVP)

- Strava / Whoop / Garmin direct providers
- Push notifications when nightly brief lands
- Charts/trends view
- Apple Watch companion
