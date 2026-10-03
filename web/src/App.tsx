import { useEffect, useRef } from "react";
import { Canvas } from "./components/Canvas";
import { Inspector } from "./components/Inspector";
import { LeftPanel } from "./components/LeftPanel";
import { TopBar } from "./components/TopBar";
import { useIsNarrow } from "./lib/util";
import { useStore } from "./store";

/** A side panel: a fixed column on wide screens, a slide-in drawer below 900px. */
function Panel({
  side,
  label,
  narrow,
  children,
}: {
  side: "left" | "right";
  label: string;
  narrow: boolean;
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
      <aside aria-label={label} className={`bg-panel min-h-0 ${side === "left" ? "border-r" : "border-l"} border-rule`}>
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
      <div className={`flex-1 min-h-0 grid ${narrow ? "grid-cols-1" : "grid-cols-[280px_minmax(0,1fr)_400px]"}`}>
        <Panel side="left" label="Search and filters" narrow={narrow}>
          <LeftPanel />
        </Panel>
        <main id="center" tabIndex={-1} className="min-w-0 min-h-0 bg-paper outline-none">
          <Canvas />
        </main>
        <Panel side="right" label="Details" narrow={narrow}>
          <Inspector />
        </Panel>
      </div>
    </div>
  );
}
