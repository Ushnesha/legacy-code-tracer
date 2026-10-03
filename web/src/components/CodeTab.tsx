import { useEffect, useMemo, useState } from "react";
import { highlightLines, type Token } from "../lib/highlight";
import { marksByLine, type CalleeMark } from "../lib/inspect";
import { useFunction } from "../queries";
import { useTreeStore } from "../graph/treeStore";
import { useStore } from "../store";
import { ErrorNote } from "./ErrorNote";
import { ProgressBar } from "./ProgressBar";

const FONT_ITALIC = 1;
const FONT_BOLD = 2;

function Code({ tokens, line }: { tokens: Token[] | undefined; line: string }) {
  if (!tokens) return <code className="whitespace-pre pr-3">{line || " "}</code>;
  return (
    <code className="whitespace-pre pr-3">
      {tokens.length === 0 && " "}
      {tokens.map((t, i) => (
        <span
          key={i}
          style={{
            color: t.color,
            fontStyle: (t.fontStyle ?? 0) & FONT_ITALIC ? "italic" : undefined,
            fontWeight: (t.fontStyle ?? 0) & FONT_BOLD ? 600 : undefined,
          }}
        >
          {t.content}
        </span>
      ))}
    </code>
  );
}

type Menu = { line: number; kind: "call" | "field"; x: number; y: number };

export function CodeTab({ repo, id }: { repo: string; id: string }) {
  const q = useFunction(repo, id);
  const d = q.data;
  const code = d?.source.code ?? null;
  const lang = (d?.node.language as string | undefined) ?? "cpp";
  const start = d?.source.start ?? 1;
  const lines = useMemo(() => code?.split("\n") ?? [], [code]);
  const marks = useMemo(() => (d ? marksByLine(d) : new Map()), [d]);
  const selectCallee = useTreeStore((s) => s.selectCallee);
  const setField = useStore((s) => s.setField);
  const setTab = useStore((s) => s.setTab);

  const [tokens, setTokens] = useState<Token[][] | null>(null);
  useEffect(() => {
    let live = true;
    setTokens(null);
    if (code)
      highlightLines(code, lang)
        .then((t) => live && setTokens(t))
        .catch(() => undefined); // plain text is fine if the highlighter cannot load
    return () => {
      live = false;
    };
  }, [code, lang]);

  const [menu, setMenu] = useState<Menu | null>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (e: KeyboardEvent | MouseEvent) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      setMenu(null);
    };
    window.addEventListener("keydown", close);
    window.addEventListener("mousedown", close);
    return () => {
      window.removeEventListener("keydown", close);
      window.removeEventListener("mousedown", close);
    };
  }, [menu]);

  const pickCallee = (c: CalleeMark) => {
    setMenu(null);
    void selectCallee(c.id);
  };
  const pickField = (fieldId: string) => {
    setMenu(null);
    setField(fieldId);
    setTab("data");
  };

  const mark = "size-4 grid place-items-center rounded-node text-[10px] leading-none hover:outline hover:outline-1 hover:outline-ink";

  return (
    <div>
      <ProgressBar active={q.isFetching} label="Loading the code" />
      <div className="p-4">
        {q.isError && <ErrorNote error={q.error} what="the code" onRetry={() => q.refetch()} />}
        {d && !d.source.available && (
          <p className="text-muted">
            The source file isn't on this machine, so the code can't be shown. Check that the repo is cloned under <code>repos/</code>.
          </p>
        )}
        {d?.source.available && (
          <>
            <div className="flex items-baseline justify-between gap-2 mb-2">
              <p className="text-sm text-muted mono">
                {d.source.file}, lines {d.source.start} to {d.source.end}
              </p>
              {d.github_permalink && (
                <a className="text-sm underline underline-offset-2 hover:no-underline shrink-0" href={d.github_permalink} target="_blank" rel="noreferrer">
                  Open on GitHub
                </a>
              )}
            </div>
            <p className="text-xs text-muted mb-2" aria-label="What the marks in the margin mean">
              <span aria-hidden="true">▸</span> calls something · <span className="text-writes">■</span> changes data ·{" "}
              <span className="text-reads">■</span> reads data
            </p>
            <pre className="border border-rule rounded-node bg-panel overflow-auto text-[13px] leading-5 py-2 relative" tabIndex={0} aria-label="Source code" data-testid="code">
              {lines.map((l, i) => {
                const n = start + i;
                const m = marks.get(n);
                return (
                  <div key={i} className="flex" data-line={n}>
                    <span className="flex items-center gap-0.5 w-14 shrink-0 pl-1.5" role="group" aria-label={`Marks for line ${n}`}>
                      <span className="w-4">
                        {m && m.calls.length > 0 && (
                          <button
                            type="button"
                            className={mark}
                            data-testid="gutter-call"
                            data-line={n}
                            aria-label={`Line ${n} calls ${m.calls.map((c: CalleeMark) => c.name).join(", ")}. Select it in the tree.`}
                            title={`Calls ${m.calls.map((c: CalleeMark) => c.name).join(", ")}`}
                            onMouseDown={(e) => e.stopPropagation()}
                            onClick={(e) => {
                              if (m.calls.length === 1) pickCallee(m.calls[0]!);
                              else setMenu({ line: n, kind: "call", x: e.clientX, y: e.clientY });
                            }}
                          >
                            ▸
                          </button>
                        )}
                      </span>
                      <span className="w-4">
                        {m && m.writes.length > 0 && (
                          <button
                            type="button"
                            className={`${mark} text-writes`}
                            data-testid="gutter-write"
                            data-line={n}
                            aria-label={`Line ${n} changes ${m.writes.map((f: { name: string }) => f.name).join(", ")}`}
                            title={`Changes ${m.writes.map((f: { name: string }) => f.name).join(", ")}`}
                            onMouseDown={(e) => e.stopPropagation()}
                            onClick={(e) => (m.writes.length === 1 ? pickField(m.writes[0]!.id) : setMenu({ line: n, kind: "field", x: e.clientX, y: e.clientY }))}
                          >
                            ■
                          </button>
                        )}
                      </span>
                      <span className="w-4">
                        {m && m.reads.length > 0 && (
                          <button
                            type="button"
                            className={`${mark} text-reads`}
                            data-testid="gutter-read"
                            data-line={n}
                            aria-label={`Line ${n} reads ${m.reads.map((f: { name: string }) => f.name).join(", ")}`}
                            title={`Reads ${m.reads.map((f: { name: string }) => f.name).join(", ")}`}
                            onMouseDown={(e) => e.stopPropagation()}
                            onClick={(e) => (m.reads.length === 1 ? pickField(m.reads[0]!.id) : setMenu({ line: n, kind: "field", x: e.clientX, y: e.clientY }))}
                          >
                            ■
                          </button>
                        )}
                      </span>
                    </span>
                    <span className="select-none text-muted text-right w-9 pr-3 shrink-0">{n}</span>
                    <Code tokens={tokens?.[i]} line={l} />
                  </div>
                );
              })}
            </pre>
          </>
        )}
      </div>

      {menu && d && (
        <ul
          role="menu"
          aria-label={menu.kind === "call" ? "Pick a function to select" : "Pick a piece of data"}
          className="fixed z-50 border border-rule bg-panel p-1 text-sm max-h-60 overflow-auto"
          style={{ left: Math.min(menu.x, window.innerWidth - 260), top: Math.min(menu.y + 8, window.innerHeight - 200) }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {menu.kind === "call"
            ? marks.get(menu.line)!.calls.map((c: CalleeMark) => (
                <li key={c.id} role="none">
                  <button type="button" role="menuitem" className="block w-full text-left px-2 py-1 hover:bg-paper mono" onClick={() => pickCallee(c)}>
                    {c.container ? `${c.container}::${c.name}` : c.name}
                  </button>
                </li>
              ))
            : [...marks.get(menu.line)!.writes, ...marks.get(menu.line)!.reads].map((f: { id: string; name: string }, i: number) => (
                <li key={`${f.id}-${i}`} role="none">
                  <button type="button" role="menuitem" className="block w-full text-left px-2 py-1 hover:bg-paper mono" onClick={() => pickField(f.id)}>
                    {f.name}
                  </button>
                </li>
              ))}
        </ul>
      )}
    </div>
  );
}
