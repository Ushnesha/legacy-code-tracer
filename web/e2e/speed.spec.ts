import { expect, test, type Page } from "@playwright/test";

// The Playwright trace is kept for every run of this file, so the frames can be inspected afterwards:
//   pnpm exec playwright show-trace test-results/<this test>/trace.zip
test.use({ trace: "on" });

const search = (page: Page) => page.getByRole("combobox", { name: /Find a function/ });
const results = (page: Page) => page.getByRole("listbox", { name: "Search results" }).getByRole("option");
const nodes = (page: Page) => page.getByTestId("tree-node");

test.beforeAll(async ({ request }) => {
  const repos = (await (await request.get("http://localhost:8000/api/repos")).json()) as { name: string }[];
  if (!repos.some((r) => r.name === "jsbsim")) throw new Error("jsbsim is not loaded. Run: make extract/enrich/load REPO=repos/jsbsim");
});

async function openRoot(page: Page, repo: string, query: string, exactName: string) {
  await page.goto(`/?repo=${repo}`);
  await search(page).fill(query);
  const option = results(page).filter({ has: page.locator(".mono", { hasText: new RegExp(`^${exactName}$`) }) }).first();
  await expect(option).toBeVisible();
  await option.click();
  await expect(nodes(page).first()).toBeVisible();
}

test("big trees stay smooth: 300 visible nodes, no frame over 100 ms", async ({ page }, info) => {
  await openRoot(page, "jsbsim", "FGFDMExec::Run", "FGFDMExec::Run");
  const canvas = page.getByTestId("tree-canvas");
  const visibleCount = async () => Number(await canvas.getAttribute("data-visible-nodes"));

  // open nodes one at a time until 300 are visible
  await canvas.focus();
  for (let i = 0; i < 80 && (await visibleCount()) < 300; i++) {
    const before = await visibleCount();
    await page.keyboard.press("f"); // fit all, so every node (and its +N button) is on screen
    await page.waitForTimeout(150);
    await nodes(page).getByTestId("tree-toggle").filter({ hasText: /^\+/ }).first().dispatchEvent("click");
    await expect.poll(visibleCount, { timeout: 10_000 }).toBeGreaterThan(before);
  }
  expect(await visibleCount()).toBeGreaterThanOrEqual(300);

  // record every animation frame while we pan, zoom and open one more node
  await page.evaluate(() => {
    const w = window as unknown as { __frames: number[]; __phase: string; __phases: string[] };
    w.__frames = [];
    w.__phases = [];
    w.__phase = "start";
    let last = performance.now();
    const loop = (now: number) => {
      w.__phases.push(w.__phase);
      w.__frames.push(now - last);
      last = now;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
  const area = (await canvas.boundingBox())!;
  const cx = area.x + area.width / 2;
  const cy = area.y + area.height / 2;
  const phase = (p: string) => page.evaluate((x) => ((window as unknown as { __phase: string }).__phase = x), p);
  await phase("pan");
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  for (let i = 1; i <= 25; i++) await page.mouse.move(cx - i * 12, cy + i * 6);
  await page.mouse.up();
  await phase("zoom");
  await page.mouse.wheel(0, -300);
  await page.waitForTimeout(300);
  await phase("expand");
  const before = await visibleCount();
  await canvas.focus();
  await phase("fit");
  await page.keyboard.press("f");
  await page.waitForTimeout(150);
  await phase("expand");
  await nodes(page).getByTestId("tree-toggle").filter({ hasText: /^\+/ }).first().dispatchEvent("click");
  await expect.poll(visibleCount, { timeout: 10_000 }).toBeGreaterThan(before);
  await page.waitForTimeout(600);

  const frames = await page.evaluate(() => (window as unknown as { __frames: number[] }).__frames.slice(1));
  const phases = await page.evaluate(() => (window as unknown as { __phases: string[] }).__phases.slice(1));
  const worst = Math.max(...frames);
  const slow = frames.map((f, i) => ({ f, p: phases[i] })).filter((x) => x.f > 50).map((x) => `${x.p}:${x.f.toFixed(0)}ms`);
  console.log("slow frames:", slow.join(" "));
  const sorted = [...frames].sort((a, b) => a - b);
  const p95 = sorted[Math.floor(sorted.length * 0.95)]!;
  info.annotations.push({ type: "frames", description: `${frames.length} frames, p95 ${p95.toFixed(1)} ms, worst ${worst.toFixed(1)} ms, ${await visibleCount()} nodes` });
  console.log(`frames: ${info.annotations.at(-1)!.description}`);
  expect(worst).toBeLessThan(100);

  // ELK, inside the worker, for the big layouts this test made
  const stats = await page.evaluate(() => (window as unknown as { __layoutStats: { nodes: number; ms: number }[] }).__layoutStats);
  const big = stats.filter((s) => s.nodes >= 250);
  console.log("layouts:", stats.map((x) => `${x.nodes}:${x.ms.toFixed(0)}ms`).join(" "));
  expect(big.length).toBeGreaterThan(0);
  for (const s of big) expect(s.ms, `${s.nodes} nodes`).toBeLessThan(500);
});

test("the first tree appears in under a second", async ({ page }) => {
  await page.goto("/?repo=jsbsim");
  await expect(search(page)).toBeVisible();
  await page.waitForTimeout(1500); // the page has settled; the layout worker has warmed up
  await search(page).fill("FGFDMExec::Run");
  const option = results(page).filter({ has: page.locator(".mono", { hasText: /^FGFDMExec::Run$/ }) }).first();
  await expect(option).toBeVisible();
  const t0 = Date.now();
  await option.click();
  await expect(nodes(page).nth(1)).toBeVisible(); // the root and its children are on screen
  const ms = Date.now() - t0;
  console.log(`first tree on screen after ${ms} ms`);
  expect(ms).toBeLessThan(1000);
});
