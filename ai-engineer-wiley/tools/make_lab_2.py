#!/usr/bin/env python3
"""Build lab_2.ipynb to match Lab 1's structure and style.

Triage gets exactly the two tools Chapter 1 asked for, each backed by a CSV.
Function calling only: no LangGraph, no MCP. Gemma 4 E4B through Ollama is the
live model; a deterministic model makes every other cell repeatable.
"""
import sys
import nbformat as nbf

cells = []
def md(t): cells.append(nbf.v4.new_markdown_cell(t))
def code(src, tags=None):
    c = nbf.v4.new_code_cell(src)
    if tags:
        c.metadata["tags"] = tags
    cells.append(c)

OLLAMA = ["needs_ollama"]

md("""# Chapter 2 Lab: Give Triage Its Tools

Chapter 1 ended with Triage naming the evidence it could not get: `ip_reputation` and `recent_login_history`. In this lab you give Triage exactly those two tools, each backed by a CSV file that lives in a separate repository and is fetched over the network. The model proposes a tool call; the harness decides whether to run it. Run the cells in order.""")

# ---------------------------------------------------------------- 0
md("""## Step 0: Start Gemma (Colab)

Same as Lab 1. Skip this step to use only the deterministic model.""")
code('''# --- Step 0: install Ollama and pull Gemma (Colab) ---
import shutil, subprocess, time, urllib.request

!apt-get -qq update && apt-get -qq install -y zstd > /dev/null   # the Ollama installer needs zstd to unpack
!curl -fsSL https://ollama.com/install.sh | sh                    # install the Ollama server
!pip -q install ollama pydantic                                    # Python client + contracts library

if shutil.which("ollama") is None:                                 # stop with a clear message, not a traceback
    raise RuntimeError("Ollama did not install. Read the installer output above.")

subprocess.Popen(["ollama", "serve"],                              # start the server in the background
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(30):                                                # wait until it answers
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=2)
        break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError("The Ollama server did not start within 30 seconds.")

!ollama pull gemma4:e4b                                            # download the model (first run only)''', tags=OLLAMA)

# ---------------------------------------------------------------- 1
md("""## Step 1: Bring forward Chapter 1

The contracts, instructions, front door, validation, and status rules from Lab 1, unchanged. Chapter 2 builds on them; it does not edit them.""")
code('''# --- Step 1: Chapter 1, unchanged ---
from typing import Protocol
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ALLOWED_STATUSES = {"CLOSE", "ESCALATE", "NEEDS_INVESTIGATION"}
ALLOWED_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

class AlertState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    alert_id: str
    alert_type: str
    user_id: str
    source_ip: str
    failed_attempts: int
    successful_attempts: int
    time_window_minutes: int
    device_known: bool
    status: str = "NEW"
    risk_level: str | None = None
    reason: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    trajectory: list[dict] = Field(default_factory=list)

class AlertEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    alert_id: str
    alert_type: str
    user_id: str
    source_ip: str
    failed_attempts: int
    successful_attempts: int
    time_window_minutes: int
    device_known: bool

class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    alert_id: str
    status: str
    risk_level: str
    reason: str
    missing_evidence: list[str]

alert_json = {
    "alert_id": "ALT-1001",
    "alert_type": "multiple_login_attempts",
    "user_id": "userid123",
    "source_ip": "198.51.100.4",
    "failed_attempts": 12,
    "successful_attempts": 1,
    "time_window_minutes": 6,
    "device_known": False,
}

def ingest_alert(raw: dict) -> AlertState:
    evidence = AlertEvidence.model_validate(raw)
    return AlertState(**evidence.model_dump())

class DecisionValidationError(ValueError):
    pass

def validate_decision(decision: Decision, alert: AlertState) -> None:
    if decision.alert_id != alert.alert_id:
        raise DecisionValidationError("Decision references the wrong alert.")
    if decision.status not in ALLOWED_STATUSES:
        raise DecisionValidationError(f"Unsupported status: {decision.status}")
    if decision.risk_level not in ALLOWED_RISK_LEVELS:
        raise DecisionValidationError(f"Unsupported risk level: {decision.risk_level}")
    if not decision.reason.strip():
        raise DecisionValidationError("A grounded reason is required.")
    if decision.status == "NEEDS_INVESTIGATION" and not decision.missing_evidence:
        raise DecisionValidationError("Missing evidence must be identified.")

TERMINAL_STATUSES = {"CLOSED", "ESCALATED", "NEEDS_INVESTIGATION", "FAILED"}
STATUS_MAP = {"CLOSE": "CLOSED", "ESCALATE": "ESCALATED",
              "NEEDS_INVESTIGATION": "NEEDS_INVESTIGATION"}

print("Chapter 1 loaded. Alert", ingest_alert(alert_json).alert_id, "starts at", ingest_alert(alert_json).status)''')

# ---------------------------------------------------------------- 2
md("""## Step 2: Connect to the evidence source

The evidence is not in this notebook. It lives in a repository the SOC's tooling team owns: a threat-intel feed (`ip_reputation.csv`) and the identity provider's sign-in log (`login_history.csv`). Triage reaches them the way it would reach any outside system: over the network, with a timeout, a size limit, and no guarantee the answer is well formed.

Three things make this an external call rather than a file read:

- **Someone else owns the data.** This notebook reads it and never writes it.
- **It crosses a network boundary**, so it can be slow, refuse you, or fail.
- **The response is untrusted.** `fetch_csv()` checks its size and columns before anything uses it.

The repository is public, so this lab needs no credential. A production source would: a token the harness holds, scoped to read-only access, and never shown to the model. If the data moves, change `EVIDENCE_REPO` or `EVIDENCE_REF`; no tool code changes.""")
code('''# --- Step 2: the evidence source ---
import csv, io, os
import urllib.error, urllib.request
import pandas as pd

EVIDENCE_REPO = "gstripling00/aegis-ai-engineer-public"     # who owns the data
EVIDENCE_REF = "main"                                # branch, tag, or commit; pin a tag before publishing
EVIDENCE_URL = os.environ.get(                       # override only to use a mirror or a local copy
    "AEGIS_EVIDENCE_URL",
    f"https://raw.githubusercontent.com/{EVIDENCE_REPO}/{EVIDENCE_REF}/data")
TIMEOUT_SECONDS = 10                                 # an outside system gets a deadline
MAX_BYTES = 2_000_000                                # and a size limit

class EvidenceSourceError(RuntimeError):
    pass

def fetch_csv(name: str, required_columns: set[str]) -> list[dict]:
    """One external call: time-limited, size-limited, and checked before use."""
    # a public repo needs no credential; a production source adds one here, held by the harness
    request = urllib.request.Request(f"{EVIDENCE_URL}/{name}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise EvidenceSourceError(f"{name}: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise EvidenceSourceError(f"{name}: unreachable ({error})") from error
    if len(body) > MAX_BYTES:
        raise EvidenceSourceError(f"{name}: response larger than {MAX_BYTES} bytes")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8"))))
    missing = required_columns - set(rows[0] if rows else {})
    if missing:                                      # the shape changed: refuse it, don't guess
        raise EvidenceSourceError(f"{name}: missing columns {sorted(missing)}")
    return rows

print("Evidence source:", f"{EVIDENCE_REPO}@{EVIDENCE_REF}")
feed = fetch_csv("ip_reputation.csv", {"ip_address", "reputation"})
log = fetch_csv("login_history.csv", {"user_id", "timestamp", "outcome"})
print(f"ip_reputation.csv: {len(feed)} rows | login_history.csv: {len(log)} rows")
display(pd.DataFrame(log).head(3))''')

# ---------------------------------------------------------------- 3
md("""## Step 3: Define the tool contracts

Two new contracts. A `ToolCall` is what the model may ask for. An `Observation` is what the harness returns, and the harness, not the model, fills in where it came from.""")
code('''# --- Step 3: tool contracts ---
class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str                                 # which tool
    arguments: dict[str, str]                 # its arguments, all strings

class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str                          # harness-assigned, cited by the decision
    tool: str
    arguments: dict[str, str]
    source: str                               # where the records came from
    records: list[dict]

print(ToolCall(name="ip_reputation", arguments={"ip": "198.51.100.4"}))''')

# ---------------------------------------------------------------- 4
md("""## Step 4: Build the two tools

Each tool makes one call to the evidence source, keeps only the rows for the requested key, caps how many it returns, and keeps only the fields Triage needs. The sign-in log has 22 columns, including email addresses; Triage gets 10. Passing less is cheaper, and it keeps data the model has no use for out of its context.

The tools are read-only: nothing here can change the source.

**In the enterprise:** a real threat-intel or identity API filters on the server and returns only the matching rows. A static file can't, so this tool downloads the whole log and filters it here. The model never sees another user's rows, but the harness briefly holds them. In production, scope is enforced by the service as well as the client.""")
code('''# --- Step 4: two read-only tools ---
MAX_ROWS = 20                                 # no tool returns an unbounded result

IP_FIELDS = ["ip_address", "reputation", "risk_score", "confidence_score", "threat_category",
             "threat_types", "last_seen", "is_tor", "is_proxy", "is_hosting", "abuse_reports"]
LOGIN_FIELDS = ["timestamp", "source_ip", "outcome", "failure_reason", "mfa_result",
                "device_id", "device_managed", "new_device", "country_code", "correlation_id"]

def keep(row: dict, fields: list[str]) -> dict:
    return {f: row[f] for f in fields}

def ip_reputation(ip: str) -> Observation:
    rows = [keep(r, IP_FIELDS) for r in fetch_csv("ip_reputation.csv", set(IP_FIELDS))
            if r["ip_address"] == ip]
    return Observation(evidence_id=f"E-IP-{ip}", tool="ip_reputation", arguments={"ip": ip},
                       source=f"{EVIDENCE_REPO}@{EVIDENCE_REF}:data/ip_reputation.csv",
                       records=rows[:MAX_ROWS])

def recent_login_history(user_id: str) -> Observation:
    rows = [keep(r, LOGIN_FIELDS) for r in fetch_csv("login_history.csv", set(LOGIN_FIELDS) | {"user_id"})
            if r["user_id"] == user_id]
    rows.sort(key=lambda r: r["timestamp"], reverse=True)   # newest first
    return Observation(evidence_id=f"E-LOGIN-{user_id}", tool="recent_login_history",
                       arguments={"user_id": user_id},
                       source=f"{EVIDENCE_REPO}@{EVIDENCE_REF}:data/login_history.csv",
                       records=rows[:MAX_ROWS])

ip_obs = ip_reputation("198.51.100.4")
print(ip_obs.records[0]["reputation"], "| risk", ip_obs.records[0]["risk_score"], "|", ip_obs.records[0]["threat_category"])
login_obs = recent_login_history("userid123")
print(len(login_obs.records), "sign-ins for userid123, newest first:")
display(pd.DataFrame(login_obs.records).head(4))''')

# ---------------------------------------------------------------- 5
md("""## Step 5: Register the tools and describe them to the model

`TOOLS` is what the harness may run. `TOOL_SCHEMAS` is what the model sees: names, descriptions, and argument types, in the function-calling format Ollama accepts. The model gets the catalog, never the functions.""")
code('''# --- Step 5: registry + schemas ---
TOOLS = {"ip_reputation": ip_reputation,
         "recent_login_history": recent_login_history}

# which argument each tool takes, and which alert field it must match
TOOL_SCOPE = {"ip_reputation": ("ip", "source_ip"),
              "recent_login_history": ("user_id", "user_id")}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "ip_reputation",
        "description": "Look up threat-intel reputation for the alert's source IP.",
        "parameters": {"type": "object",
                       "properties": {"ip": {"type": "string"}},
                       "required": ["ip"]}}},
    {"type": "function", "function": {
        "name": "recent_login_history",
        "description": "Get recent sign-ins for the alert's user, newest first.",
        "parameters": {"type": "object",
                       "properties": {"user_id": {"type": "string"}},
                       "required": ["user_id"]}}},
]

print("registered:", sorted(TOOLS))
print("schemas match registry:", sorted(TOOLS) == sorted(s["function"]["name"] for s in TOOL_SCHEMAS))''')

# ---------------------------------------------------------------- 6
md("""## Step 6: Validate every tool call before it runs

Pydantic checks the shape. These checks enforce what a schema cannot: the tool exists, it gets exactly the argument it takes, and that argument belongs to the current alert. A well-formed request for some other IP is still rejected.""")
code('''# --- Step 6: tool-call validation ---
class ToolCallError(ValueError):
    pass

def validate_tool_call(call: ToolCall, alert: AlertState) -> None:
    if call.name not in TOOLS:                                   # registered?
        raise ToolCallError(f"Unknown tool: {call.name}")
    arg, alert_field = TOOL_SCOPE[call.name]
    if set(call.arguments) != {arg}:                             # exactly the right argument?
        raise ToolCallError(f"{call.name} takes only '{arg}', got {sorted(call.arguments)}")
    if call.arguments[arg] != getattr(alert, alert_field):       # scoped to this alert?
        raise ToolCallError(f"Out of scope: {arg}={call.arguments[arg]}")

alert = ingest_alert(alert_json)
for call in [ToolCall(name="ip_reputation", arguments={"ip": "198.51.100.4"}),
             ToolCall(name="ip_reputation", arguments={"ip": "198.51.100.7"}),
             ToolCall(name="recent_login_history", arguments={"user_id": "userid118"}),
             ToolCall(name="block_ip", arguments={"ip": "198.51.100.4"})]:
    try:
        validate_tool_call(call, alert)
        print("accepted:", call.name, call.arguments)
    except ToolCallError as error:
        print("rejected:", error)''')

# ---------------------------------------------------------------- 7
md("""## Step 7: Execute, and fail closed

A tool can fail: the source is unreachable, refuses the request, or returns something malformed. `execute_tool()` turns any failure into a `ToolCallError`, so the harness records it and stops instead of crashing.""")
code('''# --- Step 7: execution ---
def execute_tool(call: ToolCall) -> Observation:
    try:
        return TOOLS[call.name](**call.arguments)
    except (EvidenceSourceError, OSError, KeyError, csv.Error) as error:
        raise ToolCallError(f"{call.name} failed: {type(error).__name__}") from error

obs = execute_tool(ToolCall(name="recent_login_history", arguments={"user_id": "userid123"}))
print(obs.evidence_id, "from", obs.source, f"({len(obs.records)} rows)")''')

# ---------------------------------------------------------------- 8
md("""## Step 8: Extend the context, the decision, and the instructions

Three Chapter 1 assets grow, and each grows by addition:

- The context package now carries the tool catalog, the observations so far, and how many model calls remain.
- The decision adds `evidence_ids`: the observations it rests on.
- The standing instructions get a new version that tells Triage to gather evidence first.""")
code('''# --- Step 8: context v2, decision v2, instructions v2 ---
class ToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instructions: str
    alert: AlertEvidence
    allowed_statuses: list[str]
    allowed_risk_levels: list[str]
    available_tools: list[str]
    observations: list[Observation]
    calls_remaining: int

class GroundedDecision(Decision):
    evidence_ids: list[str]                   # which observations support this decision

SYSTEM_INSTRUCTIONS_V2 = """You are Aegis, a SOC alert-triage agent.
Evaluate the current alert using only the supplied evidence and tool results.
Do not invent missing facts.
Before deciding, use the available tools to retrieve evidence the alert does not contain.
A successful login following repeated failures increases risk.
An unknown device increases risk.
A source IP with a malicious reputation increases risk.
If required evidence is still missing, return NEEDS_INVESTIGATION.
Return exactly one structured decision with:
alert_id, status, risk_level, reason, missing_evidence, and evidence_ids.
List in evidence_ids the evidence_id of every observation your decision relies on."""

def build_tool_context(alert: AlertState, observations: list[Observation], calls_remaining: int) -> ToolContext:
    return ToolContext(
        instructions=SYSTEM_INSTRUCTIONS_V2,
        alert=AlertEvidence(**alert.model_dump(include=set(AlertEvidence.model_fields))),
        allowed_statuses=sorted(ALLOWED_STATUSES),
        allowed_risk_levels=sorted(ALLOWED_RISK_LEVELS),
        available_tools=sorted(TOOLS),
        observations=list(observations),
        calls_remaining=calls_remaining,
    )

print(build_tool_context(ingest_alert(alert_json), [], 4).model_dump_json(indent=2, exclude={"instructions"}))''')

# ---------------------------------------------------------------- 9
md("""## Step 9: Validate the grounded decision

Everything Chapter 1 checked, plus one rule: a decision may only cite evidence the harness actually retrieved.""")
code('''# --- Step 9: decision validation v2 ---
def validate_grounded_decision(decision: GroundedDecision, alert: AlertState,
                               observations: list[Observation]) -> None:
    validate_decision(decision, alert)                          # every Chapter 1 rule still applies
    retrieved = {o.evidence_id for o in observations}
    invented = [e for e in decision.evidence_ids if e not in retrieved]
    if invented:
        raise DecisionValidationError(f"Cites evidence never retrieved: {invented}")

claim = GroundedDecision(alert_id="ALT-1001", status="CLOSE", risk_level="LOW",
                         reason="The IP is clean.", missing_evidence=[],
                         evidence_ids=["E-IP-198.51.100.4"])
try:
    validate_grounded_decision(claim, ingest_alert(alert_json), observations=[])
except DecisionValidationError as error:
    print("rejected:", error)''')

# ---------------------------------------------------------------- 10
md("""## Step 10: Update the model seam

In Chapter 1 the model returned one decision. Now each call returns one step: either a tool request or a final decision.""")
code('''# --- Step 10: the model seam v2 ---
class ToolUsingModel(Protocol):
    def next_step(self, context: ToolContext) -> ToolCall | GroundedDecision:
        """Request one tool, or return the final decision."""
        ...''')

# ---------------------------------------------------------------- 11
md("""## Step 11: Two models that fit the seam

A deterministic model for tests, and Gemma for live runs. Both ask for each missing piece of evidence, then decide from what came back.""")
code('''# --- Step 11a: deterministic model (no network, same answer every run) ---
class DeterministicToolModel:
    def next_step(self, context: ToolContext) -> ToolCall | GroundedDecision:
        alert = context.alert
        have = {o.tool for o in context.observations}
        if "ip_reputation" not in have:                          # evidence first
            return ToolCall(name="ip_reputation", arguments={"ip": alert.source_ip})
        if "recent_login_history" not in have:
            return ToolCall(name="recent_login_history", arguments={"user_id": alert.user_id})

        ip = next(o for o in context.observations if o.tool == "ip_reputation")
        logins = next(o for o in context.observations if o.tool == "recent_login_history")
        malicious = any(r["reputation"] == "MALICIOUS" for r in ip.records)
        new_device_success = any(r["outcome"] == "SUCCESS" and r["new_device"] == "true"
                                 for r in logins.records)
        cited = [o.evidence_id for o in context.observations]

        if malicious and new_device_success:                     # decide from the evidence
            return GroundedDecision(
                alert_id=alert.alert_id, status="ESCALATE", risk_level="HIGH",
                reason="The source IP is known for credential stuffing, and the account "
                       "signed in from it on an unknown device.",
                missing_evidence=[], evidence_ids=cited)
        return GroundedDecision(
            alert_id=alert.alert_id, status="NEEDS_INVESTIGATION", risk_level="MEDIUM",
            reason="The retrieved evidence does not settle the alert.",
            missing_evidence=["analyst_review"], evidence_ids=cited)''')

code('''# --- Step 11b: Gemma through Ollama (live model, same seam) ---
import json
import ollama

class GemmaToolModel:
    def __init__(self, model_name: str = "gemma4:e4b", client=None):
        self.client = client or ollama.Client()
        self.model_name = model_name

    def _messages(self, context: ToolContext) -> list[dict]:
        messages = [
            {"role": "system", "content": context.instructions},
            {"role": "user", "content": context.model_dump_json(exclude={"instructions", "observations"})},
        ]
        for o in context.observations:                           # replay each call and its result
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"function": {"name": o.tool, "arguments": o.arguments}}]})
            messages.append({"role": "tool", "tool_name": o.tool,
                             "content": o.model_dump_json(exclude={"arguments"})})
        return messages

    def next_step(self, context: ToolContext) -> ToolCall | GroundedDecision:
        messages = self._messages(context)
        if context.calls_remaining > 1:                          # leave the last call for the decision
            response = self.client.chat(model=self.model_name, messages=messages,
                                        tools=TOOL_SCHEMAS, options={"temperature": 0})
            if response.message.tool_calls:                      # a request, not an action
                fn = response.message.tool_calls[0].function
                return ToolCall(name=fn.name, arguments={k: str(v) for k, v in fn.arguments.items()})
        messages.append({"role": "user", "content": "Return your final decision now."})
        response = self.client.chat(model=self.model_name, messages=messages,
                                    format=GroundedDecision.model_json_schema(),
                                    options={"temperature": 0})
        return GroundedDecision.model_validate_json(response.message.content)''')

# ---------------------------------------------------------------- 12
md("""## Step 12: The bounded tool loop

Each pass: one model call. If the model asks for a tool, the harness validates the request, runs it, and records the observation. If the model returns a decision, the harness validates it and records it. Any rejection, tool failure, or exhausted budget records `FAILED` and stops.""")
code('''# --- Step 12: the harness v2 ---
MAX_MODEL_CALLS = 4                           # two lookups, one decision, one spare

def fail(alert: AlertState, reason: str) -> AlertState:
    alert.status = "FAILED"
    alert.reason = f"Run stopped: {reason}"
    alert.trajectory.append({"event": "halt", "outcome": "rejected", "reason": reason})
    return alert

def run_triage_agent_with_tools(alert: AlertState, model: ToolUsingModel,
                                max_calls: int = MAX_MODEL_CALLS) -> AlertState:
    if alert.status in TERMINAL_STATUSES:
        raise RuntimeError("This alert has already reached a terminal state.")
    observations: list[Observation] = []

    for number in range(1, max_calls + 1):
        context = build_tool_context(alert, observations, max_calls - number + 1)
        alert.trajectory.append({"event": "model_call", "number": number})
        try:
            step = model.next_step(context)                      # the model proposes...
            if isinstance(step, ToolCall):
                validate_tool_call(step, alert)                  # ...the harness checks...
                observation = execute_tool(step)                 # ...and runs it
                observations.append(observation)
                alert.trajectory.append({"event": "tool_call", "tool": step.name,
                                         "evidence_id": observation.evidence_id,
                                         "rows": len(observation.records)})
                continue
            validate_grounded_decision(step, alert, observations)
        except (ValidationError, DecisionValidationError, ToolCallError, ValueError) as error:
            return fail(alert, str(error))

        alert.status = STATUS_MAP[step.status]                   # accepted: only the harness writes
        alert.risk_level = step.risk_level
        alert.reason = step.reason
        alert.missing_evidence = list(step.missing_evidence)
        alert.trajectory.append({"event": "halt", "outcome": "completed", "status": alert.status,
                                 "evidence_ids": list(step.evidence_ids)})
        return alert

    return fail(alert, f"no decision within {max_calls} model calls")''')

# ---------------------------------------------------------------- 13
md("""## Step 13: Run Aegis with the deterministic model

In Chapter 1 this alert stopped at `NEEDS_INVESTIGATION`. With evidence, the same alert escalates, and the decision names what it rests on.""")
code('''# --- Step 13: deterministic run ---
alert = ingest_alert(alert_json)
result = run_triage_agent_with_tools(alert, DeterministicToolModel())

print("status:", result.status, "| risk:", result.risk_level)
print("reason:", result.reason)
print("missing:", result.missing_evidence)
for event in result.trajectory:
    print("  ", event)''')

# ---------------------------------------------------------------- 14
md("""## Step 14: Run Aegis with Gemma

The same harness with a live model. Gemma chooses which tools to call and when to stop; the harness still decides what runs and what is recorded.""")
code('''# --- Step 14: live run with Gemma ---
try:  # live model: optional
    alert = ingest_alert(alert_json)
    result = run_triage_agent_with_tools(alert, GemmaToolModel())

    print("status:", result.status, "| risk:", result.risk_level)
    print("reason:", result.reason)
    for event in result.trajectory:
        print("  ", event)
except Exception as error:  # Ollama down: skip, don't stop the notebook
    print(f"Gemma run skipped (is Ollama running? see Step 0): {type(error).__name__}")''', tags=OLLAMA)

# ---------------------------------------------------------------- 15
md("""## Step 15: Prove the harness rejects bad requests

Each scripted model misbehaves in one way. Every run must stop with `FAILED`, and the reason must name the problem.""")
code('''# --- Step 15: rejected-request checks ---
class ScriptedModel:
    """Returns a fixed sequence of steps, whatever the context says."""
    def __init__(self, *steps):
        self.steps = list(steps)
    def next_step(self, context: ToolContext) -> ToolCall | GroundedDecision:
        return self.steps.pop(0)

def ask(name, **arguments):
    return ToolCall(name=name, arguments=arguments)

CHECKS = [
    ("unknown tool",               ScriptedModel(ask("block_ip", ip="198.51.100.4")),           "Unknown tool"),
    ("another IP",                 ScriptedModel(ask("ip_reputation", ip="198.51.100.7")),      "Out of scope"),
    ("another user's sign-ins",    ScriptedModel(ask("recent_login_history", user_id="userid118")), "Out of scope"),
    ("extra argument",             ScriptedModel(ask("ip_reputation", ip="198.51.100.4", limit="999")), "takes only"),
    ("cites evidence it never got", ScriptedModel(claim),                                       "never retrieved"),
    ("never decides",              ScriptedModel(*[ask("ip_reputation", ip="198.51.100.4")] * 5), "no decision within"),
]

rows = []
for label, model, expected in CHECKS:
    r = run_triage_agent_with_tools(ingest_alert(alert_json), model)
    rows.append({"check": label, "status": r.status, "reason": r.trajectory[-1]["reason"],
                 "passed": r.status == "FAILED" and expected in r.trajectory[-1]["reason"]})

saved_url, EVIDENCE_URL = EVIDENCE_URL, EVIDENCE_URL + "/moved"   # simulate the source going away
r = run_triage_agent_with_tools(ingest_alert(alert_json), DeterministicToolModel())
EVIDENCE_URL = saved_url
rows.append({"check": "evidence source down", "status": r.status, "reason": r.trajectory[-1]["reason"],
             "passed": r.status == "FAILED" and "failed" in r.trajectory[-1]["reason"]})

checks = pd.DataFrame(rows).set_index("check")
display(checks)
assert checks["passed"].all(), "A control failed: check the rows marked False."''')

# ---------------------------------------------------------------- 16
md("""## Step 16: Observability — read the audit trail

The trail now shows every tool the harness ran and the evidence each one produced, so a reviewer can trace the decision back to its sources.""")
code('''# --- Step 16: observability ---
def show_audit(alert: AlertState) -> pd.DataFrame:
    events = pd.DataFrame(alert.trajectory, dtype=object).fillna("")
    events.index = range(1, len(events) + 1)
    events.index.name = "step"
    print(f"Alert {alert.alert_id}: final status {alert.status}, risk {alert.risk_level}")
    print(f"Reason: {alert.reason}")
    return events

accepted = run_triage_agent_with_tools(ingest_alert(alert_json), DeterministicToolModel())
rejected = run_triage_agent_with_tools(ingest_alert(alert_json),
                                       ScriptedModel(ask("ip_reputation", ip="198.51.100.7")))

print("ACCEPTED RUN")
display(show_audit(accepted))
print("\\nREJECTED RUN")
display(show_audit(rejected))

print("\\nAUDIT RECORD (JSON)")
print(json.dumps({"alert_id": accepted.alert_id, "status": accepted.status,
                  "trajectory": accepted.trajectory}, indent=2))''')

md("""### Step 16b: See the audit trail

The same two trails as a picture. Open circles are model calls, squares are tool runs, the filled marker is where the run stopped, and an X marks a rejection.""")
code('''# --- Step 16b: plot the audit trail ---
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="white", context="notebook")
INK, MID = "#222222", "#7a7a7a"                    # grays: the print book is black and white

def plot_trail(ax, alert, row, label):
    events = alert.trajectory
    xs = list(range(1, len(events) + 1))
    ax.plot(xs, [row] * len(events), color=MID, lw=2, zorder=1)
    for x, e in zip(xs, events):
        stop = e["event"] == "halt"
        rejected = e.get("outcome") == "rejected"
        marker = "X" if rejected else "s" if e["event"] == "tool_call" else "o"
        ax.scatter(x, row, s=180, marker=marker, color=INK if stop else "white",
                   edgecolor=INK, linewidth=1.5, zorder=2)
        if e["event"] == "tool_call":
            text = e["tool"].replace("_", "_\\n", 1)
        elif stop:
            text = f'halt\\n{e.get("status", "rejected")}'
        else:
            text = f'model\\ncall {e["number"]}'
        ax.annotate(text, (x, row), xytext=(0, 14), textcoords="offset points", ha="center", fontsize=8)
    ax.text(0.4, row, label, ha="right", va="center", fontsize=10, weight="bold")

fig, ax = plt.subplots(figsize=(9, 3))
plot_trail(ax, accepted, 1, "accepted")
plot_trail(ax, rejected, 0, "rejected")
n = len(accepted.trajectory)
ax.set(xlim=(-0.6, n + 0.6), ylim=(-0.5, 1.9), xticks=range(1, n + 1),
       xticklabels=[f"event {i}" for i in range(1, n + 1)], yticks=[])
sns.despine(left=True)
ax.set_title("Audit trail for two runs of ALT-1001", loc="left", fontsize=11)
plt.tight_layout()
plt.show()''')

# ---------------------------------------------------------------- 17
md("""## Step 17: Evaluation — did the tools change the answer?

With the evidence in hand, an analyst would escalate ALT-1001 at high risk. This scorecard checks each run against that, and checks that the decision cites its evidence and stayed within budget. The Gemma row runs only if the Ollama server from Step 0 is up.""")
code('''# --- Step 17: evaluation ---
EXPECTED = {"status": "ESCALATED", "risk_level": "HIGH"}   # what an analyst expects, given the evidence

def score(name: str, alert: AlertState) -> dict:
    halt = alert.trajectory[-1]
    return {
        "run": name,
        "status": alert.status,
        "status correct": alert.status == EXPECTED["status"],
        "risk correct": alert.risk_level == EXPECTED["risk_level"],
        "evidence cited": bool(halt.get("evidence_ids")),
        "within budget": sum(e["event"] == "model_call" for e in alert.trajectory) <= MAX_MODEL_CALLS,
        "stopped cleanly": halt["event"] == "halt",
    }

scores = [score("deterministic", run_triage_agent_with_tools(ingest_alert(alert_json), DeterministicToolModel()))]
try:
    scores.append(score("gemma", run_triage_agent_with_tools(ingest_alert(alert_json), GemmaToolModel())))
except Exception as error:
    print(f"Gemma row skipped (is Ollama running?): {type(error).__name__}")

print("DECISION SCORECARD")
display(pd.DataFrame(scores).set_index("run"))''')

md("""### Step 17b: See the scorecard

The scorecard and the rejection checks as one picture, labeled PASS or FAIL so it reads the same in black and white.""")
code('''# --- Step 17b: plot the scorecard ---
import textwrap

card = pd.DataFrame(scores).set_index("run")[["status correct", "risk correct", "evidence cited",
                                              "within budget", "stopped cleanly"]]
card.columns = [c.replace(" ", "\\n") for c in card.columns]
controls = checks[["passed"]].T.rename(index={"passed": "controls"})
controls.columns = [textwrap.fill(c, 11) for c in controls.columns]   # wrap long labels

fig, axes = plt.subplots(1, 2, figsize=(13, 2.4 + 0.4 * len(card)), gridspec_kw={"width_ratios": [5, 7]})
for ax, data, title in [(axes[0], card, "Decision vs. expected (ALT-1001)"),
                        (axes[1], controls, "Rejection checks")]:
    sns.heatmap(data.astype(int), ax=ax, cmap=["#3a3a3a", "#e6e6e6"], vmin=0, vmax=1, cbar=False,
                annot=data.replace({True: "PASS", False: "FAIL"}), fmt="",
                linewidths=2, linecolor="white", annot_kws={"fontsize": 10, "weight": "bold"})
    ax.set_title(title, loc="left", fontsize=11)
    ax.set(xlabel="", ylabel="")
    ax.tick_params(axis="x", rotation=0, labelsize=8)
    ax.tick_params(axis="y", rotation=0)
plt.tight_layout()
plt.show()''')

md("""## Where this leaves Aegis

Triage now fetches the evidence it asked for, and its decision cites what it used. The harness still owns every boundary: which tools exist, what they may be asked, and what gets recorded.

But the standing instructions are now on their second version, and they live as a string in a notebook cell. Nothing records who changed them, nothing tests them on their own, and nothing stops a third version from quietly dropping a rule. That is where Chapter 3 begins.""")

nb = nbf.v4.new_notebook()
nb.cells = cells
nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
nb.metadata["language_info"] = {"name": "python"}
out = sys.argv[1] if len(sys.argv) > 1 else "lab_2.ipynb"
nbf.write(nb, out)
print("wrote", out, len(cells), "cells")
