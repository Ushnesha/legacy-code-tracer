import { expect, test, type Page, type Route } from "@playwright/test";

const search = (page: Page) => page.getByRole("combobox", { name: /Find a function/ });
const results = (page: Page) => page.getByRole("listbox", { name: "Search results" }).getByRole("option");
const nodes = (page: Page) => page.getByTestId("tree-node");
const node = (page: Page, qualified: string) => page.locator(`[data-testid="tree-node"][data-fn-name="${qualified}"]`).first();

test.beforeAll(async ({ request }) => {
  const repos = (await (await request.get("http://localhost:8000/api/repos")).json()) as { name: string }[];
  for (const need of ["tinyxml2", "jsbsim"])
    if (!repos.some((r) => r.name === need)) throw new Error(`${need} is not loaded. Run: make extract/enrich/load REPO=repos/${need}`);
});

async function openRoot(page: Page, repo: string, query: string, exactName: string, wait = true) {
  await page.goto(`/?repo=${repo}`);
  await search(page).fill(query);
  const option = results(page).filter({ has: page.locator(".mono", { hasText: new RegExp(`^${exactName}$`) }) }).first();
  await expect(option).toBeVisible();
  await option.click();
  if (wait) await expect(nodes(page).first()).toBeVisible();
}

/** Show the real function answer with a different summary, as if the explain step had run. */
async function withSummary(page: Page, summary: Record<string, unknown>) {
  await page.route("**/api/repos/*/functions/*", async (route: Route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.summary = { created_at: null, mentions: [], side_effects: [], reasons: [], ...summary };
    await route.fulfill({ response: res, json: body });
  });
}

const WRITES = "rgb(194, 65, 12)"; // --writes in the light theme
const READS = "rgb(31, 122, 140)"; // --reads
const VIOLET = "rgb(124, 58, 237)"; // --unverified

test.describe("Explain tab", () => {
  test("a checked explanation: big first line, three blocks, badge and footer", async ({ page }) => {
    await withSummary(page, {
      short: "Cuts a node out of its family tree.",
      detailed: "It detaches the node from its parent and its brothers and sisters.",
      inputs: "The node to remove.",
      outputs: "Nothing.",
      side_effects: ["_parent", "_next"],
      model: "claude-sonnet-5-5",
      verified: true,
    });
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByText("Cuts a node out of its family tree.")).toHaveCSS("font-size", "18px");
    await expect(panel.getByText("It detaches the node")).toBeVisible();
    for (const label of ["Takes in", "Gives back", "Changes"]) await expect(panel.getByRole("heading", { name: label, exact: true })).toBeVisible();
    await expect(panel.getByTestId("badge-verified")).toHaveText("Checked against the code");
    await expect(panel.getByTestId("explain-footer")).toHaveText("Explained by claude-sonnet-5-5. AI text can be wrong — check the Code tab.");
  });

  test("an unchecked explanation is violet and says what is wrong in plain words", async ({ page }) => {
    await withSummary(page, {
      short: "Does something with ghost_fn.",
      detailed: "It calls ghost_fn.",
      inputs: "x",
      outputs: "y",
      side_effects: [],
      model: "claude-sonnet-5-5",
      verified: false,
      reasons: ["mentions 'ghost_fn', which is not a callee, caller or field this function touches", "mentions 'nothing_here', which does not exist in the code graph"],
    });
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    const badge = page.getByTestId("badge-unverified");
    await expect(badge).toContainText("Not fully checked — mentions ghost_fn, which isn't connected here");
    await expect(badge).toContainText("mentions nothing_here, which isn't anywhere in this code");
    await expect(badge.locator("p").first()).toHaveCSS("color", VIOLET);
    await expect(page.getByRole("tabpanel").getByText("Does something with ghost_fn.")).toHaveCSS("color", VIOLET);
    await expect(page.getByTestId("badge-verified")).toHaveCount(0);
  });

  test("an entry point shows 'What happens when this runs', and each name selects its node in the tree", async ({ page }) => {
    await page.goto("/?repo=tinyxml2");
    await page.getByRole("button", { name: /^main\b.*xmltest\.cpp/ }).click();
    await expect(nodes(page).nth(1)).toBeVisible();
    // a child of main whose name is not shared with an overload elsewhere in the tree
    const names = await nodes(page).evaluateAll((els) => els.slice(1).map((e) => e.getAttribute("data-fn-name")!));
    const target = names.find((n) => names.filter((m) => m === n).length === 1)!;
    await page.route("**/api/repos/*/story/*", (route) =>
      route.fulfill({
        json: {
          function: {},
          story: "",
          steps: [`It starts by calling ${target} to get going.`, "Then it tidies up, which needs no function."],
          model: "claude-sonnet-5-5",
          created_at: null,
        },
      }),
    );
    await page.reload(); // the story is fetched when the explanation opens
    await expect(nodes(page).nth(1)).toBeVisible();
    const story = page.getByTestId("story");
    await expect(page.getByRole("heading", { name: "What happens when this runs" })).toBeVisible();
    await expect(story.locator("li")).toHaveCount(2);
    await expect(story.getByRole("button")).toHaveCount(1); // only the function name is a link; plain words stay plain
    await story.getByRole("button", { name: target }).click();
    await expect(page).toHaveURL(/sel=root%2F\d+|sel=root\/\d+/);
    await expect(node(page, target)).toHaveCSS("outline-style", "solid");
  });

  test("a function that is not an entry point has no story section", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await expect(page.getByRole("heading", { name: "What happens when this runs" })).toHaveCount(0);
  });
});

test.describe("Code tab", () => {
  test("gutter marks sit on the lines the API gives; a call mark selects the callee in the tree", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLDocument::LoadFile", "XMLDocument::LoadFile");
    const id = new URL(page.url()).searchParams.get("root")!;
    const api = await (await page.request.get(`http://localhost:8000/api/repos/tinyxml2/functions/${id}`)).json();
    const lineSet = (lists: { lines: number[] }[]) => [...new Set(lists.flatMap((l) => l.lines))].sort((a, b) => a - b);

    await page.getByRole("tab", { name: "Code" }).click();
    await expect(page.getByTestId("code")).toBeVisible();
    const dom = async (kind: string) =>
      (await page.getByTestId(`gutter-${kind}`).evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-line"))))).sort((a, b) => a - b);

    const apiCalls = lineSet([...api.callees.calls, ...api.callees.may_call]);
    expect(apiCalls.length).toBeGreaterThan(0);
    expect(await dom("call")).toEqual(apiCalls);
    expect(await dom("write")).toEqual(lineSet(api.fields_written));
    expect(await dom("read")).toEqual(lineSet(api.fields_read));
    // every mark is inside the function's own lines
    for (const l of apiCalls) expect(l).toBeGreaterThanOrEqual(api.source.start);

    await expect(page.getByRole("link", { name: "Open on GitHub" })).toHaveAttribute("href", /\/blob\/[0-9a-f]{40}\/tinyxml2\.cpp#L\d+-L\d+$/);

    await page.getByTestId("gutter-call").first().click();
    await expect(page).toHaveURL(/sel=root%2F\d+|sel=root\/\d+/);
  });

  test("lines that change data are red-orange, lines that read are teal", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("tab", { name: "Code" }).click();
    await expect(page.getByTestId("gutter-write").first()).toHaveCSS("color", WRITES);
    await expect(page.getByTestId("gutter-read").first()).toHaveCSS("color", READS);
    await page.getByTestId("gutter-write").first().click(); // a data mark turns the lens on
    await expect(page).toHaveURL(/field=/);
    await expect(page.getByRole("tab", { name: "Data" })).toHaveAttribute("aria-selected", "true");
  });

  test("the code is syntax highlighted in our palette", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("tab", { name: "Code" }).click();
    const palette = () =>
      page.getByTestId("code").locator("code span").evaluateAll((els) => [...new Set(els.map((e) => getComputedStyle(e).color))]);
    await expect.poll(async () => (await palette()).length, { timeout: 15_000 }).toBeGreaterThan(2); // keywords, types, comments... not one flat color
    const colors = await palette();
    expect(colors).not.toContain(WRITES); // the meaning colors are never used for syntax
    expect(colors).not.toContain(READS);
  });
});

test.describe("Data tab and the data lens", () => {
  test("Unlink changes _parent; clicking it rings Unlink in red-orange", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("tab", { name: "Data" }).click();
    const changes = page.getByRole("region", { name: "Changes" });
    await expect(changes.getByRole("button", { name: /_parent/ })).toBeVisible();
    await expect(changes.getByRole("button", { name: /_parent/ })).toContainText("XMLNode"); // its type and class
    await expect(page.getByRole("region", { name: "Reads" })).toBeVisible();

    await changes.getByRole("button", { name: /_parent/ }).click();
    await expect(page).toHaveURL(/field=/);
    const unlink = node(page, "tinyxml2::XMLNode::Unlink");
    await expect(unlink).toHaveAttribute("data-lens", "write");
    await expect(unlink.getByTestId("lens-ring")).toHaveCSS("border-top-color", WRITES);
    await expect(page.getByTestId("lens-card")).toContainText("_parent");
    await expect(page.getByTestId("lens-card")).toContainText("written by");
  });

  test("everything that is not a writer or reader fades to 30%, and Esc clears the lens", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::DeleteChild", "XMLNode::DeleteChild");
    await expect.poll(() => nodes(page).count()).toBeGreaterThan(2);
    await node(page, "tinyxml2::XMLNode::Unlink").click();
    await page.getByRole("tab", { name: "Data" }).click();
    await page.getByRole("region", { name: "Changes" }).getByRole("button", { name: /_parent/ }).click();
    await expect(node(page, "tinyxml2::XMLNode::Unlink")).toHaveAttribute("data-lens", "write");
    const dimmed = page.locator('[data-testid="tree-node"][data-lens="dim"]');
    await expect.poll(() => dimmed.count()).toBeGreaterThan(0);
    await expect(dimmed.first()).toHaveCSS("opacity", "0.3");

    await page.getByTestId("tree-canvas").focus();
    await page.keyboard.press("Escape");
    await expect(page).not.toHaveURL(/field=/);
    await expect(page.getByTestId("lens-card")).toHaveCount(0);
    await expect(page.locator("[data-lens]")).toHaveCount(0);
  });

  test("the card counts writers missing from the tree; 'Add them' adds what it can and lists the rest", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("tab", { name: "Data" }).click();
    await page.getByRole("region", { name: "Changes" }).getByRole("button", { name: /_parent/ }).click();
    const card = page.getByTestId("lens-card");
    await expect(card).toContainText(/\d+ writers are not in this tree\./);
    const before = Number(await page.getByTestId("tree-canvas").getAttribute("data-visible-nodes"));
    await card.getByRole("button", { name: "Add them" }).click();
    // Unlink calls nothing, so none of the other writers can be reached by going down: they are listed instead
    await expect(card).toContainText("can't be reached");
    await expect(card.getByRole("button", { name: /InsertEndChild|InsertFirstChild|InsertAfterChild/ }).first()).toBeVisible();
    expect(Number(await page.getByTestId("tree-canvas").getAttribute("data-visible-nodes"))).toBe(before);

    // going up from Unlink does reach them
    await page.getByRole("radio", { name: "Who calls it" }).click();
    await expect(card).toContainText(/\d+ writers? (is|are) not in this tree/);
    await card.getByRole("button", { name: "Add them" }).click();
    await expect.poll(async () => Number(await page.getByTestId("tree-canvas").getAttribute("data-visible-nodes"))).toBeGreaterThan(before);
  });

  test("the lens link survives a reload", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("tab", { name: "Data" }).click();
    await page.getByRole("region", { name: "Changes" }).getByRole("button", { name: /_next/ }).click();
    const url = page.url();
    await page.reload();
    await expect(page).toHaveURL(url);
    await expect(node(page, "tinyxml2::XMLNode::Unlink")).toHaveAttribute("data-lens", "write");
  });
});

test.describe("Impact tab", () => {
  test("groups callers by distance with counts; 'Show as tree' flips the canvas to who-calls-it", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("tab", { name: "Impact" }).click();
    await expect(page.getByRole("heading", { name: "If you change this, these could be affected" })).toBeVisible();
    await expect(page.getByText(/^Directly/)).toBeVisible();
    await expect(page.getByText(/2 steps away/)).toBeVisible();
    await expect(page.getByText(/Up to \d+ functions could be affected/)).toBeVisible();
    const root = new URL(page.url()).searchParams.get("root");
    await page.getByTestId("show-as-tree").click();
    await expect(page).toHaveURL(/dir=up/);
    expect(new URL(page.url()).searchParams.get("root")).toBe(root);
    await expect(page.getByRole("radio", { name: "Who calls it" })).toHaveAttribute("aria-checked", "true");
    await expect(nodes(page).nth(1)).toBeVisible();
  });
});

test.describe("Data map", () => {
  test("JSBSim FGPropagate: shared state gets a warning dot; a field turns on the lens", async ({ page }) => {
    await openRoot(page, "jsbsim", "FGPropagate::Run", "FGPropagate::Run");
    await page.getByRole("radio", { name: "Data", exact: true }).click();
    await expect(page).toHaveURL(/view=data/);
    const map = page.getByTestId("data-map");
    await expect(map).toBeVisible();
    await expect.poll(async () => Number(await map.getAttribute("data-fields"))).toBeGreaterThan(5);
    await expect(page.getByText(/changed by more than one function: shared state/)).toBeVisible();

    await page.getByTestId("show-shared").click();
    const dots = page.getByTestId("shared-dot");
    await expect.poll(() => dots.count()).toBeGreaterThanOrEqual(1);
    await expect(dots.first()).toHaveAccessibleName(/Shared state: \d+ different functions change this/);

    // red-orange edges for writes, teal for reads
    await expect(page.locator("path.edge-writes").first()).toHaveCSS("stroke", WRITES);
    await expect(page.locator("path.edge-reads").first()).toHaveCSS("stroke", READS);

    // clicking a field there turns on the same lens
    const sharedField = page.getByTestId("map-field").filter({ has: dots.first() }).first();
    await sharedField.click();
    await expect(page).toHaveURL(/field=/);
    await expect(page.getByTestId("lens-card")).toContainText("written by");
    await expect(page.locator('[data-testid="map-fn"][data-lens="write"]').first()).toBeAttached();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("lens-card")).toHaveCount(0);
  });

  test("the map is for the selected function's class, with a switch to its file", async ({ page }) => {
    await openRoot(page, "tinyxml2", "XMLNode::Unlink", "XMLNode::Unlink");
    await page.getByRole("radio", { name: "Data", exact: true }).click();
    await expect(page.getByTestId("data-map")).toBeVisible();
    await expect(page.getByRole("radio", { name: /^Class XMLNode/ })).toHaveAttribute("aria-checked", "true");
    await expect(page.getByTestId("map-fn").filter({ hasText: "Unlink" })).toBeVisible();
    await expect(page.getByTestId("map-field").filter({ hasText: "_parent" }).first()).toBeVisible();
    await page.getByRole("radio", { name: /^File tinyxml2\.cpp/ }).click();
    await expect(page.getByRole("radio", { name: /^File tinyxml2\.cpp/ })).toHaveAttribute("aria-checked", "true");
    await page.getByRole("radio", { name: "Calls", exact: true }).click();
    await expect(nodes(page).first()).toBeVisible();
  });
});
