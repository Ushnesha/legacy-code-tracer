import { useEffect, useRef, useState } from "react";
import { Canvas } from "./components/Canvas";
import { Inspector } from "./components/Inspector";
import { LeftPanel } from "./components/LeftPanel";
import { TopBar } from "./components/TopBar";
import { useIsNarrow } from "./lib/util";
import { useStore } from "./store";

const MIN_W = 320;
const DEFAULT_W = 400;
const maxWidth = () => Math.max(MIN_W, Math.min(1000, window.innerWidth - 280 - 200));

function loadWidth(): number {
  try {
    const n = Number(localStorage.getItem("detailsWidth"));
    return n >= MIN_W ? n : DEFAULT_W;
  } catch {
    return DEFAULT_W;
  }
}

/** A thin bar on the left edge of the details panel: drag to resize, double-click to switch between normal and wide. */
function ResizeHandle({ width, setWidth }: { width: number; setWidth: (w: number) => void }) {
  const clamp = (w: number) => Math.round(Math.min(maxWidth(), Math.max(MIN_W, w)));
  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    const move = (m: PointerEvent) => setWidth(clamp(startW + startX - m.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize details panel. Double-click to widen or reset."
      tabIndex={0}
      data-testid="details-resize"
      onPointerDown={onDown}
      onDoubleClick={() => setWidth(width > DEFAULT_W + 40 ? DEFAULT_W : clamp(760))}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") setWidth(clamp(width + 40));
        if (e.key === "ArrowRight") setWidth(clamp(width - 40));
      }}
      className="absolute left-0 top-0 bottom-0 w-1.5 -translate-x-1/2 z-10 cursor-col-resize hover:bg-ink/20 focus-visible:bg-ink/30 touch-none"
    />
  );
}

/** A side panel: a fixed column on wide screens, a slide-in drawer below 900px. */
function Panel({
  side,
  label,
  narrow,
  resize,
  children,
}: {
  side: "left" | "right";
  label: string;
  narrow: boolean;
  resize?: { width: number; setWidth: (w: number) => void };
  children: React.ReactNode;
}) {
  const open = useStore((s) => s.drawer === side);
  const setDrawer = useStore((s) => s.setDrawer);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (narrow && open) ref.current?.querySelector<HTMLElement>("input, button, [tabindex='0']")?.focus();
  }, [narrow, open]);

  if (!narrow) {
    return (
      <aside aria-label={label} className={`bg-panel min-h-0 relative ${side === "left" ? "border-r" : "border-l"} border-rule`}>
        {resize && <ResizeHandle {...resize} />}
        {children}
      </aside>
    );
  }
  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Close panel"
          onClick={() => setDrawer(null)}
          className="fixed inset-0 top-12 z-20 bg-ink/40 cursor-default"
          tabIndex={-1}
        />
      )}
      <aside
        ref={ref}
        aria-label={label}
        inert={!open}
        className={`fixed top-12 bottom-0 z-30 w-[min(92vw,380px)] bg-panel border-rule ${
          side === "left" ? "left-0 border-r" : "right-0 border-l"
        } ${
          open
            ? "translate-x-0 visible [transition:transform_.2s,visibility_0s]"
            : `invisible [transition:transform_.2s,visibility_0s_.2s] ${side === "left" ? "-translate-x-full" : "translate-x-full"}`
        }`}
      >
        {children}
      </aside>
    </>
  );
}

export default function App() {
  const narrow = useIsNarrow();
  const theme = useStore((s) => s.theme);
  const setDrawer = useStore((s) => s.setDrawer);
  const drawer = useStore((s) => s.drawer);
  const [width, setWidthState] = useState(loadWidth);
  const setWidth = (w: number) => {
    setWidthState(w);
    try {
      localStorage.setItem("detailsWidth", String(w));
    } catch {
      /* not remembered, still works */
    }
  };

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Wide again: drawers make no sense, so close them.
  useEffect(() => {
    if (!narrow && drawer) setDrawer(null);
  }, [narrow, drawer, setDrawer]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && useStore.getState().drawer) {
        setDrawer(null);
        return;
      }
      if (e.key === "Escape" && useStore.getState().tour !== null) {
        useStore.getState().setTour(null);
        return;
      }
      if (e.key === "Escape" && useStore.getState().fieldId) {
        useStore.getState().setField(null); // Esc clears the data lens from anywhere
        return;
      }
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      e.preventDefault();
      if (window.matchMedia("(max-width: 899px)").matches) setDrawer("left");
      requestAnimationFrame(() => document.getElementById("search")?.focus());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setDrawer]);

  return (
    <div className="h-dvh flex flex-col">
      <a href="#center" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-panel focus:p-2">
        Skip to the main view
      </a>
      <TopBar narrow={narrow} />
      <div
        className={`flex-1 min-h-0 grid ${narrow ? "grid-cols-1" : "grid-cols-[280px_minmax(0,1fr)_var(--details-w)]"}`}
        style={{ "--details-w": `${Math.min(width, maxWidth())}px` } as React.CSSProperties}
      >
        <Panel side="left" label="Search and filters" narrow={narrow}>
          <LeftPanel />
        </Panel>
        <main id="center" tabIndex={-1} className="min-w-0 min-h-0 bg-paper outline-none">
          <Canvas />
        </main>
        <Panel side="right" label="Details" narrow={narrow} resize={{ width, setWidth }}>
          <Inspector />
        </Panel>
      </div>
    </div>
  );
}
