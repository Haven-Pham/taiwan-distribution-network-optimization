"""Executes the analysis notebook in place and refreshes its outputs, figures and tables.

    python scripts/run_notebook.py

The notebook reads the results of scripts/run_experiments.py, which are committed, so this takes about a minute.
"""
from pathlib import Path

import nbformat
from nbclient import NotebookClient

NOTEBOOK = Path(__file__).resolve().parent.parent / "notebooks" / "taiwan_network_design_routing.ipynb"

nb = nbformat.read(NOTEBOOK, as_version=4)
NotebookClient(nb, timeout=1800, kernel_name="python3",
               resources={"metadata": {"path": str(NOTEBOOK.parent)}}).execute()
for cell in nb.cells:
    cell.metadata.pop("execution", None)
nbformat.write(nb, NOTEBOOK)
print(f"Executed {NOTEBOOK.name}")
