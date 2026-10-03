import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ApiError, api, type Direction } from "./api";

// Server answers do not change while you look at them, so keep them for a minute.
const STALE = 60_000;
const noRetryOn404 = (count: number, e: unknown) => !(e instanceof ApiError && e.status < 500 && e.status !== 0) && count < 1;

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: ({ signal }) => api.status(signal), staleTime: STALE, retry: noRetryOn404 });

export const useRepos = () =>
  useQuery({ queryKey: ["repos"], queryFn: ({ signal }) => api.repos(signal), staleTime: STALE, retry: noRetryOn404 });

export const useEntryPoints = (repo: string | null) =>
  useQuery({
    queryKey: ["entry-points", repo],
    queryFn: ({ signal }) => api.entryPoints(repo!, 100, signal),
    enabled: !!repo,
    staleTime: STALE,
    retry: noRetryOn404,
  });

export const useSearch = (repo: string | null, q: string) =>
  useQuery({
    queryKey: ["search", repo, q],
    queryFn: ({ signal }) => api.search(repo!, q, 20, signal),
    enabled: !!repo && q.trim().length >= 2,
    staleTime: STALE,
    placeholderData: keepPreviousData,
    retry: noRetryOn404,
  });

export const useFunction = (repo: string | null, id: string | null) =>
  useQuery({
    queryKey: ["function", repo, id],
    queryFn: ({ signal }) => api.fn(repo!, id!, signal),
    enabled: !!repo && !!id,
    staleTime: STALE,
    retry: noRetryOn404,
  });

export const useField = (repo: string | null, id: string | null) =>
  useQuery({
    queryKey: ["field", repo, id],
    queryFn: ({ signal }) => api.field(repo!, id!, signal),
    enabled: !!repo && !!id,
    staleTime: STALE,
    retry: noRetryOn404,
  });

export const useImpact = (repo: string | null, id: string | null, depth = 5) =>
  useQuery({
    queryKey: ["impact", repo, id, depth],
    queryFn: ({ signal }) => api.impact(repo!, id!, depth, signal),
    enabled: !!repo && !!id,
    staleTime: STALE,
    retry: noRetryOn404,
  });

export const useTree = (
  repo: string | null,
  root: string | null,
  o: { direction: Direction; depth: number; includeNoise: boolean; includeMayCall: boolean },
) =>
  useQuery({
    queryKey: ["tree", repo, root, o],
    queryFn: ({ signal }) => api.tree(repo!, root!, o, signal),
    enabled: !!repo && !!root,
    staleTime: STALE,
    placeholderData: keepPreviousData,
    retry: noRetryOn404,
  });

export const useStory = (repo: string | null, id: string | null) =>
  useQuery({
    queryKey: ["story", repo, id],
    queryFn: ({ signal }) => api.story(repo!, id!, signal),
    enabled: !!repo && !!id,
    staleTime: STALE,
    retry: false,
  });

export const useDatamap = (repo: string | null, fn: string | null, scope?: "class" | "file") =>
  useQuery({
    queryKey: ["datamap", repo, fn, scope],
    queryFn: ({ signal }) => api.datamap(repo!, fn!, scope, signal),
    enabled: !!repo && !!fn,
    staleTime: STALE,
    placeholderData: keepPreviousData,
    retry: noRetryOn404,
  });
