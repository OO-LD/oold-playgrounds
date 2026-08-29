import type { PyodideInterface } from "pyodide";
import overlaySource from "./linkOverlay.py?raw";

export type LinkResolutionEvent = {
  field: string | null;
  file: string | null;
  line: number | null;
  target: string;
  iris: string[];
  fetchedIris: string[];
  cached: number;
  backendCalls: number;
  resolvers: string[];
  values: Record<string, unknown>;
  ms: number;
};

export type BackendCall = {
  resolver: string;
  iris: string[];
  values: Record<string, unknown>;
  ms: number;
};

export type OverlayDrain = {
  events: LinkResolutionEvent[];
  backendCalls: BackendCall[];
};

const MODULE_PATH = "/lib/oold_overlay.py";

export async function loadOverlayModule(pyodide: PyodideInterface): Promise<void> {
  pyodide.FS.mkdirTree("/lib");
  pyodide.FS.writeFile(MODULE_PATH, overlaySource.replace(/\r\n/g, "\n"));
  await pyodide.runPythonAsync(`
import sys
if "/lib" not in sys.path:
    sys.path.insert(0, "/lib")
import oold_overlay
`);
}

export async function setOverlayEnabled(
  pyodide: PyodideInterface,
  enabled: boolean,
): Promise<string> {
  return pyodide.runPythonAsync(`
import oold_overlay
oold_overlay.${enabled ? "install" : "uninstall"}()
`);
}

export async function resetOverlay(pyodide: PyodideInterface): Promise<void> {
  await pyodide.runPythonAsync(`
import oold_overlay
oold_overlay.reset()
`);
}

export async function drainOverlay(
  pyodide: PyodideInterface,
): Promise<OverlayDrain> {
  const payload = await pyodide.runPythonAsync(`
import oold_overlay
oold_overlay.drain()
`);
  return JSON.parse(payload) as OverlayDrain;
}

function renderValue(value: unknown): string {
  if (value == null) {
    return "null";
  }
  if (typeof value !== "object") {
    return typeof value === "string" ? `"${value}"` : String(value);
  }
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([key]) => key !== "id" && key !== "@id",
  );
  const rendered = entries
    .map(([key, item]) => `${key}: ${renderValue(item)}`)
    .join(", ");
  return `{${rendered}}`;
}

export function formatEvent(event: LinkResolutionEvent): string {
  const field = event.field ?? "link";

  if (event.backendCalls === 0) {
    return `${field} -> cached ${event.iris.length} IRI${event.iris.length === 1 ? "" : "s"}, 0 backend calls`;
  }

  const fetched = event.fetchedIris
    .map((iri) => `${iri} ${renderValue(event.values[iri])}`)
    .join(", ");

  const summary = `${event.backendCalls} backend call${event.backendCalls === 1 ? "" : "s"}, ${event.iris.length} IRI${event.iris.length === 1 ? "" : "s"}, ${event.cached} cached`;
  const via = event.resolvers.length > 0 ? ` via ${event.resolvers.join(", ")}` : "";

  return `${field} -> ${fetched}${via}  [${summary}]`;
}

export function formatEventDetail(event: LinkResolutionEvent): string {
  const lines = [
    `**${event.field ?? "link"}** resolved to \`${event.target}\``,
    "",
    `- requested IRIs: ${event.iris.join(", ") || "(none)"}`,
    `- fetched from backend: ${event.fetchedIris.join(", ") || "(none)"}`,
    `- served from the Ref cache: ${event.cached}`,
    `- backend calls: ${event.backendCalls}${event.resolvers.length ? ` (${event.resolvers.join(", ")})` : ""}`,
    `- elapsed: ${event.ms} ms`,
    "",
    "Values that exist nowhere in this line:",
    "",
    "```json",
    JSON.stringify(event.values, null, 2),
    "```",
  ];
  return lines.join("\n");
}
