import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

const SHOTS = "../docs/screenshots";
mkdirSync(SHOTS, { recursive: true });

const search = (page: Page) => page.getByRole("combobox", { name: /Find a function/ });
const nodes = (page: Page) => page.getByTestId("tree-node");
const node = (page: Page, qualified: string) => page.locator(`[data-testid="tree-node"][data-fn-name="${qualified}"]`).first();
const text = (page: Page) => page.getByTestId("tour-text");
const settle = (page: Page) => page.waitForTimeout(900); // let the slide animation and fits finish before a screenshot

test.beforeAll(async ({ request }) => {
  const repos = (await (await request.get("http://localhost:8000/api/repos")).json()) as { name: string }[];
  if (!repos.some((r) => r.name === "jsbsim")) throw new Error("jsbsim is not loaded. Run: make demo REPO=jsbsim");
});

test.describe("Landing screen", () => {
  test("shows the repo, its counts and five places to start", async ({ page, request }) => {
    const repos = (await (await request.get("http://localhost:8000/api/repos")).json()) as { name: string; commit_sha: string; stats: { nodes: Record<string, number>; edges: Record<string, number> } }[];
    const jsb = repos.find((r) => r.name === "jsbsim")!;
    await page.goto("/?repo=jsbsim");
    const landing = page.getByTestId("landing");
    await expect(landing.getByRole("heading", { name: "jsbsim", level: 1 })).toBeVisible();
    await expect(landing).toContainText(`commit ${jsb.commit_sha.slice(0, 7)}`);

    const counts = page.getByTestId("landing-counts");
    const n = (v: number) => v.toLocaleString("en-US");
    await expect(counts.locator("div", { has: page.getByText("functions", { exact: true }) }).first()).toContainText(n(jsb.stats.nodes.function!));
    await expect(counts.locator("div", { has: page.getByText("call links", { exact: true }) }).first()).toContainText(n(jsb.stats.edges.calls!));
    await expect(counts.locator("div", { has: page.getByText("data links", { exact: true }) }).first()).toContainText(n(jsb.stats.edges.reads! + jsb.stats.edges.writes!));
    await expect(counts.locator("div", { has: page.getByText("virtual links", { exact: true }) }).first()).toContainText(n(jsb.stats.edges.may_call!));

    const cards = page.getByTestId("landing-cards").locator("li");
    await expect(cards).toHaveCount(5);
    for (const card of await cards.all()) {
      await expect(card.getByRole("button", { name: /^Trace / })).toBeVisible();
      await expect(card.locator("span.line-clamp-2")).not.toBeEmpty(); // a summary, or "No explanation yet."
    }
    await expect(landing).not.toContainText(/revolutionary|powerful|seamless|welcome/i);
    await page.screenshot({ path: `${SHOTS}/landing.png` });

    await cards.first().getByRole("button", { name: /^Trace / }).click();
    await expect(page).toHaveURL(/root=/);
    await expect(nodes(page).first()).toBeVisible();
    await expect(page.getByTestId("landing")).toHaveCount(0);
  });

  test("the landing screen comes back when the root is cleared", async ({ page }) => {
    await page.goto("/?repo=tinyxml2");
    await expect(page.getByTestId("landing")).toBeVisible();
  });
});

test.describe("Offline mode", () => {
  test("a banner says only saved explanations are shown", async ({ page }) => {
    await page.route("**/api/status", (route) => route.fulfill({ json: { offline: true } }));
    await page.goto("/?repo=tinyxml2");
    await expect(page.getByText("Offline: saved explanations only")).toBeVisible();
  });
  test("there is no banner when online", async ({ page }) => {
    await page.route("**/api/status", (route) => route.fulfill({ json: { offline: false } }));
    await page.goto("/?repo=tinyxml2");
    await expect(page.getByRole("button", { name: "Tour" })).toBeVisible();
    await expect(page.getByText("Offline: saved explanations only")).toHaveCount(0);
  });
});

test.describe("Guided tour", () => {
  test("five steps, each one sentence, each setting the URL state", async ({ page }) => {
    await page.goto("/?repo=jsbsim");
    await expect(page.getByTestId("landing")).toBeVisible();
    await page.getByTestId("tour-button").click();
    const card = page.getByTestId("tour-card");

    // 1. FGFDMExec::Run
    await expect(card).toContainText("Step 1 of 5");
    await expect(text(page)).toHaveText("This runs once for every step of the flight. It moves the clock on, then gives each part of the aircraft model its turn.");
    await expect(page).toHaveURL(/root=[0-9a-f]{12}/);
    await expect(page.getByTestId("canvas-root-name")).toHaveText("FGFDMExec::Run");
    await expect(nodes(page).nth(1)).toBeVisible();
    await settle(page);
    await page.screenshot({ path: `${SHOTS}/tour-1.png` });
    const root = new URL(page.url()).searchParams.get("root");

    // 2. the dashed fan-out
    await card.getByTestId("tour-next").click();
    await expect(card).toContainText("Step 2 of 5");
    await expect(text(page)).toContainText("Each dashed line is a virtual call.");
    await expect(page).toHaveURL(/sel=root%2F\d+|sel=root\/\d+/);
    await expect.poll(() => page.locator("path.edge-may").count()).toBeGreaterThanOrEqual(10);
    await expect(page.locator("path.edge-may").first()).toHaveCSS("stroke-dasharray", /\d/);
    const selected = new URL(page.url()).searchParams.get("sel")!;
    await expect(page.locator(`[data-testid="tree-node"][data-path-id="${selected}"]`)).toHaveCSS("outline-style", "solid");
    await settle(page);
    await page.screenshot({ path: `${SHOTS}/tour-2.png` });

    // 3. FGPropagate::Run, opened and selected
    await card.getByTestId("tour-next").click();
    await expect(card).toContainText("Step 3 of 5");
    await expect(text(page)).toHaveText("This is where the aircraft's position, speed and orientation are updated.");
    const prop = node(page, "JSBSim::FGPropagate::Run");
    await expect(prop).toHaveCSS("outline-style", "solid");
    await expect(prop.getByTestId("tree-toggle")).toHaveText("−"); // open
    const propPath = (await prop.getAttribute("data-path-id"))!;
    await expect(page).toHaveURL(new RegExp(`sel=${encodeURIComponent(propPath)}|sel=${propPath}`));
    await expect(page.locator(`[data-testid="tree-node"][data-path-id^="${propPath}/"]`).first()).toBeVisible(); // its children
    await expect(page.getByRole("tabpanel").getByRole("heading", { name: /FGPropagate::Run/ })).toBeVisible();
    await settle(page);
    await page.screenshot({ path: `${SHOTS}/tour-3.png` });

    // 4. the data lens on the aircraft's position
    await card.getByTestId("tour-next").click();
    await expect(card).toContainText("Step 4 of 5");
    await expect(text(page)).toContainText("Everything in red changes this value");
    await expect(page).toHaveURL(/field=/);
    await expect(page.getByTestId("lens-card")).toContainText("vLocation");
    await expect(page.getByTestId("lens-card")).toContainText(/written by \d+/);
    await expect(node(page, "JSBSim::FGPropagate::Run")).toHaveAttribute("data-lens", "write");
    // several of the writers are inside this tree (some may be scrolled out of view, so read the card, not the screen)
    const cardText = (await page.getByTestId("lens-card").textContent())!;
    const writers = Number(/written by (\d+)/.exec(cardText)![1]);
    const missing = Number(/(\d+) writers? (?:is|are) not in this tree/.exec(cardText)?.[1] ?? 0);
    expect(writers - missing).toBeGreaterThanOrEqual(2);
    await expect(page.locator('[data-testid="tree-node"][data-lens="dim"]').first()).toHaveCSS("opacity", "0.3");
    await expect(page.getByRole("tab", { name: "Data" })).toHaveAttribute("aria-selected", "true");
    await settle(page);
    await page.screenshot({ path: `${SHOTS}/tour-4.png` });

    // 5. impact of a low-level helper
    await card.getByTestId("tour-next").click();
    await expect(card).toContainText("Step 5 of 5");
    await expect(text(page)).toHaveText("Change this one function and here is everything that could be affected.");
    await expect(page).not.toHaveURL(/field=/);
    expect(new URL(page.url()).searchParams.get("root")).not.toBe(root);
    await expect(page.getByTestId("canvas-root-name")).toHaveText("FGPropagate::GetAltitudeASL");
    await expect(page.getByRole("tab", { name: "Impact" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText(/Up to \d+ functions could be affected/)).toBeVisible();
    await settle(page);
    await page.screenshot({ path: `${SHOTS}/tour-5.png` });
    await expect(card.getByTestId("tour-finish")).toBeVisible();
    await card.getByTestId("tour-finish").click();
    await expect(card).toHaveCount(0);
  });

  test("Back goes to the step before, and Esc closes the tour", async ({ page }) => {
    await page.goto("/?repo=jsbsim");
    await page.getByTestId("tour-button").click();
    await expect(page.getByTestId("tour-card")).toContainText("Step 1 of 5");
    await page.getByTestId("tour-next").click();
    await expect(page.getByTestId("tour-card")).toContainText("Step 2 of 5");
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.getByTestId("tour-card")).toContainText("Step 1 of 5");
    await expect(page).not.toHaveURL(/sel=/);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("tour-card")).toHaveCount(0);
  });

  test("the tour is only offered where its repo is loaded", async ({ page }) => {
    const all = (await (await page.request.get("http://localhost:8000/api/repos")).json()) as { name: string }[];
    await page.route("**/api/repos", (route) => route.fulfill({ json: all.filter((r) => r.name !== "jsbsim") }));
    await page.goto("/?repo=tinyxml2");
    await expect(page.getByTestId("landing")).toBeVisible();
    await expect(page.getByTestId("tour-button")).toHaveCount(0);
  });
});
