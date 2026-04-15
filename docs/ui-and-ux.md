# UI and UX Notes

## Current UX Direction

The current interface is designed as a production-style operator tool rather than a demo dashboard.

Key principles:

- black-first visual system
- thin, controlled borders
- left-side controls
- output-focused display area
- minimal debug clutter
- no exposed model labels
- no exposed intermediate HTML download

The main UI lives in [frontend/src/App.tsx](</C:/Users/91956/Desktop/assignment final/frontend/src/App.tsx>) and [frontend/src/styles.css](</C:/Users/91956/Desktop/assignment final/frontend/src/styles.css>).

## Layout

The page has two primary zones:

### Left Sidebar

Used for operator actions only:

- URL input
- viewport preset selector
- `Process URL`
- drag-and-drop ad upload
- `Process Ad`
- `Convert`
- `Reset Session`
- `Download Output HTML` once conversion is complete

### Main Display Area

Used for preview only:

- empty state before a job exists
- source screenshot before conversion
- final converted HTML after conversion

This keeps the right side focused on visual judgment rather than pipeline mechanics.

## Visual System

The current UI uses:

- `Outfit` for body copy
- `Sora` for headings and controls
- deep black and near-black surfaces
- purple accent system
- restrained glow rather than bright ornamental effects

The design intentionally avoids:

- pale cards
- soft rounded bubble dashboards
- visible phase labels
- backend debug panels
- token or model metadata in the operator flow

## Core UI Behaviors

### Backend Status

The top bar shows a minimal live tick instead of verbose backend text.

### Drag and Drop Upload

The ad upload control is a drag-and-drop area rather than a plain HTML file button.

### Session Restore

The frontend stores the last job id in local storage:

- key: `design-transfer-last-job-id`

On reload or relaunch, it attempts to restore the job through `GET /api/jobs/{job_id}`.

### Reset Session

`Reset Session` currently:

- clears the stored job id
- clears the in-memory state
- resets the visible inputs

This was added because relaunching while preserving old inputs felt like cache confusion during testing.

### Convert Modal

The convert modal asks for two decisions:

1. Color scheme
   - preserve site color scheme
   - use ad color scheme
2. Asset behavior
   - use placeholder assets
   - use searched assets

This kept the main screen simple while preserving important operator control.

## Preview Behavior

The preview uses a fitted viewport component to scale the output into the available stage while avoiding awkward nested scroll behavior.

The main preview goals are:

- no small nested scrollbars
- no split-screen clutter
- clear centering
- preserve aspect ratio
- show the converted result prominently

## Intentionally Removed UI Elements

Several UI elements existed during development but were intentionally removed:

- phase labels
- model names in buttons and panels
- backend-ready text label
- pipeline notes panel
- brand JSON editor in the main interface
- source HTML download
- multiple debug status tags

These removals were not cosmetic only. They were part of simplifying the product into a cleaner operator experience.

## Current Operator Output

The UI now exposes only the final deliverable:

- preview of final reskinned HTML
- download of final output HTML

Intermediate reconstruction remains part of the pipeline but is not treated as the product deliverable.
