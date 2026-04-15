# Operations, Troubleshooting, and Lessons

## Local Launch Behavior

The app is launched with [launch_app.bat](</C:/Users/91956/Desktop/assignment final/launch_app.bat>).

That script does the following:

1. changes to the project root
2. creates the virtual environment if missing
3. installs Python dependencies
4. installs Playwright Chromium
5. installs frontend dependencies
6. frees ports `8765` and `5173`
7. starts backend and frontend in separate terminal windows
8. waits for the frontend to respond
9. opens the browser

This script exists because local startup turned out to be one of the easiest places for friction to creep in.

## Environment and Keys

Supported key sources:

- environment variable `ANTHROPIC_API_KEY`
- environment variable `OPENAI_API_KEY`
- local file `ANT API KEY.txt`
- local file `OAI API KEY.txt`

The code reads these through [backend/config.py](</C:/Users/91956/Desktop/assignment final/backend/config.py>).

## Known Development Issues We Faced

### 1. Frontend did not open correctly

Observed problem:

- browser did not reliably open
- app sometimes launched twice

What we changed:

- waited for the frontend URL to respond before opening it
- reclaimed dev ports before launching
- used a single launcher rather than multiple scripts

### 2. Refresh felt like cache corruption

Observed problem:

- after relaunch or refresh, old inputs and state still appeared

What we changed:

- intentionally restore the last job from local storage
- add `Reset Session` so the operator can clear state instantly

Lesson:

- persistence is useful, but it must be explicit and reversible or it feels like broken caching

### 3. Preview layout felt broken

Observed problem:

- nested scrolling
- split visual stages
- off-center rendering

What we changed:

- simplified to one main display stage
- used a fitted viewport container
- removed unnecessary preview clutter

### 4. Source and output controls created confusion

Observed problem:

- exposing intermediate HTML made the app feel noisier than necessary

What we changed:

- kept reconstruction internal
- removed source HTML download from the UI
- kept only the final output HTML as the exposed deliverable

### 5. Asset search produced wrong images

Observed problem:

- search could latch onto ad copy or campaign terms instead of the actual product or brand

What we changed:

- prioritized brand name, product name, and product category over promotional copy
- filtered risky domains
- used official brand domains when possible
- added a fallback to generic product imagery when brand-specific imagery is unavailable

Lesson:

- image search should anchor on brand and product identity, not the marketing headline

### 6. External images were brittle

Observed problem:

- official sites sometimes blocked image fetches with `403`
- some asset URLs later disappeared with `404`
- meta-share images and logos were often technically valid but visually wrong

What we changed:

- made searched assets optional
- kept placeholder mode available
- added stricter scoring and validation for image candidates

Lesson:

- searched assets improve realism sometimes, but placeholder visuals remain the safest fallback

### 7. Incomplete-looking pages

Observed problem:

- some outputs looked strong at the top but unfinished overall

What we changed:

- made matching footer generation mandatory
- added a footer presence guardrail

Lesson:

- completeness is part of perceived quality, not just a cosmetic extra

## Practical Troubleshooting

### If the backend is not reachable

Check:

- backend terminal window is open
- port `8765` is free
- required dependencies are installed
- API key files exist or env vars are set

### If the frontend is not reachable

Check:

- frontend terminal window is open
- port `5173` is free
- `npm install` completed successfully

### If brand extraction fails

Possible causes:

- non-image upload
- corrupted file
- Anthropic transient failure
- invalid or missing API key

### If conversion fails

Possible causes:

- no reconstruction exists yet
- invalid brand JSON was provided
- model overload or transient provider issue
- generated HTML failed guardrails even after repair

### If searched assets are poor

Try:

- placeholder assets mode
- a clearer ad creative
- another run after product fallback improvements

## What the Team Learned

The biggest project-level lessons were:

- output quality matters more than clever orchestration
- chat-quality HTML generation worked better than JSON patch editing
- a clean operator UI matters as much as model quality
- local startup reliability needs first-class attention
- aggressive guardrails improve trust
- optional fallback modes are better than pretending every automated lookup will work

## Recommended Future Improvements

If the project continues, the most useful next steps would be:

- add a lightweight docs page inside the app
- persist convert options per session
- add artifact inspection for internal debugging only
- add a second optional asset candidate rather than only one approved image
- log richer asset-search reasoning into `asset-manifest.json`
- add an explicit export bundle mode if CSS or metadata packaging is ever needed
