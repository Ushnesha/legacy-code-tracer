import { useRef } from "react";
import { useFunction } from "../queries";
import { useSelectedFnId } from "../graph/treeStore";
import { useStore, type TabId } from "../store";
import { CodeTab } from "./CodeTab";
import { DataTab } from "./DataTab";
import { ExplainTab } from "./ExplainTab";
import { ImpactTab } from "./ImpactTab";
import { ReviewTab } from "./ReviewTab";

const TABS: { id: TabId; label: string }[] = [
  { id: "explain", label: "Explain" },
  { id: "code", label: "Code" },
  { id: "data", label: "Data" },
  { id: "impact", label: "Impact" },
  { id: "review", label: "Review" },
];

export function Inspector() {
  const { repo, tab } = useStore();
  const rootId = useSelectedFnId(); // the selected node, or the root when nothing is selected
  const setTab = useStore((s) => s.setTab);
  const flagged = useFunction(repo, rootId).data?.flags.length ?? 0; // shared with the Explain tab
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKey = (e: React.KeyboardEvent, i: number) => {
    const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (e.key === "Home" || e.key === "End" || move) {
      e.preventDefault();
      const n = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (i + move + TABS.length) % TABS.length;
      const next = TABS[n]!;
      setTab(next.id);
      refs.current[next.id]?.focus();
    }
  };

  const hasFunction = !!repo && !!rootId;
  const hasField = !!repo && !!useStore.getState().fieldId;

  return (
    <div className="h-full flex flex-col min-h-0">
      <div role="tablist" aria-label="Details" className="flex border-b border-rule shrink-0">
        {TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            id={`tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => setTab(t.id)}
            onKeyDown={(e) => onKey(e, i)}
            className={`px-4 py-2.5 -mb-px border-b-2 ${tab === t.id ? "border-ink font-semibold" : "border-transparent text-muted hover:text-ink"}`}
          >
            {t.label}
            {t.id === "review" && flagged > 0 && (
              <span data-testid="review-count" className="ml-1.5 rounded-node border border-unverified px-1.5 text-sm text-unverified">
                {flagged}
              </span>
            )}
          </button>
        ))}
      </div>
      <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} className="flex-1 min-h-0 overflow-auto">
        {!hasFunction && !(tab === "data" && hasField) ? (
          <p className="p-4 text-muted" data-testid="inspector-empty">
            Pick a function to see what it does.
          </p>
        ) : tab === "explain" ? (
          <ExplainTab repo={repo!} id={rootId!} />
        ) : tab === "code" ? (
          <CodeTab repo={repo!} id={rootId!} />
        ) : tab === "data" ? (
          <DataTab repo={repo!} id={rootId} />
        ) : tab === "review" ? (
          <ReviewTab repo={repo!} id={rootId!} />
        ) : (
          <ImpactTab repo={repo!} id={rootId!} />
        )}
      </div>
    </div>
  );
}
