import type { Diagnostic as TyDiagnostic, Range, Workspace } from "ty_wasm";

export type NormalizedDiagnostic = {
  id: string;
  message: string;
  severity: number;
  range: Range | undefined;
  display: string;
  raw: TyDiagnostic;
};

function callOrRead<T>(target: unknown, key: string, ...args: unknown[]): T {
  const value = (target as Record<string, unknown>)[key];
  if (typeof value === "function") {
    return (value as (...a: unknown[]) => T).call(target, ...args);
  }
  return value as T;
}

export function normalizeDiagnostic(
  diagnostic: TyDiagnostic,
  workspace: Workspace,
): NormalizedDiagnostic {
  let range: Range | undefined;
  try {
    range = callOrRead<Range | undefined>(diagnostic, "toRange", workspace);
  } catch {
    range = undefined;
  }
  if (range == null) {
    try {
      range = callOrRead<Range | undefined>(diagnostic, "range");
    } catch {
      range = undefined;
    }
  }

  let display = "";
  try {
    display = callOrRead<string>(diagnostic, "display", workspace) ?? "";
  } catch {
    display = "";
  }

  return {
    id: callOrRead<string>(diagnostic, "id") ?? "unknown",
    message: callOrRead<string>(diagnostic, "message") ?? "",
    severity: callOrRead<number>(diagnostic, "severity") ?? 2,
    range,
    display,
    raw: diagnostic,
  };
}
