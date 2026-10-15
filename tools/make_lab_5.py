#!/usr/bin/env python3
"""Append Chapter 5 (conversational slots: collecting missing input) to the Part I notebook.

Usage: python3 make_lab_5.py <part1_one_agent.ipynb> <repo root> <out.ipynb>

Nothing above Chapter 5 is edited. The approved hash for triage@5.0.0 is computed
from the repo's config file; if you edit it, rerun this script (the lab's review step).
"""
import hashlib
import sys
from pathlib import Path

import nbformat as nbf

src, repo, out = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
nb = nbf.read(src, as_version=4)
assert any("# Chapter 4" in c.source for c in nb.cells), "Run make_lab_4.py first"
assert not any("# Chapter 5" in c.source for c in nb.cells), "Chapter 5 is already in this notebook"
V5_SHA = hashlib.sha256((repo / "config/instructions/triage-5.0.0.md").read_bytes()).hexdigest()

cells = []
md = lambda t: cells.append(nbf.v4.new_markdown_cell(t))
def code(t, tags=None):
    c = nbf.v4.new_code_cell(t)
    if tags:
        c.metadata["tags"] = tags
    cells.append(c)

md("""# Chapter 5: Conversational Slots — Collecting Missing Input

Every alert so far arrived complete. This section handles one that doesn't: a help-desk report, in a user's own words, with no source IP and no counts. Triage fills what it needs in a fixed order: from the report's own words, then from systems, and only then by asking a person, one question at a time. The run pauses while it waits, and resumes when the answer arrives.

Everything above is reused, not rewritten. Run Chapters 1 to 4 first.""")

code('''# --- Checkpoint: Chapter 5 builds on Chapters 1 to 4 ---
NEEDED = ["run_triage_agent_v4", "recall_observation", "execute_tool_cached", "remember_decision",
          "build_memory", "ToolCache", "ANALYSTS", "IncidentRecord", "iso", "UTC", "WHEN",
          "FIELD_RULES", "REQUIRED_EVIDENCE", "HARNESS_CONTROLS", "REQUIRED_CONTROLS",
          "APPROVED_INSTRUCTIONS", "load_instructions", "check_instructions", "SKILLS",
          "DeterministicToolModel", "GemmaToolModelV4", "score_grounded", "show_audit"]
missing = [name for name in NEEDED if name not in globals()]
if missing:
    raise RuntimeError(f"Run the Chapter 1 to 4 cells first. Missing: {missing}")
print(f"Chapters 1 to 4 are loaded ({len(NEEDED)} names found). Continue with Chapter 5.")''')

# ---------------------------------------------------------------- 5.1
md("""## Step 5.1: The report, and the slots it must fill

A help-desk report has three trusted fields and one untrusted one. The report ID, the user, and the time come from the help-desk system, which authenticated the user before they could submit. The text is whatever the user typed.

A **slot** is one fact Triage needs before it can decide. Each slot says where its value may come from, in order of preference, what values it may take, and, if a person must be asked, exactly what to ask them.""")
code('''# --- Step 5.1: reports and slots ---
class HelpDeskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    report_id: str = Field(pattern=r"^HD-\\d{4}$")
    user_id: str = Field(pattern=r"^[a-z0-9]{1,32}$")  # from the help-desk sign-in
    received_at: str = Field(pattern=WHEN_PATTERN)
    text: str = Field(max_length=500)  # the user's own words: untrusted

class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    sources: list[str]  # where a value may come from, in order of preference
    choices: list[str] | None = None  # allowed values, for report, user, and analyst slots
    question: str | None = None  # a reviewed template, never written by a model
    ask_if: tuple[str, str] | None = None  # ask only when (slot, value) holds
    skip_if: tuple[str, str] | None = None  # don't ask when (slot, value) holds

YES_NO = ["yes", "no", "not_sure"]
MFA_REPORT_SLOTS = [
    Slot(name="source_ip", sources=["system"]),
    Slot(name="attempt_at", sources=["system"]),
    Slot(name="failed_attempts", sources=["system"]),
    Slot(name="device_known", sources=["system"]),
    Slot(name="user_initiated", sources=["report", "user"], choices=YES_NO,
         question="Were you trying to sign in at {attempt_at} UTC?"),
    Slot(name="password_exposed", sources=["user"], choices=YES_NO,
         skip_if=("user_initiated", "yes"),
         question="In the last few days, did you type your password into a page you "
                  "reached from an email or a text message?"),
    Slot(name="verified_by_analyst", sources=["analyst"],
         choices=["verified", "not_verified"], ask_if=("user_initiated", "yes"),
         question="{user_id} says they started the sign-in at {attempt_at} UTC. Did you "
                  "confirm that through a separate channel, such as a call to the number "
                  "on file?"),
]

report = HelpDeskReport(report_id="HD-2207", user_id="userid122", received_at="2026-09-15T09:05:00Z",
                        text="I just got an MFA push on my phone that I didn't ask for. I think "
                             "I got one yesterday too. Is my account OK?")
print(report.report_id, "from", report.user_id, "at", report.received_at)
print(f"{len(MFA_REPORT_SLOTS)} slots:", [s.name for s in MFA_REPORT_SLOTS])''')

# ---------------------------------------------------------------- 5.2
md("""## Step 5.2: The intake state, and its run states

`IntakeState` is the working state for collecting input, separate from the `AlertState` it will eventually produce. Each filled slot records its value and where the value came from. The run states are the point:

- `COLLECTING`: Triage is filling slots it can fill without asking anyone.
- `WAITING_FOR_HUMAN`: one question is out, to one person, and the run is paused.
- `READY`: every slot that can be filled has been; the alert can be built.

`WAITING_FOR_HUMAN` is a run state, not a triage decision. Nothing has been decided yet.""")
code('''# --- Step 5.2: intake state ---
MAX_QUESTIONS = 2  # a person's time is a budget too
QUESTION_TTL_MINUTES = 30  # an unanswered question expires

class SlotValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str
    source: str  # report, system, user:<id>, or analyst:<id>

class IntakeState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    report: HelpDeskReport
    status: str = "COLLECTING"  # COLLECTING, WAITING_FOR_HUMAN, READY
    filled: dict[str, SlotValue] = Field(default_factory=dict)
    unanswered: list[str] = Field(default_factory=list)
    pending: str | None = None  # the one slot a question is out for
    pending_to: str | None = None  # who must answer it
    asked_at: str | None = None
    questions_asked: int = 0
    trail: list[dict] = Field(default_factory=list)

    def fill(self, slot: str, value: str, source: str) -> None:
        self.filled = {**self.filled, slot: SlotValue(value=value, source=source)}
        self.trail.append({"event": "filled", "slot": slot, "source": source})

intake = IntakeState(report=report)
print(intake.status, "| filled:", list(intake.filled), "| pending:", intake.pending)''')

# ---------------------------------------------------------------- 5.3
md("""## Step 5.3: Extract before you ask

The report already answers one question: the user says the push was one they "didn't ask for." Asking them again would waste their time and teach them that the help desk doesn't read.

Extraction is a model task, so it follows the rule Aegis has followed since Chapter 1: the extractor proposes, the harness validates. Each proposal names a slot, a value from that slot's choices, and the exact words in the report that support it. The harness accepts a proposal only if the slot may be filled from the report, the value is allowed, and the quote really appears in the text. Only the text may confirm.""")
code('''# --- Step 5.3: extraction, validated ---
class SlotProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    slot: str
    value: str
    quote: str = Field(min_length=3, max_length=120)  # the report's own words

class ReportExtractor(Protocol):
    def extract(self, report: HelpDeskReport, slots: list[Slot]) -> list[SlotProposal]: ...

class DeterministicExtractor:
    """Phrase rules for tests. Same seam as the Gemma extractor below."""
    RULES = [("didn't ask for", "user_initiated", "no"),
             ("did not ask for", "user_initiated", "no"),
             ("it was me", "user_initiated", "yes"),
             ("that was me", "user_initiated", "yes")]

    def extract(self, report, slots):
        text = report.text.casefold()
        return [SlotProposal(slot=slot, value=value, quote=phrase)
                for phrase, slot, value in self.RULES if phrase in text]

def accept_proposals(intake: IntakeState, proposals: list[SlotProposal],
                     slots: list[Slot]) -> list[str]:
    spec, rejected = {s.name: s for s in slots}, []
    for p in proposals:
        slot = spec.get(p.slot)
        if slot is None or "report" not in slot.sources:
            rejected.append(f"{p.slot}: not fillable from the report")
        elif p.value not in (slot.choices or []):
            rejected.append(f"{p.slot}: {p.value!r} is not an allowed value")
        elif p.quote.casefold() not in intake.report.text.casefold():
            rejected.append(f"{p.slot}: quote not found in the report")  # text confirms
        elif p.slot not in intake.filled:
            intake.fill(p.slot, p.value, "report")
    return rejected

intake = IntakeState(report=report)
print("rejected:", accept_proposals(intake, DeterministicExtractor().extract(report, MFA_REPORT_SLOTS),
                                    MFA_REPORT_SLOTS) or "none")
print("filled from the report:", {k: v.value for k, v in intake.filled.items()})

made_up = [SlotProposal(slot="password_exposed", value="no", quote="I never share my password"),
           SlotProposal(slot="user_initiated", value="yes", quote="it was definitely me")]
print("invented proposals:", accept_proposals(IntakeState(report=report), made_up, MFA_REPORT_SLOTS))''')

code('''# --- Step 5.3b: the same seam with Gemma (structured output, then the same validation) ---
class GemmaExtractor:
    def __init__(self, model_name: str = "gemma4:e4b", client=None):
        self.client = client or ollama.Client()
        self.model_name = model_name

    def extract(self, report, slots):
        fillable = {s.name: s.choices for s in slots if "report" in s.sources}
        prompt = ("Extract slot values from the report. Use only these slots and values: "
                  f"{json.dumps(fillable)}. For each value, quote the exact words that support it. "
                  "Return an empty list if the report doesn't say.")
        schema = {"type": "object", "required": ["proposals"],
                  "properties": {"proposals": {"type": "array",
                                               "items": SlotProposal.model_json_schema()}}}
        response = self.client.chat(model=self.model_name, format=schema, options={"temperature": 0},
                                    messages=[{"role": "system", "content": prompt},
                                              {"role": "user", "content": report.text}])
        found = json.loads(response.message.content)["proposals"]
        return [SlotProposal.model_validate(p) for p in found]  # shape checked; meaning next''',
     tags=["needs_ollama"])

# ---------------------------------------------------------------- 5.4
md("""## Step 5.4: Fill from systems before you ask people

The user can't tell Triage the source IP. They never saw it. The identity provider can. `fill_from_systems()` reads the user's sign-in history through Chapter 2's tool, Chapter 3's checks, and Chapter 4's cache, and finds the MFA denial closest before the report, within the last hour. If there is none, there is nothing to triage, and intake says so.""")
code('''# --- Step 5.4: system slots ---
LOOKBACK_MINUTES = 60

def parse_when(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

def fill_from_systems(intake: IntakeState, cache: ToolCache, now: datetime) -> None:
    user = intake.report.user_id
    call = ToolCall(name="recent_login_history", arguments={"user_id": user})
    history, _ = execute_tool_cached(call, cache, now)  # checked, as every observation is
    received = parse_when(intake.report.received_at)
    since = received - timedelta(minutes=LOOKBACK_MINUTES)
    window = [r for r in history.records
              if r["mfa_result"] == "DENIED"
              and since <= parse_when(r["timestamp"]) <= received]
    intake.trail.append({"event": "system_lookup", "evidence_id": history.evidence_id,
                         "matches": len(window)})
    if not window:
        raise ValueError(f"No MFA denial for {user} in the last {LOOKBACK_MINUTES} minutes")
    latest = max(window, key=lambda r: r["timestamp"])
    intake.fill("source_ip", latest["source_ip"], "system")
    intake.fill("attempt_at", latest["timestamp"], "system")
    intake.fill("failed_attempts", str(len(window)), "system")
    known = "false" if latest["new_device"] == "true" else "true"
    intake.fill("device_known", known, "system")

intake_cache = ToolCache()
fill_from_systems(intake, intake_cache, parse_when(report.received_at))
print({k: f"{v.value} ({v.source})" for k, v in intake.filled.items()})''')

# ---------------------------------------------------------------- 5.5
md("""## Step 5.5: Ask one question, to the right person, then pause

`next_slot()` picks the first slot that still needs a person, skipping any whose conditions say not to ask. `ask_person()` sends exactly one question, from a reviewed template, to the one audience that can answer it, and pauses the run. The question goes to an outbox; in production that is the help-desk ticket or the analyst's queue.

Questions are never written by a model. A model that can phrase questions can be steered into asking for a password.""")
code('''# --- Step 5.5: one question at a time ---
OUTBOX: list[dict] = []  # stands in for the ticket thread and the analyst queue

def wanted(slot: Slot, intake: IntakeState) -> bool:
    values = {k: v.value for k, v in intake.filled.items()}
    if slot.ask_if and values.get(slot.ask_if[0]) != slot.ask_if[1]:
        return False
    if slot.skip_if and values.get(slot.skip_if[0]) == slot.skip_if[1]:
        return False
    return True

def next_slot(intake: IntakeState, slots: list[Slot]) -> Slot | None:
    for slot in slots:
        people = {"user", "analyst"} & set(slot.sources)
        if (slot.name not in intake.filled and slot.name not in intake.unanswered
                and people and wanted(slot, intake)):
            return slot
    return None

def ask_person(intake: IntakeState, slot: Slot, now: datetime) -> None:
    audience = "user" if "user" in slot.sources else "analyst"
    to = f"user:{intake.report.user_id}" if audience == "user" else "analyst:queue"
    values = {k: v.value for k, v in intake.filled.items()}
    values["user_id"] = intake.report.user_id
    OUTBOX.append({"to": to, "report_id": intake.report.report_id, "slot": slot.name,
                   "question": slot.question.format(**values), "choices": slot.choices})
    intake.pending, intake.pending_to, intake.asked_at = slot.name, audience, iso(now)
    intake.questions_asked += 1
    intake.status = "WAITING_FOR_HUMAN"
    intake.trail.append({"event": "asked", "slot": slot.name, "to": to})

slot = next_slot(intake, MFA_REPORT_SLOTS)
ask_person(intake, slot, parse_when(report.received_at) + timedelta(minutes=1))
print(intake.status, "| pending:", intake.pending)
print(OUTBOX[-1])''')

# ---------------------------------------------------------------- 5.6
md("""## Step 5.6: Accept an answer only from the person who was asked

An answer is untrusted input, like everything else that arrives from outside. `answer()` accepts it only if it answers the question that is actually pending, comes from the audience that was asked (the reporting user for a user question, a known analyst for an analyst question), is one of the offered choices, and arrives before the question expires.

The answer records who gave it. A user's answer is a claim from an account that might be compromised; an analyst's is a verification. Step 5.9 uses the difference.""")
code('''# --- Step 5.6: answers ---
class AnswerError(ValueError):
    pass

def answer(intake: IntakeState, slot_name: str, value: str, respondent: str,
           slots: list[Slot], now: datetime) -> None:
    if intake.status != "WAITING_FOR_HUMAN" or intake.pending != slot_name:
        raise AnswerError(f"No question is pending for {slot_name}")
    if now - parse_when(intake.asked_at) > timedelta(minutes=QUESTION_TTL_MINUTES):
        raise AnswerError(f"The question for {slot_name} has expired")
    kind, _, who = respondent.partition(":")
    if intake.pending_to == "user" and respondent != f"user:{intake.report.user_id}":
        raise AnswerError(f"Only {intake.report.user_id} can answer this question")
    if intake.pending_to == "analyst" and not (kind == "analyst" and who in ANALYSTS):
        raise AnswerError("Only a SOC analyst can answer this question")
    slot = {s.name: s for s in slots}[slot_name]
    if value not in (slot.choices or []):
        raise AnswerError(f"{value!r} is not one of {slot.choices}")
    intake.fill(slot_name, value, respondent)
    intake.pending = intake.pending_to = intake.asked_at = None
    intake.status = "COLLECTING"

def expire(intake: IntakeState, now: datetime) -> bool:
    """Give up on a question nobody answered in time."""
    if intake.pending and now - parse_when(intake.asked_at) > timedelta(minutes=QUESTION_TTL_MINUTES):
        intake.unanswered = [*intake.unanswered, intake.pending]
        intake.trail.append({"event": "expired", "slot": intake.pending})
        intake.pending = intake.pending_to = intake.asked_at = None
        intake.status = "COLLECTING"
        return True
    return False

asked_at = parse_when(intake.asked_at)
for slot_name, value, who in [("password_exposed", "yes", "user:userid999"),
                              ("password_exposed", "maybe?", "user:userid122"),
                              ("user_initiated", "yes", "user:userid122")]:
    try:
        answer(intake, slot_name, value, who, MFA_REPORT_SLOTS, asked_at + timedelta(minutes=4))
    except AnswerError as error:
        print("refused:", error)''')

# ---------------------------------------------------------------- 5.7
md("""## Step 5.7: The intake loop: fill, ask, pause, resume

`advance()` runs intake as far as it can go without a person: extract once, fill from systems once, then either send the next question and pause, or finish. Call it again after every answer, or on a timer to expire questions nobody answered. Every call leaves the intake in one of the three run states.""")
code('''# --- Step 5.7: advance until a person is needed ---
def advance(intake: IntakeState, extractor: ReportExtractor, slots: list[Slot],
            cache: ToolCache, now: datetime) -> IntakeState:
    if intake.status == "READY":
        return intake
    expire(intake, now)
    if intake.status == "WAITING_FOR_HUMAN":
        return intake  # still waiting: nothing to do
    done = {e["event"] for e in intake.trail}
    if "extracted" not in done:
        rejected = accept_proposals(intake, extractor.extract(intake.report, slots), slots)
        intake.trail.append({"event": "extracted", "rejected": len(rejected)})
    if "system_lookup" not in done:
        fill_from_systems(intake, cache, now)
    slot = next_slot(intake, slots)
    if slot is not None and intake.questions_asked < MAX_QUESTIONS:
        ask_person(intake, slot, now)
        return intake
    if slot is not None:
        intake.unanswered = [*intake.unanswered, slot.name]  # out of questions
    intake.status = "READY"
    intake.trail.append({"event": "ready", "unanswered": list(intake.unanswered)})
    return intake''')

# ---------------------------------------------------------------- 5.8
md("""## Step 5.8: From a finished intake to an alert

A `READY` intake becomes two things. First, an ordinary alert, `ALT-1003`, built only from slots: the same `AlertEvidence` shape Chapter 1 defined, so everything downstream works unchanged. Second, an observation, `E-INTAKE-HD-2207`, that carries the people's answers into the context with their sources, through the same checks as evidence.

The report's free text goes into neither. Triage decides from structured slots, never from the user's words.""")
code('''# --- Step 5.8: intake -> alert + observation ---
REQUIRED_EVIDENCE["reported_mfa_prompt"] = {"ip_reputation", "recent_login_history"}
FIELD_RULES.update({
    "slot": r"[a-z_]{1,32}", "value": r"[A-Za-z0-9_.:-]{1,40}",
    "source": r"report|system|user:[a-z0-9]{1,32}|analyst:[a-z0-9.-]{1,32}",
})

def to_alert(intake: IntakeState, alert_id: str) -> tuple[dict, Observation]:
    if intake.status != "READY":
        raise RuntimeError(f"{intake.report.report_id} is {intake.status}, not READY")
    v = {k: s.value for k, s in intake.filled.items()}
    alert = {"alert_id": alert_id, "alert_type": "reported_mfa_prompt",
             "user_id": intake.report.user_id, "source_ip": v["source_ip"],
             "failed_attempts": int(v["failed_attempts"]), "successful_attempts": 0,
             "time_window_minutes": LOOKBACK_MINUTES,
             "device_known": v["device_known"] == "true"}
    people = [{"slot": k, "value": s.value, "source": s.source}
              for k, s in intake.filled.items() if s.source != "system"]
    people += [{"slot": k, "value": "unanswered", "source": "system"}
               for k in intake.unanswered]
    report_id = intake.report.report_id
    return alert, Observation(evidence_id=f"E-INTAKE-{report_id}", tool="intake",
                              arguments={"report_id": report_id},
                              source="aegis-intake:helpdesk", records=people)''')

# ---------------------------------------------------------------- 5.9
md("""## Step 5.9: Who may lower the risk? Rule R12

A user's answer is a claim, and the account it comes from may be the one under attack. If an attacker controls the mailbox or the chat session, the easiest way to make the alert go away is to answer "yes, that was me." So answers are trusted asymmetrically: a user's answer can raise risk, but closing an alert on a user's word requires an analyst to have verified it.

That is rule R12, and it is a control: `validate_claim_trust()`. It ships in `triage@5.0.0`, approved through Chapter 3's process.""")
code(f'''# --- Step 5.9: rule R12 and triage@5.0.0 ---
def validate_claim_trust(decision: GroundedDecision,
                         observations: list[Observation]) -> None:
    answers = {{r["slot"]: r for o in observations if o.tool == "intake" for r in o.records}}
    claim = answers.get("user_initiated", {{}})
    verified = answers.get("verified_by_analyst", {{}}).get("value") == "verified"
    source = claim.get("source", "")
    from_user = source == "report" or source.startswith("user:")  # the report is the user too
    if decision.status == "CLOSE" and claim.get("value") == "yes" and from_user \\
            and not verified:
        raise DecisionValidationError("Closing on a user's claim needs analyst verification")

HARNESS_CONTROLS["validate_claim_trust"] = validate_claim_trust
REQUIRED_CONTROLS["R12"] = "validate_claim_trust"  # every later version must keep R12
APPROVED_INSTRUCTIONS["triage@5.0.0"] = "{V5_SHA}"

triage_v5 = approved(load_instructions("instructions/triage-5.0.0.md"))
print(f"{{triage_v5.name}}@{{triage_v5.version}} | tests:", check_instructions(triage_v5) or "no problems")
print("triage@4.0.0 now:", check_instructions(triage_v4))''')

# ---------------------------------------------------------------- 5.10
md("""## Step 5.10: The harness, version 5, and a model that reads answers

Chapter 4's harness with two changes: the intake observation joins the context right after memory, and every decision must pass rule R12. `DeterministicIntakeModel` extends Chapter 2's deterministic model with what the people said, and `GemmaToolModelV5` shows Gemma the answers as supplied context, not as a tool it called.""")
code('''# --- Step 5.10: harness v5 ---
class DeterministicIntakeModel(DeterministicToolModel):
    def next_step(self, context: ToolContextV3) -> ToolCall | GroundedDecision:
        step = super().next_step(context)  # gather evidence first, as before
        if isinstance(step, ToolCall) or context.alert.alert_type != "reported_mfa_prompt":
            return step
        said = {r["slot"]: r["value"] for o in context.observations if o.tool == "intake"
                for r in o.records}
        ip = next(o for o in context.observations if o.tool == "ip_reputation")
        risk = "HIGH" if any(r["reputation"] == "MALICIOUS" for r in ip.records) else "MEDIUM"
        decide = lambda status, risk, reason, missing=(): GroundedDecision(
            **{**step.model_dump(), "status": status, "risk_level": risk, "reason": reason,
               "missing_evidence": list(missing)})
        if said.get("password_exposed") == "yes":
            return decide("ESCALATE", "HIGH", "The user typed their password into a linked page; "
                          "only MFA stopped a sign-in from a new device.")
        if said.get("user_initiated") == "yes" and said.get("verified_by_analyst") == "verified":
            return decide("CLOSE", "LOW", "The user started the sign-in, and an analyst verified it.")
        if said.get("user_initiated") == "yes":
            return decide("NEEDS_INVESTIGATION", risk, "The user says they started the sign-in, "
                          "but no analyst has verified it.", ["verified_by_analyst"])
        return decide("ESCALATE", risk, "Someone other than the user started a sign-in that "
                      "reached MFA, so they had the password.")

class GemmaToolModelV5(GemmaToolModelV3):
    """Memory and answers are context the harness supplied, not tools Gemma called."""
    SUPPLIED = {"incident_memory": "Prior incidents (context, not evidence about this alert)",
                "intake": "Answers collected from people (with who gave each one)"}

    def _messages(self, context: ToolContextV3) -> list[dict]:
        supplied = [o for o in context.observations if o.tool in self.SUPPLIED]
        evidence = [o for o in context.observations if o.tool not in self.SUPPLIED]
        messages = super()._messages(context.model_copy(update={"observations": evidence}))
        for o in supplied:
            messages[1]["content"] += (f"\\n{self.SUPPLIED[o.tool]}: "
                                       + o.model_dump_json(exclude={"arguments"}))
        return messages

def run_triage_agent_v5(alert: AlertState, model: ToolUsingModel, inst: InstructionSet,
                        skills: list[Skill], memory: IncidentMemory, cache: ToolCache,
                        now: datetime, intake_obs: Observation,
                        max_calls: int = MAX_MODEL_CALLS) -> AlertState:
    if alert.status in TERMINAL_STATUSES:
        raise RuntimeError("This alert has already reached a terminal state.")
    approved(inst)
    approved_tools(TOOL_SCHEMAS)
    skill = select_skill(alert, skills)
    alert.trajectory.append({"event": "config", **stamp(inst, skill), "model": model_id(model)})
    recalled = recall_observation(alert, memory, now)
    observations: list[Observation] = [recalled, intake_obs]  # (1) answers join the context
    for obs in observations:
        validate_observation(obs)
        alert.trajectory.append({"event": "context", "evidence_id": obs.evidence_id,
                                 "rows": len(obs.records)})

    for number in range(1, max_calls + 1):
        context = build_tool_context_v3(alert, observations, max_calls - number + 1,
                                        inst, skill)
        alert.trajectory.append({"event": "model_call", "number": number})
        try:
            step = model.next_step(context)
            if isinstance(step, ToolCall):
                validate_tool_call(step, alert)
                observation, cached = execute_tool_cached(step, cache, now)
                observations.append(observation)
                alert.trajectory.append({"event": "tool_call", "tool": step.name,
                                         "evidence_id": observation.evidence_id,
                                         "rows": len(observation.records), "cache": cached})
                continue
            validate_grounded_decision(step, alert, observations)
            validate_evidence_coverage(step, alert, observations)
            validate_claim_trust(step, observations)  # (2) rule R12
        except (ValidationError, DecisionValidationError, ToolCallError, ValueError) as error:
            return fail(alert, str(error))

        alert.status = STATUS_MAP[step.status]
        alert.risk_level = step.risk_level
        alert.reason = step.reason
        alert.missing_evidence = list(step.missing_evidence)
        remember_decision(memory, alert, inst, now)
        alert.trajectory.append({"event": "remember", "alert_id": alert.alert_id})
        alert.trajectory.append({"event": "halt", "outcome": "completed",
                                 "status": alert.status,
                                 "evidence_ids": list(step.evidence_ids)})
        return alert

    return fail(alert, f"no decision within {max_calls} model calls")''')

# ---------------------------------------------------------------- 5.11
md("""## Step 5.11: Run HD-2207 end to end

The user's report arrives at 9:05. Intake extracts one slot from the text, fills four from the sign-in log, asks the user one question, and pauses. Four minutes later the user answers. Intake finishes, and Triage decides.""")
code('''# --- Step 5.11: the whole conversation ---
OUTBOX.clear()
t0 = parse_when(report.received_at)
mfa_memory, mfa_cache = build_memory(t0), ToolCache()
hd = IntakeState(report=report)

advance(hd, DeterministicExtractor(), MFA_REPORT_SLOTS, mfa_cache, t0)
print("9:05", hd.status, "| asked:", OUTBOX[-1]["to"], "->", OUTBOX[-1]["question"])

answer(hd, "password_exposed", "yes", "user:userid122", MFA_REPORT_SLOTS, t0 + timedelta(minutes=4))
advance(hd, DeterministicExtractor(), MFA_REPORT_SLOTS, mfa_cache, t0 + timedelta(minutes=4))
print("9:09", hd.status)

alert_json_3, intake_obs = to_alert(hd, "ALT-1003")
mfa = run_triage_agent_v5(ingest_alert(alert_json_3), DeterministicIntakeModel(), triage_v5,
                          SKILLS, mfa_memory, mfa_cache, t0 + timedelta(minutes=4), intake_obs)
print(f"\\n{mfa.alert_id}: {mfa.status} | risk {mfa.risk_level}\\nreason: {mfa.reason}")
print("\\nintake trail:")
for event in hd.trail:
    print("  ", event)
print("\\nanswers in the context:")
display(pd.DataFrame(intake_obs.records))''')

# ---------------------------------------------------------------- 5.12
md("""## Step 5.12: Run it with Gemma

Gemma extracts slots from the report through the same validation, and decides through the same harness. Requires the Ollama server from Step 0.""")
code('''# --- Step 5.12: Gemma extracts and decides ---
try:
    g = IntakeState(report=report)
    advance(g, GemmaExtractor(), MFA_REPORT_SLOTS, ToolCache(), t0)
    print("Gemma extracted:", {k: v.value for k, v in g.filled.items() if v.source == "report"})
    if g.status == "WAITING_FOR_HUMAN":
        answer(g, g.pending, "yes", "user:userid122", MFA_REPORT_SLOTS, t0 + timedelta(minutes=4))
        advance(g, GemmaExtractor(), MFA_REPORT_SLOTS, ToolCache(), t0 + timedelta(minutes=4))
    if g.status == "WAITING_FOR_HUMAN":  # Gemma left user_initiated for the user to answer
        answer(g, g.pending, "yes", "user:userid122", MFA_REPORT_SLOTS, t0 + timedelta(minutes=8))
        advance(g, GemmaExtractor(), MFA_REPORT_SLOTS, ToolCache(), t0 + timedelta(minutes=8))
    g_alert, g_obs = to_alert(g, "ALT-1004")
    g_run = run_triage_agent_v5(ingest_alert(g_alert), GemmaToolModelV5(), triage_v5, SKILLS,
                                build_memory(t0), ToolCache(), t0 + timedelta(minutes=8), g_obs)
    print("status:", g_run.status, "| risk:", g_run.risk_level, "\\nreason:", g_run.reason)
except Exception as error:
    print(f"Gemma run skipped (is Ollama running? see Step 0): {type(error).__name__}")''',
     tags=["needs_ollama"])

# ---------------------------------------------------------------- 5.13
md("""## Step 5.13: Prove the conversation controls hold

HD-2208 is the attacker's version: a report sent from the compromised account saying the push "was me." The user question is skipped, the analyst is asked instead, and the run can't close until an analyst verifies. The other checks attack the conversation itself.""")
code('''# --- Step 5.13: conversation checks ---
def try_it(action) -> str:
    try:
        action()
        return "accepted"
    except (AnswerError, ValueError, RuntimeError) as error:
        return f"refused: {error}"

attacker_report = report.model_copy(update={"report_id": "HD-2208",
    "text": "Got an MFA prompt just now, that was me on my new laptop. Please ignore."})

def attacker_run(analyst_says: str | None, model) -> AlertState:
    a = IntakeState(report=attacker_report)
    advance(a, DeterministicExtractor(), MFA_REPORT_SLOTS, ToolCache(), t0)
    if analyst_says:
        answer(a, "verified_by_analyst", analyst_says, "analyst:jlee", MFA_REPORT_SLOTS,
               t0 + timedelta(minutes=10))
    advance(a, DeterministicExtractor(), MFA_REPORT_SLOTS, ToolCache(), t0 + timedelta(minutes=45))
    alert_spec, obs = to_alert(a, "ALT-1005")
    return run_triage_agent_v5(ingest_alert(alert_spec), model, triage_v5, SKILLS,
                               build_memory(t0), ToolCache(), t0 + timedelta(minutes=45), obs)

close_anyway = lambda: ScriptedModel(
    ask("ip_reputation", ip="203.0.113.128"), ask("recent_login_history", user_id="userid122"),
    GroundedDecision(alert_id="ALT-1005", status="CLOSE", risk_level="LOW",
                     reason="The user said it was them.", missing_evidence=[],
                     evidence_ids=["E-INTAKE-HD-2208"]))

fresh_intake = lambda: advance(IntakeState(report=report), DeterministicExtractor(),
                               MFA_REPORT_SLOTS, ToolCache(), t0)
conversation_checks = {
    "answer from another user": try_it(lambda: answer(fresh_intake(), "password_exposed", "no",
                                       "user:userid999", MFA_REPORT_SLOTS, t0)),
    "answer outside the choices": try_it(lambda: answer(fresh_intake(), "password_exposed",
                                         "my password is hunter2", "user:userid122",
                                         MFA_REPORT_SLOTS, t0)),
    "answer to a question not asked": try_it(lambda: answer(fresh_intake(), "verified_by_analyst",
                                             "verified", "analyst:jlee", MFA_REPORT_SLOTS, t0)),
    "answer after the question expired": try_it(lambda: answer(fresh_intake(), "password_exposed",
                                                "no", "user:userid122", MFA_REPORT_SLOTS,
                                                t0 + timedelta(hours=1))),
    "triage before intake is ready": try_it(lambda: to_alert(fresh_intake(), "ALT-1006")),
    "HD-2208, analyst not_verified": attacker_run("not_verified", DeterministicIntakeModel()).status,
    "HD-2208, model closes anyway": attacker_run("not_verified", close_anyway()).trajectory[-1]["reason"],
    "HD-2208, analyst never answers": attacker_run(None, DeterministicIntakeModel()).status,
    "HD-2208, analyst verified": attacker_run("verified", DeterministicIntakeModel()).status,
}
display(pd.DataFrame.from_dict(conversation_checks, orient="index", columns=["result"]))''')

# ---------------------------------------------------------------- 5.14
md("""## Step 5.14: Observability and evaluation

The intake trail records every slot, where it came from, every question, who it went to, and every expiry. The alert's trajectory records that the answers reached the context. Together they answer the question a reviewer will ask about any decision built on a conversation: who told us what, and when?

The evaluation that matters for intake is about people's time as much as correctness: how many questions did it take?""")
code('''# --- Step 5.14: observability and evaluation ---
display(show_audit(mfa))

def score_intake(name: str, intake: IntakeState, alert: AlertState, expected: tuple) -> dict:
    sources = [v.source.split(":")[0] for v in intake.filled.values()]
    return {"run": name, "status": alert.status, "correct": (alert.status, alert.risk_level) == expected,
            "questions": intake.questions_asked, "from report": sources.count("report"),
            "from systems": sources.count("system"), "from people": sources.count("user")
            + sources.count("analyst"), "unanswered": len(intake.unanswered)}

display(pd.DataFrame([score_intake("HD-2207", hd, mfa, ("ESCALATED", "HIGH"))]).set_index("run"))''')

md("""## Where this leaves Aegis

Triage can now work from an incomplete report. It fills slots in a fixed order (the report's own words, then systems, then people), asks one reviewed question at a time to the one person who can answer it, pauses while it waits, and resumes when the answer arrives. Answers carry who gave them, and a user's word alone can never close an alert.

Aegis is still one agent, but it now has governed instructions, tools, memory, and a way to ask. What it doesn't have is any knowledge beyond its two tools. When an analyst asks what the runbook says to do about an MFA push after a phished password, Triage has no runbook to read. That is where Chapter 6 begins.""")

nb.cells.extend(cells)
nbf.validate(nb)
nbf.write(nb, out)
print(f"appended {len(cells)} Chapter 5 cells | triage@5.0.0 sha {V5_SHA[:12]}")
