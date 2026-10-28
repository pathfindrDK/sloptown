// Slides Miira's `.segmented__indicator` pill to the active segment (design guide: Toggles).
// Ported from miira3's packages/editor-sidepane/src/useSegmentedIndicator.ts; keep in step.
import { useLayoutEffect, type RefObject } from "react";

function update(track: HTMLElement) {
  const indicator = track.querySelector<HTMLElement>(".segmented__indicator");
  const active = track.querySelector<HTMLElement>('button[aria-pressed="true"]');
  if (!indicator) return;
  if (!active) {
    indicator.style.opacity = "0";
    return;
  }
  // The first placement jumps; only later moves slide.
  const firstReveal = indicator.style.opacity === "" || indicator.style.opacity === "0";
  if (firstReveal) indicator.style.transition = "none";
  indicator.style.opacity = "1";
  indicator.style.width = `${active.offsetWidth}px`;
  indicator.style.transform = `translateX(${active.offsetLeft}px)`;
  if (firstReveal) {
    void indicator.offsetWidth;
    indicator.style.transition = "";
  }
}

export function useSegmentedIndicator(trackRef: RefObject<HTMLElement | null>, activeKey: unknown) {
  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const run = () => update(track);
    run();
    // Segments change width when the pane is dragged or a label changes language.
    const ro = new ResizeObserver(run);
    ro.observe(track);
    track.querySelectorAll("button").forEach((b) => ro.observe(b));
    window.addEventListener("resize", run);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", run);
    };
  }, [trackRef, activeKey]);
}
