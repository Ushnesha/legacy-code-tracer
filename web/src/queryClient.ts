import { QueryClient } from "@tanstack/react-query";

// One shared client, so code outside React (the tree store) can use the same cache.
export const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });
