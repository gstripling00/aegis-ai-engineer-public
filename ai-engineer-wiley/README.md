# aegis-ai-engineer-public

Companion repository for *The Agentic AI Engineer's Handbook* and its Aegis labs.

## New here? Start with one of these

- **Run the labs:** open `notebooks/part1_one_agent.ipynb` in Google Colab (see "Run Part I in Colab" below). Nothing to install.
- **Try the Chapter 3 hash walkthrough:** [`docs/hash-walkthrough.md`](docs/hash-walkthrough.md) shows, in six terminal steps, how changing one character in an instruction file stops Aegis from starting. It explains how to get a terminal, Git, and a copy of this repository first.

## How the labs grow

- **Within a part, append.** Each part has one Workbench notebook. Each chapter adds a section to it and never edits earlier cells.
- **Between parts, promote.** At the end of a part, the stable code moves into the `aegis` package with tests, and the release is tagged. The next part's notebook installs that tagged release.

| Part | Notebook | Starts from |
|---|---|---|
| I: One Agent (Ch 1–7) | `notebooks/part1_one_agent.ipynb` | nothing |
| II: A Team of Agents (Ch 8–9) | `notebooks/part2_team.ipynb` | `aegis` v0.1 |
| III: Proving It (Ch 10–11) | `notebooks/part3_proving_it.ipynb` | `aegis` v0.2 |
| IV: Production (Ch 12–13) | `notebooks/part4_production.ipynb` | `aegis` v0.3 |

## Layout

```
data/         evidence the agent's tools call over the network (synthetic)
src/aegis/    the package (empty until Part I is promoted)
tests/        package tests
notebooks/    one Workbench notebook per part
tools/        generators for the notebooks and the evidence data
```

## The evidence data

`data/ip_reputation.csv` is a threat-intel feed snapshot and `data/login_history.csv` is an identity provider's sign-in log, 100 rows each. Chapter 2's tools fetch them from this repository over HTTPS, the way an agent calls any outside system. All IP addresses come from the ranges reserved for documentation (RFC 5737); none is a real address. `tools/make_evidence_csvs.py` regenerates both files byte for byte.

## Run Part I in Colab

1. Open `notebooks/part1_one_agent.ipynb` in Colab.
2. Run the cells in order. Step 0 installs Ollama and Gemma 4 E4B; skip it to use only the deterministic model.

The repository is public, so the evidence and config files load with no credential. If the data moves, change `EVIDENCE_REPO` or `EVIDENCE_REF` in Chapter 2's Step 2.1. For the book's release, pin `EVIDENCE_REF` to a tag so readers always get the files the chapters describe.

## Develop the package

```
pip install -r requirements.txt
pip install -e ".[dev]"
pytest
```
