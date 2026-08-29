import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Uri } from "monaco-editor";
import initTy, {
  FileHandle,
  Position as TyPosition,
  PositionEncoding,
  Workspace,
} from "ty_wasm";
import tyWasmUrl from "ty_wasm/ty_wasm_bg.wasm?url";
import CodeEditor, { type EditorHandle, type PlaygroundFile } from "./Editor/Editor";
import { normalizeDiagnostic, type NormalizedDiagnostic } from "./Editor/tyDiagnostics";
import {
  bootPyodide,
  collectSitePackageSources,
  probeImports,
  runUserScript,
  type PyodideRuntime,
} from "./Editor/pyodideRuntime";
import { DEFAULT_FILE, SAMPLES } from "./samples";

const INJECT_PACKAGES = [
  "oold",
  "pydantic",
  "pydantic_core",
  "annotated_types",
  "typing_extensions.py",
];

const PROBE_MODULES = ["rdflib", "pyld", "SPARQLWrapper", "jsondiff", "oold"];

type Status = "booting" | "ty-ready" | "installing" | "ready" | "error";

export type InjectionStats = {
  files: number;
  bytes: number;
  millis: number;
};

declare global {
  interface Window {
    __playground?: {
      workspace: Workspace;
      handles: Record<string, FileHandle>;
      TyPosition: typeof TyPosition;
      pyodide?: PyodideRuntime["pyodide"];
      runPython(code: string): Promise<unknown>;
      completionsAt(file: string, line: number, column: number): unknown[];
      hoverAt(file: string, line: number, column: number): string | null;
      status(): string;
      timings(): Record<string, number>;
      injection(): InjectionStats | null;
      imports(): Record<string, { ok: boolean; detail: string }> | null;
    };
  }
}

function describeError(caught: unknown): string {
  if (caught instanceof Error) {
    return caught.stack ?? `${caught.name}: ${caught.message}`;
  }
  if (typeof caught === "object" && caught != null) {
    const record = caught as Record<string, unknown>;
    const parts = Object.keys(record)
      .map((key) => `${key}=${String(record[key])}`)
      .join(", ");
    return `${Object.prototype.toString.call(caught)} { ${parts} }`;
  }
  return String(caught);
}

function createWorkspace(): Workspace {
  try {
    return new Workspace("/", PositionEncoding.Utf16, {});
  } catch {
    return new (Workspace as unknown as new (
      root: string,
      options: unknown,
    ) => Workspace)("/", {});
  }
}

export default function Playground() {
  const [status, setStatus] = useState<Status>("booting");
  const [progress, setProgress] = useState("starting");
  const [error, setError] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [files, setFiles] = useState<PlaygroundFile[]>([]);
  const [selected, setSelected] = useState(DEFAULT_FILE);
  const [contents, setContents] = useState<Record<string, string>>({ ...SAMPLES });
  const [diagnostics, setDiagnostics] = useState<NormalizedDiagnostic[]>([]);
  const [output, setOutput] = useState("");
  const [injection, setInjection] = useState<InjectionStats | null>(null);
  const [importResults, setImportResults] = useState<Record<
    string,
    { ok: boolean; detail: string }
  > | null>(null);
  const [tyVersion, setTyVersion] = useState("");

  const runtimeRef = useRef<PyodideRuntime | null>(null);
  const editorRef = useRef<EditorHandle | null>(null);
  const timings = useRef<Record<string, number>>({ start: performance.now() });

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        setProgress("initialising ty wasm");
        await initTy({ module_or_path: tyWasmUrl });
        if (cancelled) return;

        const ws = createWorkspace();
        const opened: PlaygroundFile[] = Object.keys(SAMPLES).map((name) => ({
          id: name,
          name,
          handle: ws.openFile(name, SAMPLES[name]),
          uri: Uri.file(name),
        }));

        try {
          const mod = await import("ty_wasm");
          setTyVersion(
            typeof mod.version === "function" ? mod.version() : "unknown",
          );
        } catch {
          setTyVersion("unknown");
        }

        setWorkspace(ws);
        setFiles(opened);
        timings.current.tyReady = performance.now();
        setStatus("ty-ready");
        setProgress("ty ready, booting pyodide");

        const runtime = await bootPyodide(
          (message) => !cancelled && setProgress(message),
          (line) => setOutput((prev) => prev + line + "\n"),
        );
        if (cancelled) return;
        runtimeRef.current = runtime;
        timings.current.pyodideReady = performance.now();
        setStatus("installing");

        setProgress("probing oold runtime imports");
        const probes = await probeImports(runtime.pyodide, PROBE_MODULES);
        if (cancelled) return;
        setImportResults(probes);
        timings.current.importsProbed = performance.now();

        setProgress("injecting site-packages into the ty workspace");
        const injectStart = performance.now();
        const sources = await collectSitePackageSources(
          runtime.pyodide,
          INJECT_PACKAGES,
        );
        if (cancelled) return;

        let bytes = 0;
        for (const entry of sources) {
          bytes += entry.source.length;
          try {
            ws.openFile(entry.path, entry.source);
          } catch {
            // ty rejects absolute and vendored paths; skip those silently
          }
        }
        const stats = {
          files: sources.length,
          bytes,
          millis: Math.round(performance.now() - injectStart),
        };
        setInjection(stats);
        timings.current.injected = performance.now();

        setStatus("ready");
        setProgress("ready");
      } catch (caught) {
        if (cancelled) return;
        setError(String(caught));
        setStatus("error");
        setProgress("failed");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const handleByName = useMemo(() => {
    const map: Record<string, FileHandle> = {};
    for (const file of files) {
      map[file.name] = file.handle;
    }
    return map;
  }, [files]);

  const recheck = useCallback(
    (ws: Workspace, name: string) => {
      const handle = handleByName[name];
      if (handle == null) {
        return;
      }
      try {
        const raw = ws.checkFile(handle);
        setDiagnostics(raw.map((item) => normalizeDiagnostic(item, ws)));
      } catch (caught) {
        setDiagnostics([]);
        setError(String(caught));
      }
    },
    [handleByName],
  );

  useEffect(() => {
    if (workspace == null) {
      return;
    }
    recheck(workspace, selected);
  }, [workspace, selected, recheck, status, injection]);

  const onChange = useCallback(
    (value: string) => {
      setContents((prev) => ({ ...prev, [selected]: value }));
      if (workspace == null) {
        return;
      }
      const handle = handleByName[selected];
      if (handle == null) {
        return;
      }
      workspace.updateFile(handle, value);
      recheck(workspace, selected);
    },
    [workspace, selected, handleByName, recheck],
  );

  const run = useCallback(async () => {
    const runtime = runtimeRef.current;
    if (runtime == null) {
      return;
    }
    setOutput("");
    try {
      await runUserScript(runtime.pyodide, contents, selected);
    } catch (caught) {
      setOutput((prev) => prev + "\n" + describeError(caught));
    }
  }, [contents, selected]);

  useEffect(() => {
    if (workspace == null) {
      return;
    }
    window.__playground = {
      workspace,
      handles: handleByName,
      TyPosition,
      pyodide: runtimeRef.current?.pyodide,
      runPython: async (code: string) => {
        const runtime = runtimeRef.current;
        if (runtime == null) {
          throw new Error("pyodide is not ready");
        }
        return runtime.pyodide.runPythonAsync(code);
      },
      completionsAt: (file, line, column) => {
        const handle = handleByName[file];
        if (handle == null) {
          throw new Error(`unknown file ${file}`);
        }
        return workspace
          .completions(handle, new TyPosition(line, column))
          .map((completion) => ({
            name: completion.name,
            kind: completion.kind,
            detail: completion.detail ?? null,
          }));
      },
      hoverAt: (file, line, column) => {
        const handle = handleByName[file];
        if (handle == null) {
          throw new Error(`unknown file ${file}`);
        }
        const hover = workspace.hover(handle, new TyPosition(line, column));
        return hover?.markdown ?? null;
      },
      status: () => status,
      timings: () => timings.current,
      injection: () => injection,
      imports: () => importResults,
    };
  }, [workspace, handleByName, status, injection, importResults]);

  const onEditorMount = useCallback((handle: EditorHandle) => {
    editorRef.current = handle;
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>oold ty playground</h1>
        <div className="meta">
          <span data-testid="status">{status}</span>
          <span data-testid="progress">{progress}</span>
          {tyVersion ? <span data-testid="ty-version">ty {tyVersion}</span> : null}
        </div>
        <button
          type="button"
          data-testid="run"
          disabled={status !== "ready"}
          onClick={run}
        >
          Run
        </button>
      </header>

      <nav className="tabs" data-testid="file-tabs">
        {files.map((file) => (
          <button
            key={file.id}
            type="button"
            data-testid={`tab-${file.name}`}
            className={file.name === selected ? "tab active" : "tab"}
            onClick={() => setSelected(file.name)}
          >
            {file.name}
          </button>
        ))}
      </nav>

      <main className="body">
        <section className="editor" data-testid="editor">
          {workspace == null ? (
            <p className="placeholder">initialising ty...</p>
          ) : (
            <CodeEditor
              fileName={selected}
              value={contents[selected] ?? ""}
              files={files}
              diagnostics={diagnostics}
              workspace={workspace}
              onChange={onChange}
              onMount={onEditorMount}
            />
          )}
        </section>

        <aside className="side">
          <h2>Diagnostics ({diagnostics.length})</h2>
          <ul data-testid="diagnostics">
            {diagnostics.map((diagnostic, index) => (
              <li key={`${diagnostic.id}-${index}`} data-testid="diagnostic">
                <code>{diagnostic.id}</code>{" "}
                <span>
                  {diagnostic.range?.start.line}:{diagnostic.range?.start.column}
                </span>{" "}
                {diagnostic.message}
              </li>
            ))}
          </ul>

          <h2>Runtime imports</h2>
          <ul data-testid="imports">
            {importResults == null ? (
              <li>pending</li>
            ) : (
              Object.entries(importResults).map(([name, result]) => (
                <li key={name} data-testid={`import-${name}`}>
                  {name}: {result.ok ? "ok" : "FAILED"} ({result.detail})
                </li>
              ))
            )}
          </ul>

          <h2>Injected sources</h2>
          <p data-testid="injection">
            {injection == null
              ? "pending"
              : `${injection.files} files, ${(injection.bytes / 1024).toFixed(0)} KB, ${injection.millis} ms`}
          </p>

          <h2>Output</h2>
          <pre data-testid="output">{output}</pre>

          {error ? (
            <>
              <h2>Error</h2>
              <pre data-testid="error">{error}</pre>
            </>
          ) : null}
        </aside>
      </main>
    </div>
  );
}
