import { createAnthropic } from '@ai-sdk/anthropic';
import type { IModelOptions, IProviderInfo } from '@jupyternaut/agent';

import {
  AzureConfigStore,
  FOUNDRY_ANTHROPIC_ID,
  createShapedFetch
} from './config';

/**
 * Foundry serves the Anthropic messages API at `/anthropic/v1/messages`, and
 * `@ai-sdk/anthropic` appends `/messages` to whatever base URL it is given, so
 * the `/v1` has to be part of it. Entering the resource's `/anthropic` root is
 * the obvious mistake: Azure answers the resulting path with a bare 404 that
 * carries no CORS headers, which a browser reports as a blocked cross-origin
 * request rather than as a wrong path.
 */
function foundryBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, '');
  return /\/anthropic$/.test(trimmed) ? `${trimmed}/v1` : trimmed;
}

/**
 * Builds the Azure AI Foundry Anthropic provider entry, Anthropic-native wire
 * format rather than the OpenAI-compatible shim.
 *
 * The wire format is not verified against a tenant. Everything the request
 * depends on is a setting: base URL, model, auth header name, whether the
 * Anthropic `x-api-key` header is dropped, the api version query parameter and
 * its value, the `anthropic-version` and `anthropic-beta` headers, plus free
 * extra headers, query parameters and a path rewrite.
 *
 * See the mutation note in azure-openai.ts for why the arrays are shared.
 */
export function createFoundryAnthropicProvider(
  store: AzureConfigStore
): IProviderInfo & { defaultModels: string[]; baseUrls: { url: string }[] } {
  const defaultModels: string[] = store.foundryAnthropicModels();
  const baseUrls: { url: string; description?: string }[] = [];

  return {
    id: FOUNDRY_ANTHROPIC_ID,
    name: 'Azure AI Foundry (Anthropic)',
    apiKeyRequirement: 'required',
    defaultModels,
    baseUrls,
    supportsBaseURL: true,
    supportsHeaders: true,
    supportsToolCalling: true,
    description:
      'Anthropic models on Azure AI Foundry over the Anthropic messages API. Base URL, model, auth header and api version come from the Azure AI Providers settings.',
    providerToolCapabilities: {
      webSearch: { implementation: 'anthropic' },
      webFetch: { implementation: 'anthropic' }
    },
    cacheProviderOptions: {
      anthropic: { cacheControl: { type: 'ephemeral' } }
    },
    factory: (options: IModelOptions) => {
      const cfg = store.foundryAnthropic;
      const baseURL = foundryBaseUrl(options.baseURL || cfg.baseUrl);
      if (!baseURL) {
        throw new Error(
          'Azure AI Foundry needs a base URL, for example https://<resource>.services.ai.azure.com/anthropic/v1'
        );
      }
      const model = options.model || cfg.model;
      if (!model) {
        throw new Error('Azure AI Foundry needs a model name');
      }
      const apiKey = options.apiKey ?? '';
      if (!apiKey) {
        throw new Error('API key required for Azure AI Foundry');
      }

      const headers: { [key: string]: string } = { ...cfg.headers };
      if (cfg.anthropicVersion) {
        headers['anthropic-version'] = cfg.anthropicVersion;
      }
      if (cfg.anthropicBeta.length) {
        headers['anthropic-beta'] = cfg.anthropicBeta.join(',');
      }
      if (cfg.allowDirectBrowserAccess) {
        headers['anthropic-dangerous-direct-browser-access'] = 'true';
      }

      const anthropic = createAnthropic({
        baseURL,
        apiKey,
        headers: { ...headers, ...(options.headers ?? {}) },
        fetch: createShapedFetch(
          () => store.foundryAnthropic,
          () => apiKey
        )
      });

      return anthropic(model);
    }
  };
}
