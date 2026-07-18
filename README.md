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

## Deployment

### Render (Backend)

Deploy the FastAPI backend to [Render](https://render.com):

1. **From the repo blueprint**: Push this repo to GitHub. In Render, select "New" > "Blueprint" and point to your repository. Render will auto-detect the `render.yaml` at the repo root and deploy the backend service with the correct Docker build settings.

2. **Alternatively, manual setup**: Create a new Web Service on Render, connect your repository, and configure:
   - **Docker**: Build command uses `./backend/Dockerfile` with build context `./backend`
   - **Port**: Render injects the `PORT` env var; the Dockerfile binds uvicorn to it automatically
   - **Health check**: `/health` endpoint

Either way, set these environment variables in Render:

- `ORS_API_KEY`: Your OpenRouteService API key (required; get a free tier key at [openrouteservice.org](https://openrouteservice.org))
- `SUPABASE_URL`: Supabase project URL (required for restroom data)
- `SUPABASE_KEY`: Supabase anonymous key (required)
- `ALLOWED_ORIGINS`: Comma-separated list of frontend origins (e.g., `https://your-frontend.vercel.app`). If unset, no CORS headers are sent.

**Note**: Render's free tier spins down inactive services after 15 minutes. The first request after idle will be slow (cold start).

### Vercel (Frontend)

Deploy the React/Vite frontend to [Vercel](https://vercel.com):

1. Import your repository into Vercel
2. Configure the build:
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build` (default is fine)
   - **Output Directory**: `dist` (default)
3. Set the environment variable:
   - `VITE_API_URL`: The Render backend URL, e.g., `https://your-app.onrender.com` (no trailing slash; the `/api` prefix is not used in production)
4. Deploy and redeploy after setting `VITE_API_URL` (Vercel needs to rebuild with the variable set)

The `vercel.json` in `frontend/` configures SPA routing: all requests are rewritten to `index.html`.

### CORS Configuration

Cross-origin requests (from the frontend to the backend) are only allowed when `ALLOWED_ORIGINS` is set on the Render service. Set it to your Vercel frontend URL:

```
https://your-frontend.vercel.app
```

During local development, the Vite dev server proxies `/api/*` requests to `http://localhost:8000` (stripping the `/api` prefix), so CORS is not needed.

### Demo Rate Limits

The backend enforces two demo-tier rate limits:

- **Per-IP**: 10 requests/hour (sliding window)
- **Global**: 90 requests/day (sliding window)

**Why these limits**: Each route request costs up to 20 OpenRouteService API calls. The ORS free tier allows 2000 calls/day, so 90 requests/day uses at most 1800 calls, leaving headroom. The per-IP limit prevents single clients from exhausting the daily quota.

The rate limiter is in-memory and single-instance; it resets when the Render service restarts. At scale, you would replace it with a persistent store (e.g., Redis).

Clients hitting the rate limit receive a 429 response with the message: "Demo request limit reached — try again later."

