import { useRef, type ReactNode } from "react";

export type SheetSnap = "peek" | "half" | "full";

// Snap heights as fractions of the viewport. Peek shows just the handle +
// one-line summary; full leaves a sliver of map for orientation.
const SNAP_FRACTION: Record<SheetSnap, number> = {
  peek: 0.16,
  half: 0.52,
  full: 0.92,
};

const ORDER: SheetSnap[] = ["peek", "half", "full"];

type BottomSheetProps = {
  snap: SheetSnap;
  onSnapChange: (snap: SheetSnap) => void;
  /** One-line content always visible at the peek height. */
  peek: ReactNode;
  /** Scrollable body (form / results / states). */
  children: ReactNode;
  /** Pinned footer (e.g. GPX button); omitted when nothing to show. */
  footer?: ReactNode;
};

/**
 * A dependency-free mobile bottom sheet with three snap states. Dragging the
 * handle resizes live and snaps to the nearest state on release; tapping it
 * cycles peek → half → full → peek for a no-gesture / a11y fallback.
 */
export default function BottomSheet({
  snap,
  onSnapChange,
  peek,
  children,
  footer,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ startY: number; startPx: number } | null>(null);

  function viewportH() {
    return window.innerHeight;
  }

  function snapToNearest(px: number) {
    const frac = px / viewportH();
    let best: SheetSnap = "peek";
    let bestGap = Infinity;
    for (const s of ORDER) {
      const gap = Math.abs(SNAP_FRACTION[s] - frac);
      if (gap < bestGap) {
        bestGap = gap;
        best = s;
      }
    }
    onSnapChange(best);
  }

  function onTouchStart(event: React.TouchEvent) {
    const current = sheetRef.current?.getBoundingClientRect().height ?? 0;
    drag.current = { startY: event.touches[0].clientY, startPx: current };
  }

  function onTouchMove(event: React.TouchEvent) {
    if (drag.current === null || sheetRef.current === null) return;
    const delta = drag.current.startY - event.touches[0].clientY;
    const next = Math.min(
      viewportH() * 0.95,
      Math.max(viewportH() * 0.1, drag.current.startPx + delta),
    );
    // Drive height directly during the drag for a responsive feel; the
    // committed snap state takes over (via the class-based height) on release.
    sheetRef.current.style.height = `${next}px`;
  }

  function onTouchEnd() {
    if (drag.current === null || sheetRef.current === null) return;
    const height = sheetRef.current.getBoundingClientRect().height;
    // Clear the inline override so the snapped class height applies again.
    sheetRef.current.style.height = "";
    snapToNearest(height);
    drag.current = null;
  }

  function cycle() {
    const idx = ORDER.indexOf(snap);
    onSnapChange(ORDER[(idx + 1) % ORDER.length]);
  }

  return (
    <div
      ref={sheetRef}
      className="absolute inset-x-0 bottom-0 z-30 flex flex-col rounded-t-2xl border-t border-border bg-surface shadow-[0_-4px_24px_rgba(43,42,37,0.12)] transition-[height] duration-200 ease-out"
      style={{ height: `${SNAP_FRACTION[snap] * 100}dvh` }}
    >
      <button
        type="button"
        onClick={cycle}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        aria-label="Resize panel"
        className="flex flex-none touch-none justify-center py-2.5"
      >
        <span className="h-1.5 w-10 rounded-full bg-border" />
      </button>

      <div className="flex-none px-4 pb-1">{peek}</div>

      <div className="flex-1 overflow-y-auto px-4 py-3">{children}</div>

      {footer != null && (
        <div className="flex-none border-t border-border px-4 py-3">
          {footer}
        </div>
      )}
    </div>
  );
}
