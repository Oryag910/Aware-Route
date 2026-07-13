# Aware Running Route Planner

A running-route planning tool that generates routes based on a runner’s starting location, target distance, elevation preference, and desired restroom access.

## Why This Project Exists

Planning a long run often requires more than choosing a starting point and distance. Runners may also need to consider where public restrooms are located, whether those restrooms are expected to be available, and at what point during the run they will be reached.

Existing mapping tools may display public restrooms or generate running routes, but restroom access usually does not influence how the route itself is created. Runners therefore have to manually compare maps, adjust routes, and estimate where restroom stops will fall during the run.

This project aims to make that process easier by treating restroom access as a route-planning requirement rather than simply displaying restrooms as map markers.

## Core Concept

A runner will eventually be able to provide:

* A starting location
* An approximate target distance
* A preferred restroom mile range
* An elevation preference

The application will use those preferences to generate and rank running routes that pass an eligible public restroom at a useful point during the run.

For example:

> Generate an approximately 10-mile loop starting near Columbia University, with a public restroom between miles 4 and 6 and a preference for a flatter route.

## Initial Scope

The initial version will support:

* Running routes only
* New York City only
* Manhattan as the first supported area
* Approximate route distances rather than guaranteed exact distances
* Officially listed public restrooms
* Elevation as a route-ranking preference
* Restroom availability information based on available public data

The initial target users are urban runners planning longer routes who want predictable restroom options during their run.

## Non-Goals

The initial version will not provide:

* Live turn-by-turn navigation
* Live run tracking
* Guaranteed real-time restroom availability
* Cycling routes
* Nationwide route coverage
* Weather or shade-based routing
* Water-fountain planning
* Garmin, Strava, or smartwatch integrations
* Social profiles, route sharing, or follower features

These features may be considered later, but only after the core restroom-aware route-generation experience works reliably.

## Important Limitation

Public-restroom information may be incomplete, outdated, or temporarily inaccurate. The application will not guarantee that a restroom is open or accessible.

Instead, it will communicate the available information and level of confidence associated with each restroom.

## Project Status

This project is currently in the planning and feasibility-validation stage. No production application has been built yet.

The initial work will focus on validating:

* Whether available public-restroom data is reliable enough
* Whether useful running loops can be generated around restroom constraints
* Whether runners consider this a meaningful and recurring problem

## Development Setup

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --reload-dir app
```

Backend health check:

```text
http://localhost:8000/health
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend:

```text
http://localhost:5173
```

