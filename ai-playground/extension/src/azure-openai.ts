import { createAzure } from '@ai-sdk/azure';
import type { IModelOptions, IProviderInfo } from '@jupyternaut/agent';

import {
  AZURE_OPENAI_ID,
  AzureConfigStore,
  createShapedFetch
} from './config';

/**
 * Builds the Azure OpenAI provider entry.
 *
 * `defaultModels` and `baseUrls` are arrays the registry keeps by reference
 * after registerProvider shallow-copies the info object, and the registry has
 * no deregister call. Mutating them in place is the only way the dropdown
 * follows a settings change without a page reload.
 */
export function createAzureOpenAIProvider(
  store: AzureConfigStore
): IProviderInfo & { defaultModels: string[]; baseUrls: { url: string }[] } {
  const defaultModels: string[] = store.azureOpenAIModels();
  const baseUrls: { url: string; description?: string }[] = [];

  return {
    id: AZURE_OPENAI_ID,
    name: 'Azure OpenAI',
    apiKeyRequirement: 'required',
    defaultModels,
    baseUrls,
    supportsBaseURL: true,
    supportsHeaders: true,
    supportsToolCalling: true,
    description:
      'Azure OpenAI deployment. Base URL, deployment and api-version come from the Azure AI Providers settings.',
    providerToolCapabilities: {
      webSearch: { implementation: 'openai' }
    },
    factory: (options: IModelOptions) => {
      const cfg = store.azureOpenAI;
      const baseURL = options.baseURL || cfg.baseUrl;
      if (!baseURL) {
        throw new Error(
          'Azure OpenAI needs a base URL, for example https://<resource>.openai.azure.com/openai'
        );
      }
      const deployment = options.model || cfg.deployment;
      if (!deployment) {
        throw new Error('Azure OpenAI needs a deployment name');
      }
      const apiKey = options.apiKey ?? '';
      if (!apiKey) {
        throw new Error('API key required for Azure OpenAI');
      }

      const azure = createAzure({
        baseURL,
        apiKey,
        ...(cfg.apiVersion ? { apiVersion: cfg.apiVersion } : {}),
        useDeploymentBasedUrls: cfg.useDeploymentBasedUrls,
        headers: { ...cfg.headers, ...(options.headers ?? {}) },
        fetch: createShapedFetch(
          () => store.azureOpenAI,
          () => apiKey
        )
      });

      return cfg.apiStyle === 'chat'
        ? azure.chat(deployment)
        : azure.responses(deployment);
    }
  };
}
