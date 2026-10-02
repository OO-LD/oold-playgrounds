export const PLUGIN_ID = 'oold-azure-providers:plugin';

export const AZURE_OPENAI_ID = 'azure-openai';
export const FOUNDRY_ANTHROPIC_ID = 'azure-foundry-anthropic';

export type ShapedFetch = (
  input: RequestInfo | URL,
  init?: RequestInit
) => Promise<Response>;

/**
 * Parts of an outgoing request that a tenant may need to differ on. Nothing
 * here is baked into the bundle; all of it comes from the plugin settings.
 */
export interface IRequestShape {
  apiVersion: string;
  apiVersionParam: string;
  apiKeyHeader: string;
  dropDefaultAuthHeaders: boolean;
  headers: { [key: string]: string };
  query: { [key: string]: string };
  pathPattern: string;
  pathReplacement: string;
}

export interface IAzureOpenAIConfig extends IRequestShape {
  baseUrl: string;
  deployment: string;
  apiStyle: 'responses' | 'chat';
  useDeploymentBasedUrls: boolean;
  models: string[];
}

export interface IFoundryAnthropicConfig extends IRequestShape {
  baseUrl: string;
  model: string;
  anthropicVersion: string;
  anthropicBeta: string[];
  allowDirectBrowserAccess: boolean;
  models: string[];
}

export const DEFAULT_AZURE_OPENAI: IAzureOpenAIConfig = {
  baseUrl: '',
  deployment: '',
  apiStyle: 'responses',
  useDeploymentBasedUrls: true,
  models: [],
  apiVersion: '2024-10-21',
  apiVersionParam: 'api-version',
  apiKeyHeader: 'api-key',
  dropDefaultAuthHeaders: false,
  headers: {},
  query: {},
  pathPattern: '',
  pathReplacement: ''
};

export const DEFAULT_FOUNDRY_ANTHROPIC: IFoundryAnthropicConfig = {
  baseUrl: '',
  model: '',
  models: [],
  anthropicVersion: '2023-06-01',
  anthropicBeta: [],
  allowDirectBrowserAccess: true,
  apiVersion: '',
  apiVersionParam: 'api-version',
  apiKeyHeader: 'api-key',
  dropDefaultAuthHeaders: false,
  headers: {},
  query: {},
  pathPattern: '',
  pathReplacement: ''
};

function asRecord(value: unknown): { [key: string]: string } {
  const out: { [key: string]: string } = {};
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [k, v] of Object.entries(value as object)) {
      if (typeof v === 'string') {
        out[k] = v;
      }
    }
  }
  return out;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter(v => typeof v === 'string') : [];
}

function merge<T extends object>(defaults: T, raw: unknown): T {
  const out = { ...defaults } as { [key: string]: unknown };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return out as T;
  }
  for (const [key, fallback] of Object.entries(defaults)) {
    const value = (raw as { [key: string]: unknown })[key];
    if (value === undefined || value === null) {
      continue;
    }
    if (Array.isArray(fallback)) {
      out[key] = asStringList(value);
    } else if (typeof fallback === 'object') {
      out[key] = asRecord(value);
    } else if (typeof value === typeof fallback) {
      out[key] = value;
    }
  }
  return out as T;
}

/**
 * Holds the settings-derived configuration that the provider factories read on
 * every model creation, so a tenant change is a settings change.
 */
export class AzureConfigStore {
  get azureOpenAI(): IAzureOpenAIConfig {
    return this._azureOpenAI;
  }

  get foundryAnthropic(): IFoundryAnthropicConfig {
    return this._foundryAnthropic;
  }

  update(composite: unknown): void {
    const raw = (composite ?? {}) as { [key: string]: unknown };
    this._azureOpenAI = merge(DEFAULT_AZURE_OPENAI, raw.azureOpenAI);
    this._foundryAnthropic = merge(
      DEFAULT_FOUNDRY_ANTHROPIC,
      raw.azureFoundryAnthropic
    );
  }

  /**
   * Model list shown in the provider dropdown, configured deployment first.
   */
  azureOpenAIModels(): string[] {
    const cfg = this._azureOpenAI;
    return cfg.deployment && !cfg.models.includes(cfg.deployment)
      ? [cfg.deployment, ...cfg.models]
      : [...cfg.models];
  }

  foundryAnthropicModels(): string[] {
    const cfg = this._foundryAnthropic;
    return cfg.model && !cfg.models.includes(cfg.model)
      ? [cfg.model, ...cfg.models]
      : [...cfg.models];
  }

  private _azureOpenAI: IAzureOpenAIConfig = { ...DEFAULT_AZURE_OPENAI };
  private _foundryAnthropic: IFoundryAnthropicConfig = {
    ...DEFAULT_FOUNDRY_ANTHROPIC
  };
}

/**
 * Wraps globalThis.fetch and applies the configured request shape. The AI SDK
 * resolves the api version and the deployment path itself for Azure OpenAI;
 * for Foundry neither is known, so both come from here.
 */
export function createShapedFetch(
  getShape: () => IRequestShape,
  getApiKey: () => string
): ShapedFetch {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const shape = getShape();
    const href =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const url = new URL(href, globalThis.location?.href);

    if (shape.pathPattern) {
      url.pathname = url.pathname.replace(
        new RegExp(shape.pathPattern),
        shape.pathReplacement
      );
    }
    if (shape.apiVersion && shape.apiVersionParam) {
      if (!url.searchParams.has(shape.apiVersionParam)) {
        url.searchParams.set(shape.apiVersionParam, shape.apiVersion);
      }
    }
    for (const [key, value] of Object.entries(shape.query)) {
      url.searchParams.set(key, value);
    }

    const base =
      init?.headers ?? (input instanceof Request ? input.headers : undefined);
    const headers = new Headers(base as HeadersInit | undefined);
    if (shape.dropDefaultAuthHeaders) {
      headers.delete('x-api-key');
      headers.delete('authorization');
    }
    const apiKey = getApiKey();
    if (apiKey && shape.apiKeyHeader) {
      headers.set(shape.apiKeyHeader, apiKey);
    }
    for (const [key, value] of Object.entries(shape.headers)) {
      headers.set(key, value);
    }

    const request: RequestInit =
      input instanceof Request && !init
        ? {
            method: input.method,
            body: input.body,
            signal: input.signal,
            credentials: input.credentials,
            mode: input.mode
          }
        : { ...init };
    request.headers = headers;
    return globalThis.fetch(url.toString(), request);
  };
}
