import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("console", (m) => console.log(`[console:${m.type()}]`, m.text().slice(0, 400)));
page.on("pageerror", (e) => console.log("[pageerror]", String(e).slice(0, 800)));
page.on("requestfailed", (r) => console.log("[reqfail]", r.url().slice(0,150), r.failure()?.errorText));
page.on("response", (r) => { if (r.status() >= 400) console.log("[http]", r.status(), r.url().slice(0,150)); });
await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
for (let i = 0; i < 120; i++) {
  const status = await page.getByTestId("status").textContent().catch(() => null);
  if (status === "error" || status === "ready") break;
  await page.waitForTimeout(2000);
}
console.log("STATUS:", await page.getByTestId("status").textContent().catch(()=>null));
console.log("PROGRESS:", await page.getByTestId("progress").textContent().catch(()=>null));
const err = await page.getByTestId("error").textContent().catch(() => null);
console.log("ERROR TEXT:\n", err);
console.log("OUTPUT:\n", (await page.getByTestId("output").textContent().catch(()=>null) ?? "").slice(-3000));
await browser.close();
