import { useState, type FormEvent } from "react";

export type ElevationPreference = "flat" | "moderate" | "hilly";

export type RouteShape = "round" | "out_and_back" | "mix";

export type RouteFormValues = {
  targetDistanceMiles: number;
  restroomMinMile: number;
  restroomMaxMile: number;
  elevationPreference: ElevationPreference;
  shape: RouteShape;
  runTime: string;
  workoutType: string;
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
  const [shape, setShape] = useState<RouteShape>("mix");
  const [runTime, setRunTime] = useState("");
  const [workoutType, setWorkoutType] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    onSubmit({
      targetDistanceMiles: Number(targetDistanceMiles),
      restroomMinMile: Number(restroomMinMile),
      restroomMaxMile: Number(restroomMaxMile),
      elevationPreference,
      shape,
      runTime,
      workoutType,
    });
  }

  const labelClass = "block text-sm font-medium text-slate-700 mb-1";
  const controlClass =
    "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-900">
        Route preferences
      </h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="target-distance" className={labelClass}>
            Target distance in miles
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
          <label htmlFor="elevation-preference" className={labelClass}>
            Elevation preference
          </label>
          <select
            id="elevation-preference"
            value={elevationPreference}
            onChange={(event) =>
              setElevationPreference(
                event.target.value as ElevationPreference,
              )
            }
            className={controlClass}
          >
            <option value="flat">Flat</option>
            <option value="moderate">Moderate</option>
            <option value="hilly">Hilly</option>
          </select>
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

        <div>
          <label htmlFor="restroom-min-mile" className={labelClass}>
            Restroom minimum mile
          </label>
          <input
            id="restroom-min-mile"
            type="number"
            min="0"
            step="0.1"
            value={restroomMinMile}
            onChange={(event) => setRestroomMinMile(event.target.value)}
            required
            className={controlClass}
          />
        </div>

        <div>
          <label htmlFor="restroom-max-mile" className={labelClass}>
            Restroom maximum mile
          </label>
          <input
            id="restroom-max-mile"
            type="number"
            min="0"
            step="0.1"
            value={restroomMaxMile}
            onChange={(event) => setRestroomMaxMile(event.target.value)}
            required
            className={controlClass}
          />
        </div>

        <div>
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

        <div>
          <label htmlFor="workout-type" className={labelClass}>
            Workout type
          </label>
          <select
            id="workout-type"
            value={workoutType}
            onChange={(event) => setWorkoutType(event.target.value)}
            className={controlClass}
          >
            <option value="">Any run</option>
            <option value="easy">Easy</option>
            <option value="tempo">Tempo</option>
            <option value="long">Long</option>
            <option value="hills">Hills</option>
            <option value="intervals">Intervals</option>
          </select>
        </div>
      </div>

      <button
        type="submit"
        disabled={startPosition === null || isLoading}
        className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {isLoading ? "Generating..." : "Generate Route"}
      </button>

      {startPosition === null && (
        <p className="text-sm text-slate-500">
          Select a starting point on the map first.
        </p>
      )}
    </form>
  );
}
