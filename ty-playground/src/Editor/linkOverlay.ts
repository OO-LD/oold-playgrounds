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

const INLINE_KEY_LIMIT = 4;
const INLINE_DEPTH_LIMIT = 2;
const INLINE_TEXT_LIMIT = 220;

/**
 * A remote payload can be far larger than a locally seeded one: a single
 * Wikidata entity carries every property in every language. The inline text is
 * capped so the decoration stays readable; the hover keeps the full JSON.
 */
function renderValue(value: unknown, depth = 0): string {
  if (value == null) {
    return "null";
  }
  if (typeof value !== "object") {
    const text = typeof value === "string" ? `"${value}"` : String(value);
    return text.length > 60 ? `${text.slice(0, 57)}..."` : text;
  }
  if (depth >= INLINE_DEPTH_LIMIT) {
    return Array.isArray(value) ? `[${value.length} items]` : "{...}";
  }
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([key]) => key !== "id" && key !== "@id",
  );
  const shown = entries.slice(0, INLINE_KEY_LIMIT);
  const rendered = shown
    .map(([key, item]) => `${shortKey(key)}: ${renderValue(item, depth + 1)}`)
    .join(", ");
  const omitted = entries.length - shown.length;
  return `{${rendered}${omitted > 0 ? `, +${omitted} more` : ""}}`;
}

/** IRI-shaped keys are shown by their local name so the line stays scannable. */
function shortKey(key: string): string {
  const match = /[^/#]+$/.exec(key);
  return match ? match[0] : key;
}

function truncate(text: string): string {
  return text.length > INLINE_TEXT_LIMIT
    ? `${text.slice(0, INLINE_TEXT_LIMIT - 3)}...`
    : text;
}

export function formatEvent(event: LinkResolutionEvent): string {
  const field = event.field ?? "link";

  if (event.backendCalls === 0) {
    return `${field} -> cached ${event.iris.length} IRI${event.iris.length === 1 ? "" : "s"}, 0 backend calls`;
  }

  const fetched = truncate(
    event.fetchedIris
      .map((iri) => `${iri} ${renderValue(event.values[iri])}`)
      .join(", "),
  );

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
