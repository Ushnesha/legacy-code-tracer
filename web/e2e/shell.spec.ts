import { expect, test, type Page } from "@playwright/test";

test.beforeAll(async ({ request }) => {
  const res = await request.get("http://localhost:8000/api/repos");
  const repos = (await res.json()) as { name: string }[];
  if (!repos.some((r) => r.name === "tinyxml2")) {
    throw new Error("tinyxml2 is not loaded. Run: make extract REPO=repos/tinyxml2 && make enrich REPO=repos/tinyxml2 && make load REPO=repos/tinyxml2");
  }
});

const search = (page: Page) => page.getByRole("combobox", { name: /Find a function/ });

const results = (page: Page) => page.getByRole("listbox", { name: "Search results" }).getByRole("option");

async function pickTinyxml2(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /tinyxml2/ }).click();
  await expect(page).toHaveURL(/repo=tinyxml2/);
}

test("pick tinyxml2, search Parse, click XMLDocument::Parse: the URL gets root=", async ({ page }) => {
  await pickTinyxml2(page);

  // Nothing picked yet.
  await expect(page.getByTestId("inspector-empty")).toHaveText("Pick a function to see what it does.");
  expect(new URL(page.url()).searchParams.has("root")).toBe(false);

  await search(page).fill("Parse");
  const option = results(page).filter({ hasText: "XMLDocument::Parse" }).first();
  await expect(option).toBeVisible();
  await option.click();

  await expect(page).toHaveURL(/root=[0-9a-f]{12}/);
  await expect(page.getByTestId("canvas-root-name")).toContainText("XMLDocument::Parse");
  await expect(page.getByTestId("breadcrumb-root")).toContainText("XMLDocument::Parse");
  await expect(page.getByTestId("inspector-empty")).toHaveCount(0);
  await expect(page.getByRole("tabpanel").getByRole("heading", { name: /XMLDocument::Parse/ })).toBeVisible();
});

test("the link restores the same view after a reload, including direction", async ({ page }) => {
  await pickTinyxml2(page);
  await search(page).fill("LoadFile");
  await results(page).first().click();
  await page.getByRole("radio", { name: "Who calls it" }).click();
  await expect(page).toHaveURL(/dir=up/);
  const url = page.url();
  await page.reload();
  await expect(page).toHaveURL(url);
  await expect(page.getByRole("radio", { name: "Who calls it" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("canvas-root-name")).toContainText("LoadFile");
});

test("Back goes to the previous function", async ({ page }) => {
  await pickTinyxml2(page);
  await search(page).fill("Unlink");
  await expect(results(page).first()).toContainText("Unlink");
  await results(page).first().click();
  const first = page.url();
  await search(page).fill("Clear");
  await expect(results(page).first()).toContainText("Clear");
  await results(page).first().click();
  expect(page.url()).not.toBe(first);
  await page.goBack();
  await expect(page).toHaveURL(first);
  await expect(page.getByTestId("canvas-root-name")).toContainText("Unlink");
});

test("'/' focuses search; arrow keys and Enter pick a result", async ({ page }) => {
  await pickTinyxml2(page);
  await page.locator("body").click({ position: { x: 700, y: 400 } });
  await page.keyboard.press("/");
  await expect(search(page)).toBeFocused();
  await page.keyboard.type("Unlink");
  await expect(results(page).first()).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await expect(results(page).nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/root=/);
  await expect(page.getByTestId("canvas-root-name")).toContainText("Unlink");
});

test("the data tab shows what a function changes", async ({ page }) => {
  await pickTinyxml2(page);
  await search(page).fill("XMLNode::Unlink");
  await results(page).first().click();
  await page.getByRole("tab", { name: "Data" }).click();
  await expect(page.getByRole("button", { name: /_parent/ }).first()).toBeVisible();
  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.getByLabel("Source code")).toContainText("Unlink");
  await page.getByRole("tab", { name: "Impact" }).click();
  await expect(page.getByText(/Up to \d+ functions could be affected/)).toBeVisible();
});

test("dark theme is a toggle and light is the default", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("below 900px the side panels become drawers", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 800 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: /tinyxml2/ })).toBeHidden(); // left drawer is closed
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: /tinyxml2/ }).click();
  await search(page).fill("Unlink");
  await results(page).first().click();
  await expect(page).toHaveURL(/root=/);
  await expect(search(page)).toBeHidden(); // picking closes the drawer
  await page.getByRole("button", { name: "Details", exact: true }).click();
  await expect(page.getByRole("tabpanel").getByRole("heading", { name: /Unlink/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tabpanel").getByRole("heading", { name: /Unlink/ })).toBeHidden();
});

test("a server that is down gives one plain sentence", async ({ page }) => {
  await page.route("**/api/repos", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Couldn't reach the server");
  await expect(page.getByRole("alert")).toContainText("make serve");
});
