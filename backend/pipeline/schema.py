"""
Data contract for the Agreement Analysis pipeline.

Every field that represents a claim about the document carries a `source`
(page + optional section label) so the UI can always show where a statement
came from. Nothing here should ever be filled from model memory alone -
see pipeline/analyzer.py for how evidence is attached before this schema
is populated.
"""
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class Source(BaseModel):
    page: Optional[int] = None
    section: Optional[str] = None
    quote: Optional[str] = None  # short paraphrase/snippet used as evidence, not a verbatim long quote


class BBox(BaseModel):
    """Bounding box in PDF point space (origin top-left), used to draw the
    highlight overlay on top of the (untouched) original PDF page."""
    page: int
    x0: float
    top: float
    x1: float
    bottom: float
    page_width: float
    page_height: float


class DocumentOverview(BaseModel):
    agreement_type: Optional[str] = None
    parties: List[str] = Field(default_factory=list)
    institution: Optional[str] = None
    product: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[str] = None
    page_count: int = 0
    detected_confidence: Literal["high", "medium", "low"] = "medium"


class FinancialTerm(BaseModel):
    label: str  # e.g. "Processing Fee", "Interest / Profit Rate"
    amount_or_rule: Optional[str] = None
    calculation_shown: bool = False
    assumptions: Optional[str] = None
    source: Optional[Source] = None
    needs_review: bool = False


class Obligation(BaseModel):
    obligation: str
    meaning: Optional[str] = None
    trigger: Optional[str] = None
    frequency: Optional[str] = None
    consequence: Optional[str] = None
    source: Optional[Source] = None


class BankRight(BaseModel):
    right: str
    conditions: Optional[str] = None
    source: Optional[Source] = None


class DefaultClause(BaseModel):
    trigger: str
    stated_consequence: Optional[str] = None
    source: Optional[Source] = None


class TerminationClause(BaseModel):
    who_can_terminate: Optional[str] = None
    when: Optional[str] = None
    how: Optional[str] = None
    notice_required: Optional[str] = None
    fee: Optional[str] = None
    consequences: Optional[str] = None
    source: Optional[Source] = None


class EarlySettlement(BaseModel):
    present: bool = False
    conditions: Optional[str] = None
    charges: Optional[str] = None
    rebate: Optional[str] = None
    notice_requirement: Optional[str] = None
    source: Optional[Source] = None


class AttentionArea(BaseModel):
    level: Literal["Informational", "Attention", "Important"] = "Attention"
    topic: str
    what_document_says: str
    why_it_matters: Optional[str] = None
    source: Optional[Source] = None


class SignatureField(BaseModel):
    field: str  # e.g. "Customer Signature"
    page: Optional[int] = None
    section: Optional[str] = None
    status: Literal["Required", "Appears Required", "Needs Review"] = "Appears Required"
    meaning: Optional[str] = None
    attention_note: Optional[str] = None
    appears_blank: bool = False
    bbox: Optional[BBox] = None


class Checkbox(BaseModel):
    text: str  # e.g. "I authorize the bank to ..."
    page: Optional[int] = None
    section: Optional[str] = None
    classification: Literal[
        "Consent", "Authorization", "Acknowledgement", "Selection Required", "Optional", "Unknown"
    ] = "Unknown"
    what_it_means: Optional[str] = None
    what_happens_if_selected: Optional[str] = None
    mandatory_or_optional_evidence: Optional[str] = None
    appears_checked: Optional[bool] = None
    bbox: Optional[BBox] = None


class Consent(BaseModel):
    topic: str  # e.g. "Data-sharing consent", "Credit-check authorization"
    description: Optional[str] = None
    source: Optional[Source] = None


class MissingField(BaseModel):
    field: str
    page: Optional[int] = None
    reason: str = "This field appears incomplete."


class ActionItem(BaseModel):
    """One row in the Action Classification (section 9)."""
    action_type: Literal[
        "Signature Required", "Initial Required", "Information Required",
        "Selection Required", "Consent", "Authorization", "Acknowledgement",
        "Optional", "Needs Review"
    ]
    label: str
    page: Optional[int] = None
    source: Optional[Source] = None


class ImportantTerm(BaseModel):
    term: str
    category: Literal[
        "Financial", "Obligation", "Bank Right", "Customer Responsibility", "Penalty",
        "Default", "Termination", "Authorization", "Consent", "Signature", "Date",
        "Payment", "Security", "Guarantee", "Data Privacy", "Dispute Resolution",
        "Renewal", "Other Important Term",
    ] = "Other Important Term"
    importance: Literal["high", "medium", "low"] = "medium"
    page: Optional[int] = None
    section: Optional[str] = None
    source_text: Optional[str] = None
    explanation: Optional[str] = None
    confidence: float = 0.6
    bbox: Optional[BBox] = None


class EvidenceItem(BaseModel):
    """Shared shape for a Potential Benefit or Potential Concern - both must
    carry document evidence (spec sections 9-11: 'every benefit/concern must
    have evidence')."""
    title: str
    explanation: str
    page: Optional[int] = None
    section: Optional[str] = None
    source_text: Optional[str] = None


class AttentionScore(BaseModel):
    level: Literal["LOW", "MODERATE", "HIGH"] = "MODERATE"
    factors: List[str] = Field(default_factory=list)
    disclaimer: str = "This score is a document-review indicator, not a legal or financial judgment."
    methodology_note: Optional[str] = None


class AgreementAnalysis(BaseModel):
    document_overview: DocumentOverview
    executive_summary: Optional[str] = None
    financial_terms: List[FinancialTerm] = Field(default_factory=list)
    obligations: List[Obligation] = Field(default_factory=list)
    bank_rights: List[BankRight] = Field(default_factory=list)
    default_clauses: List[DefaultClause] = Field(default_factory=list)
    termination_clauses: List[TerminationClause] = Field(default_factory=list)
    early_settlement: Optional[EarlySettlement] = None
    attention_areas: List[AttentionArea] = Field(default_factory=list)
    important_terms: List[ImportantTerm] = Field(default_factory=list)
    potential_benefits: List[EvidenceItem] = Field(default_factory=list)
    potential_concerns: List[EvidenceItem] = Field(default_factory=list)
    signatures: List[SignatureField] = Field(default_factory=list)
    checkboxes: List[Checkbox] = Field(default_factory=list)
    consents: List[Consent] = Field(default_factory=list)
    actions: List[ActionItem] = Field(default_factory=list)
    missing_fields: List[MissingField] = Field(default_factory=list)
    questions_to_consider: List[str] = Field(default_factory=list)
    before_you_sign_checklist: List[str] = Field(default_factory=list)
    attention_score: Optional[AttentionScore] = None
    citations: List[Source] = Field(default_factory=list)
    processing_notes: List[str] = Field(default_factory=list)
    voice_summary: Optional[str] = None

    class Config:
        extra = "ignore"
