// Regenerates src/api-types.ts from the FastAPI server's OpenAPI schema.
//   pnpm gen:api     (or: make api-types)
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const root = path.dirname(web);

const schema = execFileSync(
  "uv",
  ["run", "python", "-c", "import json; from server.main import app; print(json.dumps(app.openapi(), indent=2))"],
  { cwd: root, encoding: "utf8" },
);
writeFileSync(path.join(web, "openapi.json"), schema);
execFileSync("pnpm", ["exec", "openapi-typescript", "openapi.json", "-o", "src/api-types.ts"], {
  cwd: web,
  stdio: "inherit",
});
