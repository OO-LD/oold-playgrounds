# oold-azure-providers

JupyterLab 4 extension that registers two providers with the AI provider registry
`jupyterlite-ai` 0.20.1 ships, so Azure OpenAI and Anthropic models on Azure AI Foundry
are reachable from the browser without a gateway.

## What the installed 0.20.1 actually exposes

The published provider docs say `IProviderRegistry` comes from the chat extension. It
does not. In 0.20.1:

| claim | installed 0.20.1 |
| --- | --- |
| token imported from `@jupyterlite/ai` | token is `IProviderRegistry` from `@jupyternaut/agent`, token string `@jupyternaut/agent:IProviderRegistry` |
| provided by the chat plugin | provided by `@jupyternaut/persona:provider-registry` |
| `providerInfo.fetchModels` | no such field on `IProviderInfo` |
| shipped providers include OpenRouter | shipped ids are `anthropic`, `google`, `mistral`, `openai`, `generic` |

`registerProvider(info: IProviderInfo): void` matches the docs, with two constraints the
docs do not mention: it throws on a duplicate id, and there is no deregister call.

`IProviderInfo` in 0.20.1: `id`, `name`, `apiKeyRequirement`, `defaultModels`,
`modelInfo?`, `supportsBaseURL?`, `supportsHeaders?`, `supportsToolCalling?`,
`description?`, `baseUrls?`, `providerToolCapabilities?`, `cacheProviderOptions?`,
`factory`.

`factory` receives `IModelOptions` = `{ provider, model?, apiKey?, headers?, baseURL? }`
and returns a Vercel AI SDK `LanguageModel`. `AgentManager._createModel` only fills
`provider`, `model`, `apiKey` and `baseURL`, so `headers` never reaches a factory in
practice even though `supportsHeaders` is advertised. Everything Azure-specific
therefore comes from this extension's own settings.

## Settings

Plugin id `oold-azure-providers:plugin`, schema in `schema/plugin.json`, two sections:

- `azureOpenAI`: `baseUrl`, `deployment`, `apiVersion`, `apiVersionParam`, `apiStyle`
  (`responses` or `chat`), `useDeploymentBasedUrls`, `apiKeyHeader`,
  `dropDefaultAuthHeaders`, `models`, `headers`, `query`, `pathPattern`,
  `pathReplacement`.
- `azureFoundryAnthropic`: the same request-shape keys plus `model`,
  `anthropicVersion`, `anthropicBeta`, `allowDirectBrowserAccess`.

`baseURL` and `model` set on the provider in the AI settings panel win over the plugin
settings; everything else only exists here.

## Foundry wire format, assumed

Not verified against a tenant. Each assumption is a setting:

| assumption | setting | default |
| --- | --- | --- |
| Anthropic-native messages API under a base path | `azureFoundryAnthropic.baseUrl` | empty, suggested `https://<resource>.services.ai.azure.com/anthropic/v1` |
| API key goes in `api-key`, not `x-api-key` | `apiKeyHeader` | `api-key` |
| the Anthropic SDK's `x-api-key` may stay | `dropDefaultAuthHeaders` | `false` |
| an `api-version` query parameter may be required | `apiVersion`, `apiVersionParam` | empty, `api-version` |
| `anthropic-version: 2023-06-01` still applies | `anthropicVersion` | `2023-06-01` |
| CORS needs the direct browser access opt-in | `allowDirectBrowserAccess` | `true` |
| the path the SDK builds may need rewriting | `pathPattern`, `pathReplacement` | empty |

Anything still missing can go through `headers` and `query`.

## Build

```bash
npm install
npx tsc
../.venv/Scripts/jupyter-builder.exe build .
```

`tsc` compiles `src/` to `lib/`, `jupyter-builder` writes the federated bundle to
`labextension/`. The builder is the `jupyter-builder` console script from the Python
package of the same name, which the playground venv already carries as a jupyterlab
dependency; the npm `@jupyter/builder` package it drives publishes no bin, and
`jupyter labextension build` only shells out to that same console script.

`labextension/` is committed so `pip install ./extension` needs no node toolchain.
Rebuild and commit it after any change under `src/` or `schema/`.

## Registry mutation

`ProviderRegistry.registerProvider` stores `{ ...info }`, a shallow copy, and the
registry has no deregister call. `defaultModels` and `baseUrls` are therefore shared
array instances that this extension rewrites in place when the settings change, so the
model dropdown follows a settings change without a page reload.

## Introspection

The plugin adds the command `oold-azure-providers:describe`, which returns the plugin
id, the registered provider ids, the full provider list and the resolved configuration.
`tests/providers.mjs` drives it.
