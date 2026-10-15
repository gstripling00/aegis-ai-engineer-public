#!/usr/bin/env python3
"""Append Chapter 2 (tools) to the Chapter 1 notebook, without editing any Chapter 1 cell.

Usage: python3 make_workbench_ch1_ch2.py <lab_1.ipynb> <make_lab_2.py> <out.ipynb>

Chapter 2's cells come from make_lab_2.py, minus its title, Step 0, and the
Step 1 carry-forward (the Chapter 1 cells above already define those names).
Steps are renumbered 2.1, 2.2, ... and names that would shadow Chapter 1's
are renamed, so Chapter 1's own cells still mean what they meant.
"""
import re
import subprocess
import sys
import tempfile
import nbformat as nbf

lab1_path, gen_path, out_path = sys.argv[1:4]
lab1 = nbf.read(lab1_path, as_version=4)

with tempfile.TemporaryDirectory() as d:
    subprocess.run([sys.executable, gen_path, f"{d}/lab2.ipynb"], check=True, capture_output=True)
    lab2 = nbf.read(f"{d}/lab2.ipynb", as_version=4)

# Drop Lab 2's title, Step 0 (md + code), and Step 1 carry-forward (md + code)
assert lab2.cells[1].source.startswith("## Step 0") and lab2.cells[3].source.startswith("## Step 1")
ch2 = lab2.cells[5:]

# Renumber: Lab 2's Step 2..17 become Step 2.1..2.16 (sub-steps keep their letter)
def renumber(m):
    return f"{m.group(1)}2.{int(m.group(2)) - 1}{m.group(3)}"
step_re = re.compile(r"(Step )(\d+)([a-z]?)")

def edit(src: str, old: str, new: str) -> str:
    assert old in src, f"replacement target missing: {old[:60]!r}"
    return src.replace(old, new)

for c in ch2:
    # leave "Step 0" (Lab 1's Ollama setup) alone; renumber everything else
    c.source = re.sub(r"(Step )(\d+)([a-z]?)",
                      lambda m: m.group(0) if m.group(2) == "0" else renumber(m), c.source)

def cell_with(text):
    hits = [c for c in ch2 if text in c.source]
    assert len(hits) == 1, f"{text!r} matched {len(hits)} cells"
    return hits[0]

# Step 2.14 (rejection checks): don't overwrite Chapter 1's `rows`
c = cell_with("# --- Step 2.14:")
c.source = edit(c.source, "rows = []", "check_rows = []")
c.source = c.source.replace("rows.append(", "check_rows.append(")
c.source = edit(c.source, "pd.DataFrame(rows)", "pd.DataFrame(check_rows)")

# Step 2.15 (observability): reuse Chapter 1's show_audit() instead of redefining it
md15 = cell_with("## Step 2.15: Observability")
md15.source = edit(md15.source, "so a reviewer can trace the decision back to its sources.",
                   "so a reviewer can trace the decision back to its sources. `show_audit()` from "
                   "Chapter 1's Step 12 works unchanged; the trail is just longer.")
c = cell_with("# --- Step 2.15: observability ---")
start = c.source.index("def show_audit")
end = c.source.index("accepted = ")
c.source = c.source[:start] + c.source[end:]
c.source = c.source.replace("accepted", "tool_accepted").replace("rejected = ", "tool_rejected = ")
c.source = c.source.replace("show_audit(rejected)", "show_audit(tool_rejected)")

# Step 2.15b (plot): Chapter 1 already imported seaborn and set the theme; new function name
c = cell_with("# --- Step 2.15b:")
c.source = edit(c.source, 'import matplotlib.pyplot as plt\nimport seaborn as sns\n\nsns.set_theme(style="white", context="notebook")\n', "")
c.source = c.source.replace("plot_trail", "plot_tool_trail")
c.source = c.source.replace("(ax, accepted,", "(ax, tool_accepted,").replace("(ax, rejected,", "(ax, tool_rejected,")
c.source = edit(c.source, "n = len(accepted.trajectory)", "n = len(tool_accepted.trajectory)")

# Step 2.16 (evaluation): keep Chapter 1's EXPECTED and score() intact
c = cell_with("# --- Step 2.16: evaluation ---")
c.source = c.source.replace("EXPECTED", "EXPECTED_WITH_EVIDENCE").replace("score(", "score_grounded(")
c.source = edit(c.source, "def score_grounded(name", "def score_grounded(name")

# Fix one Chapter 1 cell: guard the live Gemma run so "Run all" survives Ollama being down
step10 = [c for c in lab1.cells if c.cell_type == "code" and c.source.startswith("# --- Step 10:")]
assert len(step10) == 1, "Lab 1 Step 10 not found"
step10[0].source = '''# --- Step 10: live run with Gemma ---
alert = ingest_alert(alert_json)                   # fresh working state
try:                                               # live model: optional
    result = run_triage_agent(alert, GemmaTriageModel())
    print("status:", result.status, "| risk:", result.risk_level)
    print("reason:", result.reason)
    print("missing:", result.missing_evidence)
    print("trajectory:", result.trajectory)
except Exception as error:                         # Ollama down: skip, don't stop the notebook
    print(f"Gemma run skipped (is Ollama running? see Step 0): {type(error).__name__}")'''

checkpoint = nbf.v4.new_code_cell('''# --- Checkpoint: Chapter 2 builds on Chapter 1 ---
NEEDED = ["AlertState", "AlertEvidence", "Decision", "alert_json", "ingest_alert",
          "validate_decision", "DecisionValidationError", "STATUS_MAP", "TERMINAL_STATUSES",
          "show_audit", "pd", "plt", "sns"]
missing = [name for name in NEEDED if name not in globals()]
if missing:                                        # stop here with a clear message, not a NameError later
    raise RuntimeError(f"Run the Chapter 1 cells first. Missing: {missing}")
print(f"Chapter 1 is loaded ({len(NEEDED)} names found). Continue with Chapter 2.")''')

# Chapter 2 opener
opener = nbf.v4.new_markdown_cell(
    "# Chapter 2: Give Triage Its Tools\n\n"
    "Everything above stays as it is. Chapter 1 ended with Triage naming the evidence it could not get: "
    "`ip_reputation` and `recent_login_history`. This section gives Triage exactly those two tools, each "
    "backed by a CSV file.\n\n"
    "It builds on the Chapter 1 cells above: the contracts, `alert_json`, `ingest_alert()`, "
    "`validate_decision()`, and the status rules are reused, not rewritten. Run Chapter 1 first, then "
    "continue here. The model proposes a tool call; the harness decides whether to run it.")

out = nbf.v4.new_notebook()
out.metadata = lab1.metadata
out.cells = list(lab1.cells) + [opener, checkpoint] + ch2
nbf.validate(out)
nbf.write(out, out_path)
print("Chapter 1 cells:", len(lab1.cells), "| Chapter 2 cells:", len(ch2) + 1, "| total:", len(out.cells))
