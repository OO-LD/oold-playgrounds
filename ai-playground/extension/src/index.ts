import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { IProviderRegistry } from '@jupyternaut/agent';

import {
  AZURE_OPENAI_ID,
  AzureConfigStore,
  FOUNDRY_ANTHROPIC_ID,
  PLUGIN_ID
} from './config';
import { createAzureOpenAIProvider } from './azure-openai';
import { createFoundryAnthropicProvider } from './foundry-anthropic';

export const DESCRIBE_COMMAND = 'oold-azure-providers:describe';

function syncList(target: string[], next: string[]): void {
  target.splice(0, target.length, ...next);
}

function syncBaseUrls(
  target: { url: string; description?: string }[],
  url: string,
  description: string
): void {
  target.splice(0, target.length);
  if (url) {
    target.push({ url, description });
  }
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID,
  description: 'Azure OpenAI and Azure AI Foundry Anthropic providers',
  autoStart: true,
  requires: [IProviderRegistry],
  optional: [ISettingRegistry],
  activate: async (
    app: JupyterFrontEnd,
    providerRegistry: IProviderRegistry,
    settingRegistry: ISettingRegistry | null
  ) => {
    const store = new AzureConfigStore();
    const azureOpenAI = createAzureOpenAIProvider(store);
    const foundryAnthropic = createFoundryAnthropicProvider(store);

    const refresh = (): void => {
      syncList(azureOpenAI.defaultModels, store.azureOpenAIModels());
      syncList(foundryAnthropic.defaultModels, store.foundryAnthropicModels());
      syncBaseUrls(
        azureOpenAI.baseUrls,
        store.azureOpenAI.baseUrl,
        'Configured Azure OpenAI endpoint'
      );
      syncBaseUrls(
        foundryAnthropic.baseUrls,
        store.foundryAnthropic.baseUrl,
        'Configured Azure AI Foundry endpoint'
      );
    };

    if (settingRegistry) {
      try {
        const settings = await settingRegistry.load(PLUGIN_ID);
        store.update(settings.composite);
        settings.changed.connect(() => {
          store.update(settings.composite);
          refresh();
        });
      } catch (reason) {
        console.warn(
          `${PLUGIN_ID}: settings not loaded, using defaults`,
          reason
        );
      }
    }
    refresh();

    for (const info of [azureOpenAI, foundryAnthropic]) {
      if (providerRegistry.getProviderInfo(info.id)) {
        console.warn(`${PLUGIN_ID}: provider ${info.id} is already registered`);
        continue;
      }
      providerRegistry.registerProvider(info);
    }

    app.commands.addCommand(DESCRIBE_COMMAND, {
      label: 'Describe Azure AI providers',
      execute: () => ({
        pluginId: PLUGIN_ID,
        registered: [AZURE_OPENAI_ID, FOUNDRY_ANTHROPIC_ID].filter(
          id => providerRegistry.getProviderInfo(id) !== null
        ),
        available: providerRegistry.getAvailableProviders(),
        config: {
          azureOpenAI: store.azureOpenAI,
          azureFoundryAnthropic: store.foundryAnthropic
        }
      })
    });
  }
};

export default plugin;
