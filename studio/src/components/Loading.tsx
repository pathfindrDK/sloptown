// Miira's loading pattern (design guide § Loading): three accent tiles on a 3D wave.

export function LoadingTiles({ size = "md" }: { size?: "md" | "sm" | "xs" }) {
  return (
    <span className={`loading-tiles loading-tiles--${size}`} aria-hidden>
      {[0, 1, 2].map((col) => (
        <span key={col} className="loading-tiles__tile" style={{ ["--tile-col" as string]: col }}>
          <span className="loading-tiles__face" />
        </span>
      ))}
    </span>
  );
}

/** Tiles + text, centred in its box (`overlay` covers the positioned parent instead). */
export function Loading({ label, overlay = false }: { label?: string; overlay?: boolean }) {
  return (
    <div className={overlay ? "loading loading--overlay" : "loading"} role="status" aria-live="polite">
      <LoadingTiles />
      {label && <span>{label}</span>}
    </div>
  );
}
