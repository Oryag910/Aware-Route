// Brand wordmark mark: a looping trail line that terminates in a location
// pin — "a route that finds a stop." Flat, two brand colors, favicon-safe.
// Hex literals (not CSS vars) so it renders reliably as SVG presentation
// attributes across browsers.

type AwareMarkProps = {
  className?: string;
};

export default function AwareMark({ className = "h-7 w-7" }: AwareMarkProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      {/* looping route */}
      <path
        d="M4 15c0-4.5 3.5-8 8-8s7 3 7 6.5-3 5-6 3.5-2-5 1-6"
        stroke="#3f6b4f"
        strokeWidth="1.75"
        strokeLinecap="round"
        fill="none"
      />
      {/* pin planted at the route's start/end */}
      <path
        d="M4 10.5c0-2.2 1.8-4 4-4s4 1.8 4 4c0 2.8-4 6-4 6s-4-3.2-4-6Z"
        fill="#5b8aa6"
      />
      <circle cx="8" cy="10.5" r="1.4" fill="#ffffff" />
    </svg>
  );
}
