# ruff: noqa
import os
import google.auth
import re
import json
import datetime
from pydantic import BaseModel, Field
from google.genai import types

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import Workflow, START
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from app.config import config

# Initialize Gemini Model
model = Gemini(model=config.model)

# -----------------------------------------------------------------------------
# Structured Output Schemas
# -----------------------------------------------------------------------------

class AnalysisViolation(BaseModel):
    category: str = Field(description="Violation category (e.g. Travel, Meals, Subscriptions)")
    amount: float = Field(description="Transaction amount")
    description: str = Field(description="Details of the violation")
    severity: str = Field(description="Low, Medium, or High")

class AnalysisResult(BaseModel):
    violations: list[AnalysisViolation] = Field(default_factory=list)
    is_suspicious: bool = Field(description="Whether the transaction seems suspicious")
    notes: str = Field(description="Any extra notes or flags")

class AuditFinding(BaseModel):
    policy_impact: str = Field(description="Corporate policy impacted")
    justification: str = Field(description="Audit justification")
    remediation: str = Field(description="Recommended action/remediation")

class AuditResult(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)
    override_score: float = Field(description="Risk adjustment score from 0-10")
    recommend_review: bool = Field(description="Whether human review is recommended")

class AuditReport(BaseModel):
    transactions_analyzed: int = Field(description="Number of transactions processed")
    violations_found: list[str] = Field(default_factory=list)
    risk_score: float = Field(description="Overall risk score from 0 to 100")
    requires_human_review: bool = Field(description="Whether the audit requires human compliance review")
    summary: str = Field(description="Detailed summary of the compliance audit")

# -----------------------------------------------------------------------------
# MCP Toolset Connection
# -----------------------------------------------------------------------------

mcp_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", "-m", "app.mcp_server"],
        )
    )
)

# -----------------------------------------------------------------------------
# Sub-agents
# -----------------------------------------------------------------------------

transaction_analyzer = LlmAgent(
    name="transaction_analyzer",
    model=model,
    instruction=(
        "You are a Transaction Analyzer sub-agent. Your job is to analyze transaction logs "
        "and identify potential policy violations. First, retrieve current corporate policy rules "
        "and lookup employee details using the available MCP tools to determine if a violation has occurred."
    ),
    tools=[mcp_tools],
    output_schema=AnalysisResult,
)

compliance_auditor = LlmAgent(
    name="compliance_auditor",
    model=model,
    instruction=(
        "You are a Compliance Auditor sub-agent. Your job is to evaluate violations "
        "identified in transaction logs, check for potential fraudulent patterns, and recommend policy exceptions "
        "or remediation steps. Use the MCP logging tool to log compliance audit actions."
    ),
    tools=[mcp_tools],
    output_schema=AuditResult,
)

# -----------------------------------------------------------------------------
# Orchestrator Agent
# -----------------------------------------------------------------------------

orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=model,
    instruction=(
        "You are the Audit Compliance Orchestrator. You coordinate transaction compliance checks. "
        "Utilize transaction_analyzer to scan the transactions for policy violations, "
        "then use compliance_auditor to evaluate findings, check for overrides, and compute the risk. "
        "Synthesize all information into a final structured AuditReport."
    ),
    tools=[
        AgentTool(transaction_analyzer),
        AgentTool(compliance_auditor)
    ],
    output_schema=AuditReport,
    output_key="audit_report",
)

# -----------------------------------------------------------------------------
# Workflow Function Nodes
# -----------------------------------------------------------------------------

def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    text = ""
    if node_input and node_input.parts:
        text = node_input.parts[0].text or ""
    
    # 1. PII Scrubbing (Credit Card & SSN)
    scrubbed_text = text
    cc_pattern = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
    ssn_pattern = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    
    scrubbed_cc = False
    scrubbed_ssn = False
    
    if cc_pattern.search(scrubbed_text):
        scrubbed_text = cc_pattern.sub("[REDACTED_CC]", scrubbed_text)
        scrubbed_cc = True
        
    if ssn_pattern.search(scrubbed_text):
        scrubbed_text = ssn_pattern.sub("[REDACTED_SSN]", scrubbed_text)
        scrubbed_ssn = True

    # 2. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions",
        "system prompt",
        "override rules",
        "you are now a",
        "bypass validation",
        "bypass security",
        "do not check policies"
    ]
    
    has_injection = False
    matched_keyword = ""
    for kw in injection_keywords:
        if kw in text.lower():
            has_injection = True
            matched_keyword = kw
            break

    # 3. Domain-specific rule (high risk fraud words or > $50,000 amount limit)
    fraud_keywords = ["bribery", "kickback", "laundering", "tax evasion"]
    has_fraud_risk = False
    matched_fraud = ""
    for fk in fraud_keywords:
        if fk in text.lower():
            has_fraud_risk = True
            matched_fraud = fk
            break
            
    high_amount = False
    amount_matches = re.findall(r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+)", text)
    for amt_str in amount_matches:
        try:
            amt = float(amt_str.replace(",", ""))
            if amt > 50000.0:
                high_amount = True
                break
        except ValueError:
            pass

    # 4. JSON Audit Log & Routing Verdict
    verdict = "safe"
    severity = "INFO"
    details = "Query passed initial security verification."

    if has_injection:
        verdict = "security_event"
        severity = "WARNING"
        details = f"Prompt injection attempt detected via keyword: '{matched_keyword}'."
    elif has_fraud_risk:
        verdict = "security_event"
        severity = "CRITICAL"
        details = f"Fraud risk detected via high-risk term: '{matched_fraud}'."
    elif high_amount:
        verdict = "security_event"
        severity = "CRITICAL"
        details = "Transaction amount exceeds the security check limit of $50,000."
    elif scrubbed_cc or scrubbed_ssn:
        severity = "WARNING"
        details = f"PII data redacted (CC: {scrubbed_cc}, SSN: {scrubbed_ssn})."

    audit_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": "security_check",
        "severity": severity,
        "original_query_length": len(text),
        "verdict": verdict,
        "details": details
    }
    
    import sys
    print(f"SECURITY AUDIT: {json.dumps(audit_entry)}", file=sys.stderr)
    
    ctx.state["original_query"] = text
    ctx.state["scrubbed_query"] = scrubbed_text
    ctx.state["security_verdict"] = verdict
    ctx.state["security_details"] = details
    
    return Event(output=scrubbed_text, route=verdict)

def security_event_handler(ctx: Context, node_input: str) -> Event:
    msg = f"Security Block: Audit processing cancelled. Reason: {ctx.state.get('security_details')}"
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=msg)]))
    yield Event(output={"status": "security_block", "message": msg})

def route_decision(ctx: Context, node_input: dict) -> Event:
    requires_review = node_input.get("requires_human_review", False)
    if requires_review:
        return Event(output=node_input, route="human_review")
    return Event(output=node_input, route="auto_approve")

async def human_approval(ctx: Context, node_input: dict):
    if not ctx.resume_inputs or "approval" not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="approval",
            message=f"Human Review Required! Risk Score: {node_input.get('risk_score')}/100. Violations: {node_input.get('violations_found')}. Approve the transactions? (yes/no):"
        )
        return
    
    decision = ctx.resume_inputs["approval"]
    ctx.state["human_decision"] = decision
    yield Event(output={
        "status": "reviewed",
        "decision": decision,
        "report": node_input
    })

def final_report(ctx: Context, node_input: dict) -> Event:
    if "decision" in node_input:
        status_text = f"Audit reviewed by human. Decision: {node_input['decision']}."
        report_data = node_input["report"]
    else:
        status_text = "Audit auto-approved (no human review needed)."
        report_data = node_input

    summary = (
        f"### Audit Compliance Report\n"
        f"Status: {status_text}\n"
        f"Transactions Analyzed: {report_data.get('transactions_analyzed', 0)}\n"
        f"Violations Found:\n" + "\n".join(f"- {v}" for v in report_data.get('violations_found', [])) + "\n"
        f"Risk Score: {report_data.get('risk_score', 0)}/100\n\n"
        f"Summary:\n{report_data.get('summary', 'No summary generated.')}"
    )

    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=summary)]))
    yield Event(output={"report": report_data, "status": status_text})

# -----------------------------------------------------------------------------
# Workflow Definition
# -----------------------------------------------------------------------------

root_agent = Workflow(
    name="compliance_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"security_event": security_event_handler, "safe": orchestrator_agent}),
        (orchestrator_agent, route_decision),
        (route_decision, {"human_review": human_approval, "auto_approve": final_report}),
        (human_approval, final_report),
        (security_event_handler, final_report),
    ],
    description="Compliance workflow for scanning financial transactions and checking policies.",
)

app = App(
    root_agent=root_agent,
    name="app",
)
