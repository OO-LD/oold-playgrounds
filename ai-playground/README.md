# AI schema agent playground

A JupyterLite page where an LLM turns a folder of CSV files into validated OO-LD schemas.
Deployed as `/ai` of the playgrounds site.

Almost nothing is here. The kernel API the agent calls, the Azure provider extension, the
vendored wheel set and the build itself live in
[`OO-LD/oold-jupyterlite-ai`](https://github.com/OO-LD/oold-jupyterlite-ai) and are
consumed from a sibling checkout, the same way the `oold` wheel is.

| File | What it decides |
| --- | --- |
| `oold-lite.toml` | which capabilities are on, where the brief comes from, which checkouts to build wheels from |
| `brief/framing.md` | what the agent is told it is for. Everything else in the prompt is composed from the package's capability fragments |
| `brief/closing.md` | how it should answer, read last |
| `contents/` | what is on the drive: the landing notebook and one sample CSV |
| `tests/run.mjs` | the site serves under a subpath, the kernel reaches idle, the chat plugin and the holoviews renderers are there |

The suites that test the agent, the kernel API and the git client are in the other repo,
because that is what they test.

## Build

```bash
python -m venv .venv
OOLD_AI_SRC=../../oold-jupyterlite-ai .venv/bin/python -m pip install -r requirements.txt
bash scripts/build.sh                     # leaves a site in _output/
```

`requirements.txt` installs the sibling checkout editable, so an edit there reaches the
next build with no reinstall. It pins nothing: one resolution in one repo decides the
Pyodide version the vendored wheel set is pinned against.

The extension is not built by pip. `scripts/build.sh` rebuilds it when its sources are
newer, which needs `npm ci` in `../../oold-jupyterlite-ai/extension` once.

## Test

```bash
python -m oold_jupyterlite_ai.cli serve --port 8902 --dir _output
node tests/run.mjs
```

`BASE` points the suite somewhere else, which is how the deployed subpath is checked:

```bash
BASE=http://127.0.0.1:8904/ai node tests/run.mjs
```

## Using your own key

Open **AI Settings** from the chat toolbar and add a provider. The system prompt is already
filled in; the site ships it as a settings override.

Keys go to the secrets manager, which in JupyterLite is memory only, so a reload loses
them. Turning off "Use the secrets manager to manage API keys" stores the key in browser
settings instead, in plain text, where it survives a reload.

For Azure AI Foundry the base URL has to end at `/anthropic/v1`, and `model` is the
deployment name rather than a dated Anthropic model id.
