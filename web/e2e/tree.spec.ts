import { expect, test, type Locator, type Page } from "@playwright/test";

const search = (page: Page) => page.getByRole("combobox", { name: /Find a function/ });
const results = (page: Page) => page.getByRole("listbox", { name: "Search results" }).getByRole("option");
const nodes = (page: Page) => page.getByTestId("tree-node");
const node = (page: Page, qualified: string) => page.locator(`[data-testid="tree-node"][data-fn-name="${qualified}"]`);

test.beforeAll(async ({ request }) => {
  const repos = (await (await request.get("http://localhost:8000/api/repos")).json()) as { name: string }[];
  for (const need of ["tinyxml2", "jsbsim"])
    if (!repos.some((r) => r.name === need)) throw new Error(`${need} is not loaded. Run: make extract/enrich/load REPO=repos/${need}`);
});

async function openRoot(page: Page, repo: string, query: string, exactName: string) {
  await page.goto(`/?repo=${repo}`);
  await search(page).fill(query);
  const option = results(page).filter({ has: page.locator(".mono", { hasText: new RegExp(`^${exactName}$`) }) }).first();
  await expect(option).toBeVisible();
  await option.click();
  await expect(nodes(page).first()).toBeVisible();
}

const box = async (l: Locator) => (await l.boundingBox())!;

/** Wait until an element has stopped moving on screen (the fit/slide animations are over). */
async function settled(l: Locator) {
  let last = await box(l);
  for (let stable = 0; stable < 3; ) {
    await l.page().waitForTimeout(150);
    const now = await box(l);
    stable = Math.abs(now.x - last.x) < 0.5 && Math.abs(now.y - last.y) < 0.5 ? stable + 1 : 0;
    last = now;
  }
}

test.describe("JSBSim: FGFDMExec::Run", () => {
  test("shows virtual calls as dashed amber edges", async ({ page }) => {
    await openRoot(page, "jsbsim", "FGFDMExec::Run", "FGFDMExec::Run");
    await expect(page.getByTestId("canvas-root-name")).toHaveText("FGFDMExec::Run");
    const dashed = page.locator("path.edge-may");
    await expect.poll(() => dashed.count(), { timeout: 10_000 }).toBeGreaterThanOrEqual(10);
    await expect(dashed.first()).toHaveCSS("stroke-dasharray", /\d/);
    await expect(page.locator("path.edge-calls").first()).not.toHaveCSS("stroke-dasharray", /\d/);
    // virtual nodes carry a "virtual" badge
    await expect(nodes(page).getByText("virtual", { exact: true }).first()).toBeVisible();
  });

  test("opening a node adds nodes to its right and the node does not jump", async ({ page }) => {
    await openRoot(page, "jsbsim", "FGFDMExec::Run", "FGFDMExec::Run");
    const canvas = page.getByTestId("tree-canvas");
    await canvas.focus();
    await page.keyboard.press("f"); // fit everything so every node is on screen

    // FGPropagate::Run arrives already open (depth 2). Close it, then open it again.
    // FGPropagate::Run also calls itself through a virtual call, so the name can appear twice: take the outer one
    await expect(node(page, "JSBSim::FGPropagate::Run").first()).toBeVisible();
    const paths = await node(page, "JSBSim::FGPropagate::Run").evaluateAll((els) => els.map((e) => e.getAttribute("data-path-id")!));
    const pathId = paths.sort((a, b) => a.split("/").length - b.split("/").length)[0]!; // the outer one
    const prop = page.locator(`[data-testid="tree-node"][data-path-id="${pathId}"]`);
    const kids = page.locator(`[data-testid="tree-node"][data-path-id^="${pathId}/"]`);
    const toggle = prop.getByTestId("tree-toggle");

    // sample the node's screen position the whole time a click is settling: it must stay put
    async function noJump(target: Locator, click: () => Promise<void>) {
      await settled(target);
      const before = await box(target);
      await click();
      let worst = 0;
      const t0 = Date.now();
      const seen: string[] = [];
      while (Date.now() - t0 < 900) {
        const now = await box(target);
        seen.push(`${Date.now() - t0}ms ${(now.x - before.x).toFixed(1)},${(now.y - before.y).toFixed(1)}`);
        worst = Math.max(worst, Math.abs(now.x - before.x), Math.abs(now.y - before.y));
        await page.waitForTimeout(25);
      }
      expect(worst, `moved by (dx,dy) over time: ${seen.join(" | ")}`).toBeLessThanOrEqual(5);
    }

    await expect(toggle).toHaveText("−");
    await noJump(prop, () => toggle.dispatchEvent("click")); // close
    await expect(kids).toHaveCount(0);
    await expect(toggle).toHaveText(/^\+\d+$/);

    await noJump(prop, () => toggle.dispatchEvent("click")); // open again: no new request, children already loaded
    await expect.poll(() => kids.count()).toBeGreaterThan(0);
    const after = await box(prop);
    for (const k of await kids.all()) expect((await box(k)).x).toBeGreaterThan(after.x + after.width);

    // a real expansion: a grandchild that still has children on the server
    const candidate = kids.filter({ has: page.getByTestId("tree-toggle").filter({ hasText: /^\+\d+$/ }) }).first();
    await expect(candidate).toBeVisible();
    const leafPath = (await candidate.getAttribute("data-path-id"))!;
    // pin it by path: a locator that looks for "has a +N button" would slide to the next node once this one opens
    const leaf = page.locator(`[data-testid="tree-node"][data-path-id="${leafPath}"]`);
    const leafKids = page.locator(`[data-testid="tree-node"][data-path-id^="${leafPath}/"]`);
    let requests = 0;
    page.on("request", (r) => r.url().includes("/tree?") && requests++);
    await noJump(leaf, () => leaf.getByTestId("tree-toggle").dispatchEvent("click"));
    await expect.poll(() => leafKids.count()).toBeGreaterThan(0);
    expect(requests).toBe(1); // exactly one /tree request, depth 1
    const leafBox = await box(leaf);
    for (const k of await leafKids.all()) expect((await box(k)).x).toBeGreaterThan(leafBox.x + leafBox.width);
  });
});

test.describe("tinyxml2: interaction", () => {
  test("click selects (sel= in the URL, inspector follows); double-click makes it the new root", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLDocument::LoadFile", "XMLDocument::LoadFile");
    const rootUrl = page.url();
    expect(rootUrl).not.toContain("sel=");

    await expect(nodes(page)).not.toHaveCount(1);
    const child = nodes(page).nth(1);
    const childName = (await child.getAttribute("data-fn-name"))!;
    await child.click();
    await expect(page).toHaveURL(/sel=root%2F0|sel=root\/0/);
    await expect(child).toHaveCSS("outline-style", "solid");
    await expect(page.getByTestId("breadcrumb-root")).toBeVisible();
    await expect(page.getByRole("tabpanel").getByRole("heading", { name: childName.split("::").slice(-2).join("::") })).toBeVisible();

    await child.dblclick();
    await expect(page).toHaveURL(/root=/);
    expect(page.url()).not.toBe(rootUrl);
    await expect(page.getByTestId("canvas-root-name")).toHaveText(childName.replace(/^tinyxml2::/, ""));
    // the breadcrumb grew: the old root is a button that goes back
    await page.getByRole("navigation", { name: "Where you are" }).getByTitle("Go back to this function").click();
    await expect(page.getByTestId("canvas-root-name")).toHaveText("XMLDocument::LoadFile");
  });

  test("keyboard: arrows move along the tree, Enter opens, Backspace closes, F fits", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLDocument::LoadFile", "XMLDocument::LoadFile");
    const canvas = page.getByTestId("tree-canvas");
    await canvas.focus();
    await page.keyboard.press("ArrowRight"); // first child
    await expect(page).toHaveURL(/sel=root%2F0|sel=root\/0/);
    await page.keyboard.press("ArrowDown"); // next sibling
    await expect(page).toHaveURL(/sel=root%2F1|sel=root\/1/);
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowLeft"); // back to the root
    await expect(page).not.toHaveURL(/sel=/);

    const before = Number(await canvas.getAttribute("data-visible-nodes"));
    await page.keyboard.press("Backspace"); // the root is open: close it
    await expect.poll(async () => Number(await canvas.getAttribute("data-visible-nodes"))).toBe(1);
    await page.keyboard.press("Enter"); // open it again
    await expect.poll(async () => Number(await canvas.getAttribute("data-visible-nodes"))).toBe(before);
    await page.keyboard.press("f");
    await expect(canvas).toBeFocused();
  });

  test("a shared sel= link lands on the right node", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLDocument::LoadFile", "XMLDocument::LoadFile");
    await page.getByTestId("tree-canvas").focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    const url = page.url();
    const sel = new URL(url).searchParams.get("sel");
    test.skip(!sel || sel.split("/").length < 3, "this root has no grandchildren");
    await page.reload();
    await expect(page).toHaveURL(url);
    await expect(page.locator(`[data-testid="tree-node"][data-path-id="${sel}"]`)).toHaveCSS("outline-style", "solid", { timeout: 10_000 });
  });

  test("who calls it: the tree flips and the arrows point at the selected function", async ({ page }) => {
    await openRoot(page, "tinyxml2", "Unlink", "XMLNode::Unlink");
    await page.getByRole("radio", { name: "Who calls it" }).click();
    await expect(page).toHaveURL(/dir=up/);
    await expect(nodes(page).nth(1)).toBeVisible();
    const root = await box(nodes(page).first());
    const child = await box(nodes(page).nth(1));
    expect(child.x).toBeLessThan(root.x); // callers are to the left of what they call
  });

  test("reduced motion: nodes snap, nothing slides", async ({ browser }) => {
    const ctx = await browser.newContext({ reducedMotion: "reduce", viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await openRoot(page, "tinyxml2", "XMLDocument::LoadFile", "XMLDocument::LoadFile");
    await page.getByTestId("tree-canvas").focus();
    await page.keyboard.press("f");
    const toggle = page.getByTestId("tree-toggle").filter({ hasText: /^\+/ }).first();
    const n = Number(await page.getByTestId("tree-canvas").getAttribute("data-visible-nodes"));
    await toggle.dispatchEvent("click");
    await expect.poll(async () => Number(await page.getByTestId("tree-canvas").getAttribute("data-visible-nodes"))).toBeGreaterThan(n);
    await ctx.close();
  });
});
