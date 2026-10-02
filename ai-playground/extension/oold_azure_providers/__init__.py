"""Prebuilt JupyterLab extension registering Azure providers with jupyterlite-ai."""

__version__ = "0.1.0"


def _jupyter_labextension_paths():
    return [{"src": "../labextension", "dest": "oold-azure-providers"}]
