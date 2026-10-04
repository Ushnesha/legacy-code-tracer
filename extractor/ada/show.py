"""Debug helper: print the semantic edges of a graph JSON."""

import json
import sys

with open(sys.argv[1]) as fh:
    d = json.load(fh)
n = {
    x["id"]: f"{x['kind'][:3]}:{x['qualified_name']} {(x['signature'] or '')[:40]}"
    for x in d["nodes"]
}
for e in d["edges"]:
    if e["type"] in ("CALLS", "MAY_CALL", "UNRESOLVED_CALL", "READS", "WRITES"):
        print(
            e["type"],
            "|",
            n[e["source"]],
            "->",
            n[e["target"]],
            {k: v for k, v in e["properties"].items() if k not in ("language", "candidates")},
        )
print([x["qualified_name"] for x in d["nodes"] if x["properties"].get("entry_point")])
