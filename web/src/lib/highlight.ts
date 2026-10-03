// Syntax highlighting with shiki, loaded the first time the Code tab opens (the grammars are big).
// The theme only uses CSS variables (--shiki-*), which index.css maps onto our palette in both themes.
import type { HighlighterCore, ThemedToken } from "shiki/core";

export type Lang = "cpp" | "ada";
export type Token = Pick<ThemedToken, "content" | "color" | "fontStyle">;

let highlighter: Promise<HighlighterCore> | null = null;
const loaded = new Set<Lang>();

async function get(): Promise<HighlighterCore> {
  highlighter ??= (async () => {
    const [{ createHighlighterCore, createCssVariablesTheme }, { createJavaScriptRegexEngine }] = await Promise.all([
      import("shiki/core"),
      import("shiki/engine/javascript"),
    ]);
    return createHighlighterCore({
      themes: [createCssVariablesTheme({ name: "aerotrace", variablePrefix: "--shiki-", variableDefaults: {}, fontStyle: true })],
      langs: [],
      engine: createJavaScriptRegexEngine(),
    });
  })();
  return highlighter;
}

const GRAMMARS: Record<Lang, () => Promise<unknown>> = {
  cpp: () => import("shiki/langs/cpp.mjs"),
  ada: () => import("shiki/langs/ada.mjs"),
};

/** One array of tokens per source line. */
export async function highlightLines(code: string, lang: string): Promise<Token[][]> {
  const l: Lang = lang === "ada" ? "ada" : "cpp";
  const h = await get();
  if (!loaded.has(l)) {
    await h.loadLanguage((await GRAMMARS[l]()) as never);
    loaded.add(l);
  }
  return h.codeToTokensBase(code, { lang: l, theme: "aerotrace" });
}
