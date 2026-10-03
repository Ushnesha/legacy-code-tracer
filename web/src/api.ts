// Typed client for every route in server/main.py. Types come from the server's OpenAPI schema
// (run `pnpm gen:api` after changing a route).
import type { components, paths } from "./api-types";

type JsonOf<P extends keyof paths> = paths[P] extends {
  get: { responses: { 200: { content: { "application/json": infer T } } } };
}
  ? T
  : never;

type Schemas = components["schemas"];
export type RepoInfo = Schemas["RepoInfo"];
export type FnBrief = Schemas["FnBrief"];
export type EntryPoint = Schemas["EntryPoint"];
export type FnWithEdge = Schemas["FnWithEdge"];
export type FieldBrief = Schemas["FieldBrief"];
export type FieldUse = Schemas["FieldUse"];
export type SearchResponse = Schemas["SearchResponse"];
export type SummaryInfo = Schemas["SummaryInfo"];
export type FunctionDetail = Schemas["FunctionDetail"];
export type TreeNode = Schemas["TreeNode"];
export type TreeResponse = Schemas["TreeResponse"];
export type FieldDetail = Schemas["FieldDetail"];
export type ImpactResponse = Schemas["ImpactResponse"];
export type StoryResponse = Schemas["StoryResponse"];
export type Rollup = Schemas["Rollup"];
export type RollupDetail = Schemas["RollupDetail"];
export type PathResponse = Schemas["PathResponse"];
export type StatusResponse = Schemas["StatusResponse"];
export type DataMapResponse = Schemas["DataMapResponse"];
export type DataMapField = Schemas["DataMapField"];
export type DataMapLink = Schemas["DataMapLink"];

// Compile-time check that each route still returns what we think it does.
export type ReposResponse = JsonOf<"/api/repos">;
export type EntryPointsResponse = JsonOf<"/api/repos/{repo}/entry-points">;

const BASE: string = import.meta.env.VITE_API_BASE ?? "";

/** A failed request. `status` 0 means the server could not be reached at all. */
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type Query = Record<string, string | number | boolean | undefined>;

async function get<T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined) qs.set(k, String(v));
  const url = `${BASE}${path}${qs.size ? `?${qs}` : ""}`;
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "The server could not be reached.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      /* not JSON: keep the status text */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const seg = (s: string) => encodeURIComponent(s);
const segPath = (s: string) => s.split("/").map(encodeURIComponent).join("/");

export type Direction = "down" | "up";

export const api = {
  status: (signal?: AbortSignal) => get<StatusResponse>("/api/status", undefined, signal),
  repos: (signal?: AbortSignal) => get<ReposResponse>("/api/repos", undefined, signal),
  entryPoints: (repo: string, limit = 100, signal?: AbortSignal) =>
    get<EntryPointsResponse>(`/api/repos/${seg(repo)}/entry-points`, { limit }, signal),
  search: (repo: string, q: string, limit = 20, signal?: AbortSignal) =>
    get<SearchResponse>(`/api/repos/${seg(repo)}/search`, { q, limit }, signal),
  fn: (repo: string, id: string, signal?: AbortSignal) =>
    get<FunctionDetail>(`/api/repos/${seg(repo)}/functions/${seg(id)}`, undefined, signal),
  tree: (
    repo: string,
    root: string,
    o: { direction?: Direction; depth?: number; includeNoise?: boolean; includeMayCall?: boolean } = {},
    signal?: AbortSignal,
  ) =>
    get<TreeResponse>(
      `/api/repos/${seg(repo)}/tree`,
      {
        root,
        direction: o.direction,
        depth: o.depth,
        include_noise: o.includeNoise,
        include_may_call: o.includeMayCall,
      },
      signal,
    ),
  field: (repo: string, id: string, signal?: AbortSignal) =>
    get<FieldDetail>(`/api/repos/${seg(repo)}/fields/${seg(id)}`, undefined, signal),
  impact: (repo: string, id: string, depth = 5, signal?: AbortSignal) =>
    get<ImpactResponse>(`/api/repos/${seg(repo)}/impact/${seg(id)}`, { depth }, signal),
  story: (repo: string, id: string, signal?: AbortSignal) =>
    get<StoryResponse>(`/api/repos/${seg(repo)}/story/${seg(id)}`, undefined, signal),
  path: (repo: string, source: string, target: string, direction: Direction = "down", signal?: AbortSignal) =>
    get<PathResponse>(`/api/repos/${seg(repo)}/path`, { source, target, direction }, signal),
  datamap: (repo: string, fn: string, scope?: "class" | "file", signal?: AbortSignal) =>
    get<DataMapResponse>(`/api/repos/${seg(repo)}/datamap`, { fn, scope }, signal),
  classes: (repo: string, q?: string, signal?: AbortSignal) =>
    get<Rollup[]>(`/api/repos/${seg(repo)}/classes`, { q }, signal),
  classSummary: (repo: string, name: string, signal?: AbortSignal) =>
    get<RollupDetail>(`/api/repos/${seg(repo)}/classes/${segPath(name)}`, undefined, signal),
  files: (repo: string, q?: string, signal?: AbortSignal) =>
    get<Rollup[]>(`/api/repos/${seg(repo)}/files`, { q }, signal),
  fileSummary: (repo: string, path: string, signal?: AbortSignal) =>
    get<RollupDetail>(`/api/repos/${seg(repo)}/files/${segPath(path)}`, undefined, signal),
};

/** One plain sentence: what failed and what to try. */
export function plainError(e: unknown, what: string): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return `Couldn't reach the server while loading ${what}. Start it with "make serve" and try again.`;
    if (e.status === 404) return e.detail.endsWith(".") ? e.detail : `${e.detail}.`;
    if (e.status === 422) return `Couldn't load ${what} because the request wasn't valid. Try picking it again.`;
    if (e.status >= 500) return `The server had a problem while loading ${what}. Try again in a moment.`;
    return `Couldn't load ${what} (${e.detail}). Try again.`;
  }
  return `Couldn't load ${what}. Try again.`;
}
