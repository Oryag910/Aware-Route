import { useState, type FormEvent } from "react";

export type ElevationPreference = "flat" | "moderate" | "hilly";

export type RouteFormValues = {
  targetDistanceMiles: number;
  restroomMinMile: number;
  restroomMaxMile: number;
  elevationPreference: ElevationPreference;
};

type RouteFormProps = {
  startPosition: [number, number] | null;
  onSubmit: (values: RouteFormValues) => void;
  isLoading: boolean;
};

export default function RouteForm({
  startPosition,
  onSubmit,
  isLoading,
}: RouteFormProps) {
  const [targetDistanceMiles, setTargetDistanceMiles] = useState("5");
  const [restroomMinMile, setRestroomMinMile] = useState("1");
  const [restroomMaxMile, setRestroomMaxMile] = useState("4");
  const [elevationPreference, setElevationPreference] =
    useState<ElevationPreference>("moderate");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    onSubmit({
      targetDistanceMiles: Number(targetDistanceMiles),
      restroomMinMile: Number(restroomMinMile),
      restroomMaxMile: Number(restroomMaxMile),
      elevationPreference,
    });
  }

  return (
    <form onSubmit={handleSubmit}>
      <div>
        <label htmlFor="target-distance">Target distance in miles</label>
        <input
          id="target-distance"
          type="number"
          min="0.1"
          step="0.1"
          value={targetDistanceMiles}
          onChange={(event) => setTargetDistanceMiles(event.target.value)}
          required
        />
      </div>

      <div>
        <label htmlFor="restroom-min-mile">Restroom minimum mile</label>
        <input
          id="restroom-min-mile"
          type="number"
          min="0"
          step="0.1"
          value={restroomMinMile}
          onChange={(event) => setRestroomMinMile(event.target.value)}
          required
        />
      </div>

      <div>
        <label htmlFor="restroom-max-mile">Restroom maximum mile</label>
        <input
          id="restroom-max-mile"
          type="number"
          min="0"
          step="0.1"
          value={restroomMaxMile}
          onChange={(event) => setRestroomMaxMile(event.target.value)}
          required
        />
      </div>

      <div>
        <label htmlFor="elevation-preference">Elevation preference</label>
        <select
          id="elevation-preference"
          value={elevationPreference}
          onChange={(event) =>
            setElevationPreference(
              event.target.value as ElevationPreference,
            )
          }
        >
          <option value="flat">Flat</option>
          <option value="moderate">Moderate</option>
          <option value="hilly">Hilly</option>
        </select>
      </div>

      <button
        type="submit"
        disabled={startPosition === null || isLoading}
      >
        {isLoading ? "Generating..." : "Generate Route"}
      </button>

      {startPosition === null && (
        <p>Select a starting point on the map first.</p>
      )}
    </form>
  );
}
