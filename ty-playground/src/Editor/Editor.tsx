import Monaco, { type Monaco as MonacoApi, type OnMount } from "@monaco-editor/react";
import {
  type CancellationToken,
  editor,
  type IDisposable,
  type IPosition,
  type IRange,
  languages,
  MarkerSeverity,
  Position,
  Range,
  Uri,
} from "monaco-editor";
import { useCallback, useEffect, useRef } from "react";
import {
  CompletionKind,
  DocumentHighlightKind,
  type DocumentHighlight,
  type FileHandle,
  InlayHintKind,
  type LocationLink,
  Position as TyPosition,
  Range as TyRange,
  SemanticToken,
  Severity,
  type TextEdit,
  type Workspace,
} from "ty_wasm";
import type { NormalizedDiagnostic } from "./tyDiagnostics";
import {
  formatEvent,
  formatEventDetail,
  type LinkResolutionEvent,
} from "./linkOverlay";

import IStandaloneCodeEditor = editor.IStandaloneCodeEditor;
import CompletionItemKind = languages.CompletionItemKind;

export type PlaygroundFile = {
  id: string;
  name: string;
  handle: FileHandle;
  uri: Uri;
};

type Props = {
  fileName: string;
  value: string;
  files: PlaygroundFile[];
  diagnostics: NormalizedDiagnostic[];
  linkEvents: LinkResolutionEvent[];
  workspace: Workspace;
  onChange(value: string): void;
  onMount(handle: EditorHandle): void;
};

export type EditorHandle = {
  editor: IStandaloneCodeEditor;
  monaco: MonacoApi;
};

export default function CodeEditor({
  fileName,
  value,
  files,
  diagnostics,
  linkEvents,
  workspace,
  onChange,
  onMount,
}: Props) {
  const serverRef = useRef<PlaygroundServer | null>(null);
  const editorRef = useRef<IStandaloneCodeEditor | null>(null);
  const decorationsRef = useRef<editor.IEditorDecorationsCollection | null>(null);

  useEffect(() => {
    const instance = editorRef.current;
    if (instance == null) {
      return;
    }

    const model = instance.getModel();
    if (model == null) {
      return;
    }

    const byLine = new Map<number, LinkResolutionEvent[]>();
    for (const event of linkEvents) {
      if (event.file !== fileName || event.line == null) {
        continue;
      }
      if (event.line < 1 || event.line > model.getLineCount()) {
        continue;
      }
      const bucket = byLine.get(event.line);
      if (bucket == null) {
        byLine.set(event.line, [event]);
      } else {
        bucket.push(event);
      }
    }

    const decorations = [...byLine.entries()].map(([line, events]) => ({
      range: new Range(line, model.getLineLength(line) + 1, line, model.getLineLength(line) + 1),
      options: {
        after: {
          content: `    ${events.map(formatEvent).join("   |   ")}`,
          inlineClassName: "oold-link-overlay",
        },
        hoverMessage: events.map((event) => ({
          value: formatEventDetail(event),
          isTrusted: true,
        })),
        showIfCollapsed: true,
      },
    }));

    if (decorationsRef.current == null) {
      decorationsRef.current = instance.createDecorationsCollection(decorations);
    } else {
      decorationsRef.current.set(decorations);
    }
  }, [linkEvents, fileName, value]);

  useEffect(() => {
    serverRef.current?.update({ workspace, files });
  }, [workspace, files]);

  useEffect(() => {
    serverRef.current?.updateMarkers(diagnostics);
  }, [diagnostics]);

  useEffect(() => () => serverRef.current?.dispose(), []);

  const handleMount: OnMount = useCallback(
    (instance, monacoApi) => {
      serverRef.current?.dispose();
      editorRef.current = instance;
      decorationsRef.current = null;
      const server = new PlaygroundServer(monacoApi, instance, {
        workspace,
        files,
      });
      server.updateMarkers(diagnostics);
      serverRef.current = server;
      onMount({ editor: instance, monaco: monacoApi });
    },
    [workspace, files, diagnostics, onMount],
  );

  return (
    <Monaco
      onMount={handleMount}
      defaultValue={value}
      onChange={(next) => onChange(next ?? "")}
      options={{
        fixedOverflowWidgets: true,
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        "semanticHighlighting.enabled": true,
      }}
      language="python"
      path={fileName}
      theme="vs-dark"
    />
  );
}

type ServerProps = {
  workspace: Workspace;
  files: PlaygroundFile[];
};

class PlaygroundServer
  implements
    languages.TypeDefinitionProvider,
    languages.DeclarationProvider,
    languages.DefinitionProvider,
    languages.ReferenceProvider,
    editor.ICodeEditorOpener,
    languages.HoverProvider,
    languages.InlayHintsProvider,
    languages.DocumentFormattingEditProvider,
    languages.CompletionItemProvider,
    languages.DocumentSemanticTokensProvider,
    languages.DocumentRangeSemanticTokensProvider,
    languages.SignatureHelpProvider,
    languages.DocumentHighlightProvider,
    languages.CodeActionProvider,
    languages.RenameProvider
{
  private diagnostics: NormalizedDiagnostic[] = [];
  private providerDisposables: IDisposable[];
  private vendoredFileHandles = new Map<string, FileHandle>();

  triggerCharacters: string[] = ["."];
  signatureHelpTriggerCharacters: string[] = ["(", ","];
  signatureHelpRetriggerCharacters: string[] = [")"];
  resolveCompletionItem: undefined;

  constructor(
    private monaco: MonacoApi,
    private editorInstance: IStandaloneCodeEditor,
    private props: ServerProps,
  ) {
    this.providerDisposables = [
      monaco.languages.registerTypeDefinitionProvider("python", this),
      monaco.languages.registerDeclarationProvider("python", this),
      monaco.languages.registerDefinitionProvider("python", this),
      monaco.languages.registerReferenceProvider("python", this),
      monaco.languages.registerHoverProvider("python", this),
      monaco.languages.registerInlayHintsProvider("python", this),
      monaco.languages.registerCompletionItemProvider("python", this),
      monaco.languages.registerDocumentSemanticTokensProvider("python", this),
      monaco.languages.registerDocumentRangeSemanticTokensProvider("python", this),
      monaco.editor.registerEditorOpener(this),
      monaco.languages.registerDocumentFormattingEditProvider("python", this),
      monaco.languages.registerSignatureHelpProvider("python", this),
      monaco.languages.registerDocumentHighlightProvider("python", this),
      monaco.languages.registerCodeActionProvider("python", this),
      monaco.languages.registerRenameProvider("python", this),
    ];
  }

  update(props: ServerProps) {
    this.props = props;
  }

  private getVendoredPath(uri: Uri): string {
    return uri.authority ? `${uri.authority}${uri.path}` : uri.path;
  }

  private getOrCreateVendoredFileHandle(path: string): FileHandle {
    const cached = this.vendoredFileHandles.get(path);
    if (cached != null) {
      return cached;
    }
    const handle = this.props.workspace.getVendoredFile(path);
    this.vendoredFileHandles.set(path, handle);
    return handle;
  }

  private getFileHandleForModel(model: editor.ITextModel): FileHandle | null {
    if (model.uri.scheme === "vendored") {
      return this.getOrCreateVendoredFileHandle(this.getVendoredPath(model.uri));
    }
    const file = this.props.files.find(
      (candidate) => candidate.uri.toString() === model.uri.toString(),
    );
    return file?.handle ?? null;
  }

  getLegend(): languages.SemanticTokensLegend {
    return {
      tokenTypes: SemanticToken.kinds(),
      tokenModifiers: SemanticToken.modifiers(),
    };
  }

  provideDocumentSemanticTokens(
    model: editor.ITextModel,
  ): languages.SemanticTokens | null {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return null;
    }
    return generateMonacoTokens(this.props.workspace.semanticTokens(handle), model);
  }

  releaseDocumentSemanticTokens() {}

  provideDocumentRangeSemanticTokens(
    model: editor.ITextModel,
    range: Range,
  ): languages.SemanticTokens | null {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return null;
    }
    const tokens = this.props.workspace.semanticTokensInRange(
      handle,
      monacoRangeToTyRange(range),
    );
    return generateMonacoTokens(tokens, model);
  }

  provideCompletionItems(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.CompletionList> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }

    const completions = this.props.workspace.completions(
      handle,
      new TyPosition(position.lineNumber, position.column),
    );

    const digits = String(Math.max(completions.length - 1, 0)).length;

    const word = model.getWordUntilPosition(position);
    const range: IRange = {
      startLineNumber: position.lineNumber,
      endLineNumber: position.lineNumber,
      startColumn: word.startColumn,
      endColumn: position.column,
    };

    return {
      incomplete: true,
      suggestions: completions.map((completion, index) => ({
        label: {
          label: completion.name,
          description: completion.detail ?? undefined,
        },
        sortText: String(index).padStart(digits, "0"),
        kind:
          completion.kind == null
            ? CompletionItemKind.Variable
            : mapCompletionKind(completion.kind),
        insertText: completion.insert_text ?? completion.name,
        additionalTextEdits: completion.additional_text_edits?.map(
          (edit: TextEdit) => ({
            range: tyRangeToMonacoRange(edit.range),
            text: edit.new_text,
          }),
        ),
        documentation: completion.documentation,
        detail: completion.detail,
        range,
      })),
    };
  }

  provideSignatureHelp(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.SignatureHelpResult> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    const help = this.props.workspace.signatureHelp(
      handle,
      new TyPosition(position.lineNumber, position.column),
    );
    if (help == null) {
      return undefined;
    }
    return {
      dispose() {},
      value: {
        signatures: help.signatures.map((signature) => ({
          label: signature.label,
          documentation: signature.documentation
            ? { value: signature.documentation }
            : undefined,
          parameters: signature.parameters.map((parameter) => ({
            label: parameter.label,
            documentation: parameter.documentation
              ? { value: parameter.documentation }
              : undefined,
          })),
          activeParameter: signature.active_parameter,
        })),
        activeSignature: help.active_signature ?? 0,
        activeParameter:
          help.active_signature != null
            ? (help.signatures[help.active_signature]?.active_parameter ?? 0)
            : 0,
      },
    };
  }

  provideDocumentHighlights(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.DocumentHighlight[]> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    return this.props.workspace
      .documentHighlights(handle, new TyPosition(position.lineNumber, position.column))
      .map((highlight: DocumentHighlight) => ({
        range: tyRangeToMonacoRange(highlight.range),
        kind: mapDocumentHighlightKind(highlight.kind),
      }));
  }

  provideInlayHints(
    model: editor.ITextModel,
    range: Range,
  ): languages.ProviderResult<languages.InlayHintList> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    const hints = this.props.workspace.inlayHints(handle, monacoRangeToTyRange(range));
    if (hints.length === 0) {
      return undefined;
    }
    return {
      dispose: () => {},
      hints: hints.map((hint) => ({
        label: hint.label.map((part) => ({ label: part.label })),
        position: { lineNumber: hint.position.line, column: hint.position.column },
        kind:
          hint.kind === InlayHintKind.Type
            ? languages.InlayHintKind.Type
            : languages.InlayHintKind.Parameter,
        textEdits: hint.text_edits.map((edit: TextEdit) => ({
          range: tyRangeToMonacoRange(edit.range),
          text: edit.new_text,
        })),
      })),
    };
  }

  resolveInlayHint(): languages.ProviderResult<languages.InlayHint> {
    return undefined;
  }

  provideHover(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.Hover> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    const hover = this.props.workspace.hover(
      handle,
      new TyPosition(position.lineNumber, position.column),
    );
    if (hover == null) {
      return undefined;
    }
    return {
      range: tyRangeToMonacoRange(hover.range),
      contents: [{ value: hover.markdown, isTrusted: true }],
    };
  }

  provideTypeDefinition(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.Definition | languages.LocationLink[]> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    return this.mapNavigationTargets(
      this.props.workspace.gotoTypeDefinition(
        handle,
        new TyPosition(position.lineNumber, position.column),
      ),
    );
  }

  provideDeclaration(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.Definition | languages.LocationLink[]> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    return this.mapNavigationTargets(
      this.props.workspace.gotoDeclaration(
        handle,
        new TyPosition(position.lineNumber, position.column),
      ),
    );
  }

  provideDefinition(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.Definition | languages.LocationLink[]> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    return this.mapNavigationTargets(
      this.props.workspace.gotoDefinition(
        handle,
        new TyPosition(position.lineNumber, position.column),
      ),
    );
  }

  provideReferences(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.Location[]> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }
    return this.mapNavigationTargets(
      this.props.workspace.gotoReferences(
        handle,
        new TyPosition(position.lineNumber, position.column),
      ),
    );
  }

  provideCodeActions(
    model: editor.ITextModel,
    range: Range,
  ): languages.ProviderResult<languages.CodeActionList> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return undefined;
    }

    const actions: languages.CodeAction[] = [];
    for (const diagnostic of this.diagnostics) {
      if (diagnostic.range == null) {
        continue;
      }
      const monacoRange = tyRangeToMonacoRange(diagnostic.range);
      if (!Range.areIntersecting(range, monacoRange)) {
        continue;
      }
      const codeActions = this.props.workspace.codeActions(handle, diagnostic.raw);
      if (codeActions == null) {
        continue;
      }
      for (const codeAction of codeActions) {
        actions.push({
          title: codeAction.title,
          kind: "quickfix",
          isPreferred: codeAction.preferred,
          edit: {
            edits: codeAction.edits.map((edit) => ({
              resource: model.uri,
              textEdit: {
                range: tyRangeToMonacoRange(edit.range),
                text: edit.new_text,
              },
              versionId: model.getVersionId(),
            })),
          },
        });
      }
    }

    if (actions.length === 0) {
      return undefined;
    }
    return { actions, dispose: () => {} };
  }

  provideDocumentFormattingEdits(
    model: editor.ITextModel,
  ): languages.ProviderResult<languages.TextEdit[]> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null) {
      return null;
    }
    const formatted = this.props.workspace.format(handle);
    if (formatted == null) {
      return null;
    }
    return [{ range: model.getFullModelRange(), text: formatted }];
  }

  private renameRejection<T>(): T & languages.Rejection {
    return { rejectReason: "this element can't be renamed" } as T & languages.Rejection;
  }

  resolveRenameLocation(
    model: editor.ITextModel,
    position: Position,
  ): languages.ProviderResult<languages.RenameLocation & languages.Rejection> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null || model.uri.scheme === "vendored") {
      return this.renameRejection();
    }
    const range = this.props.workspace.prepareRename(
      handle,
      new TyPosition(position.lineNumber, position.column),
    );
    if (range == null) {
      return this.renameRejection();
    }
    const monacoRange = tyRangeToMonacoRange(range);
    return { range: monacoRange, text: model.getValueInRange(monacoRange) };
  }

  provideRenameEdits(
    model: editor.ITextModel,
    position: Position,
    newName: string,
  ): languages.ProviderResult<languages.WorkspaceEdit & languages.Rejection> {
    const handle = this.getFileHandleForModel(model);
    if (handle == null || model.uri.scheme === "vendored") {
      return this.renameRejection();
    }
    const renameEdits = this.props.workspace.rename(
      handle,
      new TyPosition(position.lineNumber, position.column),
      newName,
    );
    if (renameEdits.length === 0) {
      return this.renameRejection();
    }
    const edits: languages.IWorkspaceTextEdit[] = [];
    for (const edit of renameEdits) {
      const targetModel = this.monaco.editor.getModel(Uri.file(edit.path));
      if (targetModel == null) {
        continue;
      }
      edits.push({
        resource: targetModel.uri,
        textEdit: {
          range: tyRangeToMonacoRange(edit.range),
          text: edit.new_text,
        },
        versionId: targetModel.getVersionId(),
      });
    }
    return { edits };
  }

  openCodeEditor(
    source: editor.ICodeEditor,
    resource: Uri,
    selectionOrPosition?: IRange | IPosition,
  ): boolean {
    const model =
      resource.scheme === "vendored"
        ? (this.monaco.editor.getModel(resource) ??
          this.createVendoredModel(resource))
        : this.monaco.editor.getModel(resource);

    if (model == null) {
      return false;
    }

    source.setModel(model);

    if (selectionOrPosition != null) {
      if (Position.isIPosition(selectionOrPosition)) {
        source.setPosition(selectionOrPosition);
        source.revealPositionInCenterIfOutsideViewport(selectionOrPosition);
      } else {
        source.setSelection(selectionOrPosition);
        source.revealRangeNearTopIfOutsideViewport(selectionOrPosition);
      }
    }
    return true;
  }

  private createVendoredModel(uri: Uri): editor.ITextModel {
    const handle = this.getOrCreateVendoredFileHandle(this.getVendoredPath(uri));
    const content = this.props.workspace.sourceText(handle);
    return this.monaco.editor.createModel(content, "python", uri);
  }

  private mapNavigationTargets(links: LocationLink[]): languages.LocationLink[] {
    return links.map((link) => {
      const uri = link.path.startsWith("vendored:")
        ? Uri.parse(link.path)
        : Uri.file(link.path);
      const full = tyRangeToMonacoRange(link.full_range);
      return {
        uri:
          uri.scheme === "vendored"
            ? (this.monaco.editor.getModel(uri)?.uri ?? this.createVendoredModel(uri).uri)
            : uri,
        range: full,
        targetSelectionRange:
          link.selection_range == null
            ? undefined
            : tyRangeToMonacoRange(link.selection_range),
        originSelectionRange:
          link.origin_selection_range == null
            ? undefined
            : tyRangeToMonacoRange(link.origin_selection_range),
      } as languages.LocationLink;
    });
  }

  updateMarkers(diagnostics: NormalizedDiagnostic[]) {
    this.diagnostics = diagnostics;
    const model = this.editorInstance.getModel();
    if (model == null) {
      return;
    }

    this.monaco.editor.setModelMarkers(
      model,
      "ty",
      diagnostics.map((diagnostic) => ({
        code: diagnostic.id,
        startLineNumber: diagnostic.range?.start.line ?? 1,
        startColumn: diagnostic.range?.start.column ?? 1,
        endLineNumber: diagnostic.range?.end.line ?? 1,
        endColumn: diagnostic.range?.end.column ?? 1,
        message: diagnostic.message,
        severity: mapSeverity(diagnostic.severity),
      })),
    );
  }

  dispose() {
    for (const disposable of this.providerDisposables) {
      disposable.dispose();
    }
    this.providerDisposables = [];
  }
}

function mapSeverity(severity: number): MarkerSeverity {
  switch (severity) {
    case Severity.Info:
      return MarkerSeverity.Info;
    case Severity.Warning:
      return MarkerSeverity.Warning;
    default:
      return MarkerSeverity.Error;
  }
}

function tyRangeToMonacoRange(range: TyRange): IRange {
  return {
    startLineNumber: range.start.line,
    startColumn: range.start.column,
    endLineNumber: range.end.line,
    endColumn: range.end.column,
  };
}

function monacoRangeToTyRange(range: IRange): TyRange {
  return new TyRange(
    new TyPosition(range.startLineNumber, range.startColumn),
    new TyPosition(range.endLineNumber, range.endColumn),
  );
}

function generateMonacoTokens(
  semantic: SemanticToken[],
  model: editor.ITextModel,
): languages.SemanticTokens {
  const result: number[] = [];
  const lineCount = model.getLineCount();
  let prevLine = 0;
  let prevChar = 0;

  for (const token of semantic) {
    const start = token.range.start;
    const end = token.range.end;

    // Monaco throws away the whole token set if any token overruns its line, which
    // happens whenever the editor model is momentarily ahead of the ty workspace.
    if (start.line < 1 || start.line > lineCount || end.line !== start.line) {
      continue;
    }
    const lineLength = model.getLineLength(start.line);
    if (start.column - 1 >= lineLength || end.column - 1 > lineLength) {
      continue;
    }

    const line = start.line - 1;
    const char = start.column - 1;
    const length = model.getValueLengthInRange(tyRangeToMonacoRange(token.range));

    if (length <= 0) {
      continue;
    }

    result.push(
      line - prevLine,
      prevLine === line ? char - prevChar : char,
      length,
      token.kind,
      token.modifiers,
    );

    prevLine = line;
    prevChar = char;
  }

  return { data: Uint32Array.from(result) };
}

function mapCompletionKind(kind: CompletionKind): CompletionItemKind {
  switch (kind) {
    case CompletionKind.Text:
      return CompletionItemKind.Text;
    case CompletionKind.Method:
      return CompletionItemKind.Method;
    case CompletionKind.Function:
      return CompletionItemKind.Function;
    case CompletionKind.Constructor:
      return CompletionItemKind.Constructor;
    case CompletionKind.Field:
      return CompletionItemKind.Field;
    case CompletionKind.Variable:
      return CompletionItemKind.Variable;
    case CompletionKind.Class:
      return CompletionItemKind.Class;
    case CompletionKind.Interface:
      return CompletionItemKind.Interface;
    case CompletionKind.Module:
      return CompletionItemKind.Module;
    case CompletionKind.Property:
      return CompletionItemKind.Property;
    case CompletionKind.Unit:
      return CompletionItemKind.Unit;
    case CompletionKind.Value:
      return CompletionItemKind.Value;
    case CompletionKind.Enum:
      return CompletionItemKind.Enum;
    case CompletionKind.Keyword:
      return CompletionItemKind.Keyword;
    case CompletionKind.Snippet:
      return CompletionItemKind.Snippet;
    case CompletionKind.Color:
      return CompletionItemKind.Color;
    case CompletionKind.File:
      return CompletionItemKind.File;
    case CompletionKind.Reference:
      return CompletionItemKind.Reference;
    case CompletionKind.Folder:
      return CompletionItemKind.Folder;
    case CompletionKind.EnumMember:
      return CompletionItemKind.EnumMember;
    case CompletionKind.Constant:
      return CompletionItemKind.Constant;
    case CompletionKind.Struct:
      return CompletionItemKind.Struct;
    case CompletionKind.Event:
      return CompletionItemKind.Event;
    case CompletionKind.Operator:
      return CompletionItemKind.Operator;
    case CompletionKind.TypeParameter:
      return CompletionItemKind.TypeParameter;
    default:
      return CompletionItemKind.Variable;
  }
}

function mapDocumentHighlightKind(
  kind: DocumentHighlightKind,
): languages.DocumentHighlightKind {
  switch (kind) {
    case DocumentHighlightKind.Read:
      return languages.DocumentHighlightKind.Read;
    case DocumentHighlightKind.Write:
      return languages.DocumentHighlightKind.Write;
    default:
      return languages.DocumentHighlightKind.Text;
  }
}
