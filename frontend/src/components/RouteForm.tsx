import { useState, type FormEvent } from "react";

import StatusMessage from "./StatusMessage";
import { X } from "../icons";

export type RouteShape = "round" | "out_and_back" | "mix";

export type FacilityKind = "restroom" | "water";

export type FacilityMode = "none" | "restroom" | "water" | "both";

export type FacilityRequirementForm = {
  id: string;
  kind: FacilityKind;
  minMile: number;
  maxMile: number;
};

export type RouteFormValues = {
  targetDistanceMiles: number;
  facilityMode: FacilityMode;
  facilityRequirements: FacilityRequirementForm[];
  shape: RouteShape;
  runTime: string;
};

type RouteFormProps = {
  startPosition: [number, number] | null;
  onSubmit: (values: RouteFormValues) => void;
  isLoading: boolean;
};

const FACILITY_MODE_OPTIONS: { value: FacilityMode; label: string }[] = [
  { value: "none", label: "No facilities" },
  { value: "restroom", label: "Restrooms" },
  { value: "water", label: "Water" },
  { value: "both", label: "Restrooms + water" },
];

const FACILITY_KIND_LABELS: Record<FacilityKind, string> = {
  restroom: "restroom",
  water: "water",
};

function modeRequiresKind(mode: FacilityMode, kind: FacilityKind): boolean {
  return mode === "both" || mode === kind;
}

let requirementIdCounter = 0;

function makeRequirement(kind: FacilityKind): FacilityRequirementForm {
  requirementIdCounter += 1;
  // Default mirrors the old single-restroom-range default (1-4 mi) --
  // a sensible starting window for either facility kind.
  return {
    id: `req-${requirementIdCounter}-${Date.now()}`,
    kind,
    minMile: 1,
    maxMile: 4,
  };
}

export default function RouteForm({
  startPosition,
  onSubmit,
  isLoading,
}: RouteFormProps) {
  const [targetDistanceMiles, setTargetDistanceMiles] = useState("5");
  const [facilityMode, setFacilityMode] = useState<FacilityMode>("restroom");
  const [facilityRequirements, setFacilityRequirements] = useState<
    FacilityRequirementForm[]
  >([makeRequirement("restroom")]);
  const [shape, setShape] = useState<RouteShape>("mix");
  const [runTime, setRunTime] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  function handleModeChange(nextMode: FacilityMode) {
    setFacilityMode(nextMode);

    // Ensure the modes that need rows always have at least one, seeding a
    // default row the first time a kind becomes visible. Existing rows of
    // any kind are kept in state even when hidden (e.g. switching "both"
    // -> "restroom" doesn't delete the water rows) -- they're simply
    // filtered out of what gets submitted, based on the active mode at
    // submit time. This keeps mode-switching cheap and avoids ever losing
    // a user's in-progress edits by mistake.
    setFacilityRequirements((current) => {
      let next = current;

      if (
        modeRequiresKind(nextMode, "restroom") &&
        !next.some((r) => r.kind === "restroom")
      ) {
        next = [...next, makeRequirement("restroom")];
      }
      if (
        modeRequiresKind(nextMode, "water") &&
        !next.some((r) => r.kind === "water")
      ) {
        next = [...next, makeRequirement("water")];
      }

      return next;
    });
  }

  function handleAddRequirement(kind: FacilityKind) {
    setFacilityRequirements((current) => [...current, makeRequirement(kind)]);
  }

  function handleRemoveRequirement(id: string) {
    setFacilityRequirements((current) => {
      const target = current.find((r) => r.id === id);
      const withoutTarget = current.filter((r) => r.id !== id);

      // Never let a kind the active mode requires drop to zero rows
      // silently -- re-seed a default row instead of leaving a mode that
      // claims to require restroom and/or water stops with none of one
      // (or both, for "both" mode) kind.
      if (
        target !== undefined &&
        modeRequiresKind(facilityMode, target.kind) &&
        !withoutTarget.some((r) => r.kind === target.kind)
      ) {
        return [...withoutTarget, makeRequirement(target.kind)];
      }

      return withoutTarget;
    });
  }

  function handleRequirementChange(
    id: string,
    field: "minMile" | "maxMile",
    value: string,
  ) {
    setFacilityRequirements((current) =>
      current.map((requirement) =>
        requirement.id === id
          ? { ...requirement, [field]: Number(value) }
          : requirement,
      ),
    );
  }

  function activeRequirements(): FacilityRequirementForm[] {
    if (facilityMode === "none") return [];
    if (facilityMode === "both") return facilityRequirements;
    return facilityRequirements.filter((r) => r.kind === facilityMode);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const distance = Number(targetDistanceMiles);

    if (!(distance > 0)) {
      setFormError("Target distance must be greater than 0.");
      return;
    }

    const requirements = activeRequirements();

    for (const requirement of requirements) {
      const kindLabel = FACILITY_KIND_LABELS[requirement.kind];

      if (!(requirement.minMile >= 0)) {
        setFormError(`Minimum ${kindLabel} mile cannot be negative.`);
        return;
      }

      if (!(requirement.maxMile > requirement.minMile)) {
        setFormError(
          `Maximum ${kindLabel} mile must be greater than the minimum mile.`,
        );
        return;
      }

      if (requirement.maxMile > distance) {
        setFormError(
          `Requested ${kindLabel} range must fall within the route distance.`,
        );
        return;
      }
    }

    setFormError(null);
    onSubmit({
      targetDistanceMiles: distance,
      facilityMode,
      facilityRequirements: requirements,
      shape,
      runTime,
    });
  }

  const labelClass = "block text-sm font-medium text-ink mb-1.5";
  const subLabelClass = "block text-xs text-ink-muted mb-1";
  const controlClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:border-primary";

  const showRestroomRows =
    facilityMode === "restroom" || facilityMode === "both";
  const showWaterRows = facilityMode === "water" || facilityMode === "both";

  function renderRequirementRows(kind: FacilityKind) {
    const rows = facilityRequirements.filter((r) => r.kind === kind);
    const kindLabel = FACILITY_KIND_LABELS[kind];

    return (
      <div className="space-y-2">
        {rows.map((requirement, index) => (
          <div key={requirement.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <div>
              <label
                htmlFor={`${kind}-min-mile-${requirement.id}`}
                className={subLabelClass}
              >
                Min mile
              </label>
              <input
                id={`${kind}-min-mile-${requirement.id}`}
                type="number"
                min="0"
                step="0.1"
                value={requirement.minMile}
                onChange={(event) =>
                  handleRequirementChange(
                    requirement.id,
                    "minMile",
                    event.target.value,
                  )
                }
                required
                className={controlClass}
              />
            </div>
            <div>
              <label
                htmlFor={`${kind}-max-mile-${requirement.id}`}
                className={subLabelClass}
              >
                Max mile
              </label>
              <input
                id={`${kind}-max-mile-${requirement.id}`}
                type="number"
                min="0.1"
                step="0.1"
                value={requirement.maxMile}
                onChange={(event) =>
                  handleRequirementChange(
                    requirement.id,
                    "maxMile",
                    event.target.value,
                  )
                }
                required
                className={controlClass}
              />
            </div>
            <div className="flex items-end">
              <button
                type="button"
                onClick={() => handleRemoveRequirement(requirement.id)}
                aria-label={`Remove ${kindLabel} stop ${index + 1}`}
                className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-border text-ink-muted transition-colors hover:border-danger/50 hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={() => handleAddRequirement(kind)}
          className="text-sm font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded"
        >
          + Add {kindLabel}
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="font-display text-lg font-semibold text-ink">
        Plan your run
      </h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="target-distance" className={labelClass}>
            Target distance (mi)
          </label>
          <input
            id="target-distance"
            type="number"
            min="0.1"
            step="0.1"
            value={targetDistanceMiles}
            onChange={(event) => setTargetDistanceMiles(event.target.value)}
            required
            className={controlClass}
          />
        </div>

        <div>
          <label htmlFor="route-shape" className={labelClass}>
            Route shape
          </label>
          <select
            id="route-shape"
            value={shape}
            onChange={(event) => setShape(event.target.value as RouteShape)}
            className={controlClass}
          >
            <option value="mix">Mixed (best overall)</option>
            <option value="round">Round (loop)</option>
            <option value="out_and_back">Out &amp; back</option>
          </select>
        </div>

        <fieldset className="sm:col-span-2 m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>Requested stops</legend>

          <div
            role="radiogroup"
            aria-label="Facility requirements"
            className="grid grid-cols-2 gap-2 sm:grid-cols-4"
          >
            {FACILITY_MODE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={facilityMode === option.value}
                onClick={() => handleModeChange(option.value)}
                className={`rounded-lg border px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 ${
                  facilityMode === option.value
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border bg-surface text-ink-muted hover:border-primary/50"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>

          {(showRestroomRows || showWaterRows) && (
            <div className="mt-3 space-y-4">
              {showRestroomRows && (
                <div>
                  <p className={subLabelClass}>Restroom mile ranges</p>
                  {renderRequirementRows("restroom")}
                </div>
              )}
              {showWaterRows && (
                <div>
                  <p className={subLabelClass}>Water mile ranges</p>
                  {renderRequirementRows("water")}
                </div>
              )}
            </div>
          )}
        </fieldset>

        <div className="sm:col-span-2">
          <label htmlFor="run-time" className={labelClass}>
            Run time (optional)
          </label>
          <input
            id="run-time"
            type="datetime-local"
            value={runTime}
            onChange={(event) => setRunTime(event.target.value)}
            className={controlClass}
          />
        </div>
      </div>

      {formError !== null && (
        <StatusMessage variant="danger" heading="Check your inputs">
          {formError}
        </StatusMessage>
      )}

      <button
        type="submit"
        disabled={startPosition === null || isLoading}
        className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-on-primary transition-colors hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:bg-primary/95 disabled:cursor-not-allowed disabled:bg-ink-muted/30 disabled:text-ink-muted"
      >
        {isLoading ? "Building routes…" : "Find routes"}
      </button>

      {startPosition === null && (
        <p className="text-sm text-ink-muted">
          Drop a pin on the map to set your start.
        </p>
      )}
    </form>
  );
}
