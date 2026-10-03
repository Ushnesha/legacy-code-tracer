import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FieldBrief, FnBrief } from "../api";
import { classColor } from "../lib/classColor";
import { plural, shortName, useDebounced } from "../lib/util";
import { useSearch } from "../queries";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";

type Item = { kind: "fn"; f: FnBrief } | { kind: "field"; f: FieldBrief };

export function Search({ repo }: { repo: string }) {
  const listId = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const q = useDebounced(text.trim(), 200);
  const res = useSearch(repo, q);
  const setRoot = useStore((s) => s.setRoot);
  const setField = useStore((s) => s.setField);
  const setTab = useStore((s) => s.setTab);
  const inputRef = useRef<HTMLInputElement>(null);

  const items: Item[] = useMemo(
    () => [
      ...(res.data?.functions ?? []).map((f) => ({ kind: "fn", f }) as const),
      ...(res.data?.fields ?? []).map((f) => ({ kind: "field", f }) as const),
    ],
    [res.data],
  );
  useEffect(() => setActive(0), [items]);

  const pick = (it: Item) => {
    if (it.kind === "fn") setRoot(it.f.id);
    else {
      setField(it.f.id);
      setTab("data");
    }
    setOpen(false);
  };

  const showList = open && q.length >= 2;
  // While you are still typing, the list on screen belongs to an older word: dim it and do not let Enter pick from it.
  const stale = text.trim() !== q || res.isPlaceholderData;
  const optionId = (i: number) => `${listId}-opt-${i}`;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!items.length) return;
      e.preventDefault();
      setOpen(true);
      setActive((a) => (e.key === "ArrowDown" ? (a + 1) % items.length : (a - 1 + items.length) % items.length));
    } else if (e.key === "Enter") {
      const it = items[active];
      if (showList && it && !stale) {
        e.preventDefault();
        pick(it);
      }
    } else if (e.key === "Escape") {
      if (open) setOpen(false);
      else setText("");
    }
  };

  const fnCount = res.data?.functions.length ?? 0;

  return (
    <section aria-label="Search" className="p-4 pb-2">
      <label htmlFor="search" className="block font-semibold mb-1">
        Find a function or piece of data
      </label>
      <div className="relative">
        <input
          id="search"
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && items.length ? optionId(active) : undefined}
          autoComplete="off"
          spellCheck={false}
          value={text}
          placeholder="Type a name. Press / to jump here."
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="w-full border border-rule rounded-node px-3 py-2 bg-panel placeholder:text-muted"
        />
        <ProgressBar active={res.isFetching} label="Searching" />
      </div>

      <div className="mt-2" aria-live="polite">
        {res.isError && <ErrorNote error={res.error} what="search results" onRetry={() => res.refetch()} />}
        {showList && !res.isError && res.data && items.length === 0 && (
          <p className="text-muted text-sm">Nothing matches “{q}”. Try a shorter name.</p>
        )}
        {showList && items.length > 0 && (
          <ul id={listId} role="listbox" aria-label="Search results" aria-busy={stale} className={`border border-rule rounded-node bg-panel max-h-80 overflow-auto ${stale ? "opacity-60" : ""}`}>
            {items.map((it, i) => {
              const isFn = it.kind === "fn";
              const showHeading = i === 0 || (isFn ? false : i === fnCount);
              return (
                <li key={`${it.kind}-${it.f.id}`} role="presentation">
                  {showHeading && (
                    <div className="px-3 pt-2 pb-1 text-xs text-muted" role="presentation">
                      {isFn ? "Functions" : "Data"}
                    </div>
                  )}
                  <div
                    id={optionId(i)}
                    role="option"
                    aria-selected={i === active}
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pick(it)}
                    className={`px-3 py-2 cursor-pointer border-l-4 ${i === active ? "bg-paper" : ""}`}
                    style={{ borderLeftColor: classColor(it.f.container) }}
                  >
                    <div className="mono text-sm break-all">{isFn ? shortName(it.f) : `${shortName(it.f)}`}</div>
                    <div className="text-xs text-muted">
                      {isFn ? (
                        <>
                          {it.f.file}:{it.f.line_start}
                          {it.f.summary ? ` · ${it.f.summary}` : ""}
                        </>
                      ) : (
                        <>
                          {it.f.type} · {it.f.file}:{it.f.line}
                        </>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {showList && items.length > 0 && (
          <p className="sr-only">{plural(items.length, "result")}. Use the arrow keys, then Enter.</p>
        )}
      </div>
    </section>
  );
}
