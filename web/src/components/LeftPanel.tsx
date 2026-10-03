import { useStore } from "../store";
import { EntryPoints } from "./EntryPoints";
import { Legend } from "./Legend";
import { RepoPicker } from "./RepoPicker";
import { Search } from "./Search";

export function LeftPanel() {
  const repo = useStore((s) => s.repo);
  return (
    <div className="h-full flex flex-col min-h-0">
      {repo ? (
        <>
          <Search repo={repo} />
          <EntryPoints repo={repo} />
        </>
      ) : (
        <RepoPicker />
      )}
      <Legend />
    </div>
  );
}
