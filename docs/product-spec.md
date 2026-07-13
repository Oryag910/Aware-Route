# Aware Running Route — Product Specification

## 1. Product Overview

Aware Running Route is a web-based running-route planner that generates routes based on a runner’s:

* Starting location
* Approximate target distance
* Desired restroom mile range
* Elevation preference

Unlike general-purpose mapping tools that only display nearby restrooms, Aware Running Route will use restroom placement as part of the route-generation and ranking process.

## 2. Problem

Urban runners planning longer runs often need access to a public restroom during their route.

Existing tools may generate routes or display restroom locations, but runners usually have to:

* Compare multiple maps
* Manually adjust routes
* Estimate when they will reach a restroom
* Check whether the restroom is likely to be available
* Balance restroom access with distance and elevation preferences

Aware Running Route aims to reduce this manual planning.

## 3. Target User

The initial target user is an urban runner planning a longer run, generally between 6 and 20 miles, who wants a public restroom available during a specific portion of the route.

The first supported users will be runners in Manhattan.

## 4. Core User Story

> As an urban runner planning a long run, I want a route near my target distance that passes an expected-open public restroom during my preferred mile range so that I can run without manually planning restroom access.

## 5. Product Goals

The initial product should allow a runner to:

1. Select a starting location.
2. Enter an approximate target distance.
3. Choose the mile range during which they want restroom access.
4. Select an elevation preference.
5. Receive up to three ranked running routes.
6. Understand why one route ranked above another.
7. View restroom availability information and uncertainty.
8. Export a selected route as a GPX file.

## 6. Core Inputs

### Starting Location

The user selects a point on the map.

Address search is not required for the initial version.

### Target Distance

The user enters the approximate desired route distance.

Generated routes do not need to match the target exactly. The application will display the actual distance and the difference from the requested distance.

### Restroom Mile Range

The user specifies when during the run they want restroom access.

Example:

> Between miles 4 and 6.

The application should prefer routes that pass an eligible restroom during that range.

### Elevation Preference

Elevation is a soft preference that affects route ranking.

Initial options may include:

* Flatter route
* Balanced route
* Hillier route

Elevation should not automatically reject an otherwise valid route during the initial MVP.

## 7. Core Outputs

For each generated route, the application should display:

* Route geometry
* Actual distance
* Difference from the target distance
* Estimated elevation gain
* Public-restroom location
* Restroom mile marker
* Restroom operating information
* Restroom confidence or uncertainty
* Explanation of the route’s ranking
* GPX export option

## 8. Functional Requirements

### Route Generation

The system must:

* Generate running routes that begin and end near the selected starting point.
* Produce approximate loop routes.
* Use legal pedestrian paths supplied by a routing provider.
* Include an eligible public restroom.
* Attempt to place the restroom within the requested mile range.
* Generate multiple candidates when possible.
* Reject clearly invalid routes.
* Rank valid routes using explicit scoring rules.

### Restroom Selection

The system must:

* Use officially listed public-restroom data.
* Exclude records without usable coordinates.
* Consider operational status, seasonality, and available hours.
* Avoid guaranteeing real-time availability.
* Preserve the original data source.
* Clearly communicate uncertainty.

### Route Ranking

Initial ranking should consider:

* Distance error
* Restroom mile-range error
* Elevation preference
* Repeated route segments
* Restroom confidence
* Similarity to other returned routes

### Route Display

The frontend must:

* Display the selected starting location.
* Display generated route alternatives.
* Display the associated restroom.
* Allow the user to switch between route alternatives.
* Show loading, failure, and no-route states clearly.

### GPX Export

The user must be able to download a selected route as a valid GPX file for use in another compatible application.

## 9. Nonfunctional Requirements

### Explainability

The system should explain why each route received its ranking.

### Privacy

The system should:

* Allow anonymous route generation.
* Avoid storing exact starting locations by default.
* Never publish routes automatically.
* Avoid collecting medical information.
* Avoid exposing API keys in frontend code.

### Reliability

The system should:

* Handle routing-provider failures.
* Set limits on external API requests.
* Prevent uncontrolled candidate generation.
* Return useful error messages.
* Avoid claiming unavailable data is accurate.

### Testability

Core route logic should be testable without calling live external APIs.

Saved and sanitized provider responses should be usable as test fixtures.

## 10. Initial Geographic Scope

The first version will support:

* New York City
* Manhattan as the initial development and testing area

Nationwide coverage is not part of the MVP.

## 11. MVP Scope

The MVP includes:

* Running routes only
* Map-based start selection
* Approximate distance input
* Restroom mile-range input
* Elevation preference
* Public-restroom data
* Up to three ranked route options
* Route and restroom map display
* Restroom uncertainty information
* GPX export

## 12. Non-Goals

The MVP will not include:

* Cycling routes
* Live turn-by-turn navigation
* Live run tracking
* Guaranteed restroom availability
* Nationwide coverage
* Water-fountain planning
* Weather-based routing
* Shade-based routing
* Traffic-light optimization
* Garmin integration
* Strava integration
* Smartwatch applications
* Social profiles
* Followers
* Public route sharing
* Free-text restroom reviews
* Payments or subscriptions

## 13. Data Limitations

Public-restroom data may be:

* Incomplete
* Outdated
* Temporarily inaccurate
* Missing operating hours
* Missing accessibility details
* Incorrect about temporary closures

The application must not promise that a restroom will be open.

Instead, it should communicate whether a restroom is:

* Listed as open
* Officially listed but unverified
* Recently reported open
* Recently reported closed
* Of uncertain status

## 14. Initial Acceptance Criteria

The MVP will be considered successful when:

1. A user can select a Manhattan starting point.
2. A user can enter a target distance.
3. A user can enter a desired restroom mile range.
4. A user can select an elevation preference.
5. The backend can generate at least one valid route for supported test scenarios.
6. The route begins and ends near the selected starting location.
7. The route passes an eligible restroom.
8. The restroom’s mile marker is calculated.
9. Actual distance and estimated elevation are displayed.
10. Multiple meaningfully different routes are returned when available.
11. Impossible requests produce a useful message.
12. Restroom uncertainty is clearly displayed.
13. A route can be exported as GPX.
14. Core route-generation logic has automated tests.
15. External routing calls can be replaced with saved fixtures during testing.

## 15. Planned Pivot

If automatic loop generation cannot reliably produce useful routes, the project will shift to:

> A route-analysis tool that allows the user to import or draw an existing route and calculates the smallest valid detour through a public restroom during a selected mile range.

This pivot would preserve the restroom data, geospatial logic, map interface, routing integration, and GPX export work.

## 16. Current Status

The project is currently in Phase 0: project definition and repository setup.

No application code has been written yet.

The next phases will validate:

* Whether runners experience this problem regularly
* Whether NYC restroom data is usable
* Whether the routing provider can generate suitable pedestrian loops
* Whether restroom-constrained routes can be created reliably

