create table restrooms (
    id uuid primary key default gen_random_uuid(),
    source_id text unique not null,
    facility_name text not null,
    location_type text,
    operator text,
    status text not null,
    seasonality text,
    hours_of_operation text,
    accessibility text,
    restroom_type text,
    changing_stations text,
    additional_notes text,
    website text,
    latitude double precision not null,
    longitude double precision not null,
    borough text not null default 'Manhattan',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index restrooms_status_idx on restrooms (status);

alter table restrooms enable row level security;
