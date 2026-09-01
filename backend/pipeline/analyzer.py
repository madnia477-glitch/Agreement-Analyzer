"""
Orchestrates the full analysis pipeline described in spec section 2:

  Structure Detection -> Clause Extraction -> Form/Field Detection ->
  Signature Detection -> Checkbox/Consent Detection -> Obligation Detection ->
  Fee/Penalty Detection -> Risk & Attention Analysis -> RAG Evidence
  Verification -> Generate Agreement Report

Each category below is retrieved and analyzed independently (spec section 32:
"Do not put all functionality into one giant AI prompt"), then merged and
validated into a single AgreementAnalysis object (spec section 30).
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional, Callable   # <-- Optional imported here
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from .parser import ParsedDocument
from .retriever import EvidenceIndex, build_chunks
from .detector import detect_candidates, Candidate
from . import llm, terms
from .schema import (
    AgreementAnalysis, DocumentOverview, FinancialTerm, Obligation, BankRight,
    DefaultClause, TerminationClause, EarlySettlement, AttentionArea,
    SignatureField, Checkbox, Consent, ActionItem, MissingField, AttentionScore, Source,
    ImportantTerm, EvidenceItem,
)

logger = logging.getLogger("agreement_analyzer.analyzer")

# Free Groq tiers have a small tokens-per-minute budget, so we cap how many
# category calls are in flight at once (llm.py also serializes/throttles the
# actual HTTP calls). Raise this via env var only if you're on a paid tier.
_MAX_PARALLEL_CALLS = int(os.environ.get("ANALYZER_MAX_PARALLEL_CALLS", "2"))

CATEGORY_QUERIES: Dict[str, List[str]] = {
    "overview": ["agreement between", "this agreement", "parties", "bank name", "product", "effective date", "account number"],
    "financial": ["fee", "charge", "interest rate", "profit rate", "processing fee", "annual fee",
                  "late payment", "penalty", "insurance takaful", "tax", "principal amount"],
    "obligations_rights": ["customer shall", "customer must", "customer agrees to", "you must",
                            "the bank may", "the bank reserves the right", "the bank is entitled to",
                            "maintain minimum balance", "notify the bank", "provide documents"],
    "default_termination": ["default", "breach of agreement", "terminate this agreement", "termination",
                             "early settlement", "prepayment", "pre-closure", "notice period", "outstanding balance"],
    "attention": ["automatic renewal", "arbitration", "governing law", "liability", "indemnify",
                  "data sharing", "credit information bureau", "variable rate", "amend these terms",
                  "modify this agreement", "waiver"],
}


def _source_or_none(page) -> Source | None:
    return Source(page=page) if page else None


# ---------------------------------------------------------------------------
# Category 1: Document overview + executive summary + questions to consider
# ---------------------------------------------------------------------------

def _analyze_overview(index: EvidenceIndex, page_count: int) -> Dict[str, Any]:
    evidence = index.format_for_prompt(index.search_many(CATEGORY_QUERIES["overview"], top_k_each=4))
    prompt = f"""Using ONLY the evidence below from a bank/financial agreement, extract an overview.

EVIDENCE:
{evidence}

Return JSON exactly in this shape:
{{
  "agreement_type": "string or null (e.g. Loan Agreement, Credit Card Agreement)",
  "parties": ["list of named parties, e.g. bank name and customer/applicant role"],
  "institution": "string or null",
  "product": "string or null",
  "version": "string or null",
  "effective_date": "string or null",
  "executive_summary": "3-5 sentence plain-language summary of what this agreement is and what it covers, grounded only in the evidence",
  "questions_to_consider": ["5-8 specific questions the customer may want to clarify before signing, based on gaps or notable terms in the evidence"]
}}
If a field cannot be determined from the evidence, use null (or an empty list). Do not guess."""
    data = llm.call_json(prompt)
    data["page_count"] = page_count
    return data


# ---------------------------------------------------------------------------
# Category 2: Financial terms / fees / penalties
# ---------------------------------------------------------------------------

def _analyze_financial(index: EvidenceIndex) -> List[FinancialTerm]:
    evidence = index.format_for_prompt(index.search_many(CATEGORY_QUERIES["financial"], top_k_each=4))
    prompt = f"""Using ONLY the evidence below, list every financial obligation, fee, charge, or penalty
described in this agreement (spec: Principal, Interest/Profit Rate, Processing Fee, Annual Fee,
Late Payment Fee, Early Settlement Fee, Insurance/Takaful, Taxes, Other Charges, Penalties).

EVIDENCE:
{evidence}

Return a JSON array. Each item:
{{
  "label": "short name of the charge",
  "amount_or_rule": "the amount, rate, or rule as stated in the evidence, or null if not specified",
  "calculation_shown": true/false,
  "assumptions": "only if you had to combine numbers to compute something, state the assumption; else null",
  "page": integer page number from the evidence this came from,
  "needs_review": true if the amount/rule is ambiguous or not clearly stated
}}
Only include items actually supported by the evidence. Do not invent amounts."""
    items = llm.call_json(prompt)
    out = []
    for it in items if isinstance(items, list) else []:
        out.append(FinancialTerm(
            label=it.get("label", "Unlabeled charge"),
            amount_or_rule=it.get("amount_or_rule"),
            calculation_shown=bool(it.get("calculation_shown", False)),
            assumptions=it.get("assumptions"),
            source=_source_or_none(it.get("page")),
            needs_review=bool(it.get("needs_review", False)),
        ))
    return out


# ---------------------------------------------------------------------------
# Category 3: Obligations + Bank rights
# ---------------------------------------------------------------------------

def _analyze_obligations_rights(index: EvidenceIndex) -> tuple[List[Obligation], List[BankRight]]:
    evidence = index.format_for_prompt(index.search_many(CATEGORY_QUERIES["obligations_rights"], top_k_each=4))
    prompt = f"""Using ONLY the evidence below, separately list (a) obligations imposed on the customer
and (b) rights granted to the bank/institution.

EVIDENCE:
{evidence}

Return JSON:
{{
  "obligations": [
    {{"obligation": "short label", "meaning": "plain-language explanation", "trigger": "string or null",
      "frequency": "string or null", "consequence": "string or null if stated", "page": integer}}
  ],
  "bank_rights": [
    {{"right": "short label", "conditions": "conditions under which the bank can exercise this right, or null", "page": integer}}
  ]
}}
Only include items supported by the evidence."""
    data = llm.call_json(prompt)
    obligations = [
        Obligation(
            obligation=o.get("obligation", ""), meaning=o.get("meaning"), trigger=o.get("trigger"),
            frequency=o.get("frequency"), consequence=o.get("consequence"), source=_source_or_none(o.get("page")),
        ) for o in data.get("obligations", [])
    ]
    rights = [
        BankRight(right=r.get("right", ""), conditions=r.get("conditions"), source=_source_or_none(r.get("page")))
        for r in data.get("bank_rights", [])
    ]
    return obligations, rights


# ---------------------------------------------------------------------------
# Category 4: Default / Termination / Early settlement
# ---------------------------------------------------------------------------

def _analyze_default_termination(index: EvidenceIndex) -> tuple[List[DefaultClause], List[TerminationClause], EarlySettlement]:
    evidence = index.format_for_prompt(index.search_many(CATEGORY_QUERIES["default_termination"], top_k_each=4))
    prompt = f"""Using ONLY the evidence below, extract (a) what can put the customer in default,
(b) how this agreement can end, and (c) early settlement / prepayment terms.

EVIDENCE:
{evidence}

Return JSON:
{{
  "default_clauses": [
    {{"trigger": "condition, e.g. missed payment", "stated_consequence": "what the agreement says may happen, or null", "page": integer}}
  ],
  "termination_clauses": [
    {{"who_can_terminate": "string or null", "when": "string or null", "how": "string or null",
      "notice_required": "string or null", "fee": "string or null", "consequences": "string or null", "page": integer}}
  ],
  "early_settlement": {{
    "present": true/false,
    "conditions": "string or null", "charges": "string or null", "rebate": "string or null",
    "notice_requirement": "string or null", "page": integer or null
  }}
}}
Only include items supported by the evidence."""
    data = llm.call_json(prompt)
    defaults = [
        DefaultClause(trigger=d.get("trigger", ""), stated_consequence=d.get("stated_consequence"),
                      source=_source_or_none(d.get("page")))
        for d in data.get("default_clauses", [])
    ]
    terms = [
        TerminationClause(
            who_can_terminate=t.get("who_can_terminate"), when=t.get("when"), how=t.get("how"),
            notice_required=t.get("notice_required"), fee=t.get("fee"), consequences=t.get("consequences"),
            source=_source_or_none(t.get("page")),
        ) for t in data.get("termination_clauses", [])
    ]
    es_raw = data.get("early_settlement") or {}
    early = EarlySettlement(
        present=bool(es_raw.get("present", False)), conditions=es_raw.get("conditions"),
        charges=es_raw.get("charges"), rebate=es_raw.get("rebate"),
        notice_requirement=es_raw.get("notice_requirement"), source=_source_or_none(es_raw.get("page")),
    )
    return defaults, terms, early


# ---------------------------------------------------------------------------
# Category 5: Attention areas + benefits/concerns
# ---------------------------------------------------------------------------

def _analyze_attention(index: EvidenceIndex) -> tuple[List[AttentionArea], List[EvidenceItem], List[EvidenceItem]]:
    evidence = index.format_for_prompt(index.search_many(CATEGORY_QUERIES["attention"], top_k_each=4))
    prompt = f"""Using ONLY the evidence below, identify terms a customer may want to review carefully
(spec: significant fees, variable rates, broad authorization, automatic renewal, strict default
conditions, early settlement charges, extensive obligations, liability/arbitration provisions,
data-sharing consent, rights to modify terms). Never call anything "illegal" or "unsafe" - only
describe what the document says and why it deserves attention. Also separately list document-supported
positive/useful characteristics (potential benefits) and characteristics worth extra attention
(potential concerns) - every single one MUST cite a page and a short paraphrase of the source text.

EVIDENCE:
{evidence}

Return JSON:
{{
  "attention_areas": [
    {{"level": "Informational|Attention|Important", "topic": "short label",
      "what_document_says": "plain-language description grounded in evidence",
      "why_it_matters": "why a customer would want to review this", "page": integer}}
  ],
  "potential_benefits": [
    {{"title": "short label, e.g. 'Clearly defined payment schedule'",
      "explanation": "one sentence on why this may be useful, grounded in evidence",
      "page": integer, "section": "string or null", "source_text": "short paraphrase of the supporting text"}}
  ],
  "potential_concerns": [
    {{"title": "short label, e.g. 'Early settlement charge'",
      "explanation": "one neutral sentence describing what the document says and why it deserves attention",
      "page": integer, "section": "string or null", "source_text": "short paraphrase of the supporting text"}}
  ]
}}
Only include items supported by the evidence. Use "Important" sparingly, only for the most consequential items.
Never phrase a concern as a legal or financial judgment - describe only what the document says."""
    data = llm.call_json(prompt)
    areas = [
        AttentionArea(
            level=a.get("level", "Attention") if a.get("level") in ("Informational", "Attention", "Important") else "Attention",
            topic=a.get("topic", ""), what_document_says=a.get("what_document_says", ""),
            why_it_matters=a.get("why_it_matters"), source=_source_or_none(a.get("page")),
        ) for a in data.get("attention_areas", [])
    ]
    benefits = [
        EvidenceItem(title=b.get("title", ""), explanation=b.get("explanation", ""),
                     page=b.get("page"), section=b.get("section"), source_text=b.get("source_text"))
        for b in data.get("potential_benefits", []) if isinstance(b, dict)
    ]
    concerns = [
        EvidenceItem(title=c.get("title", ""), explanation=c.get("explanation", ""),
                     page=c.get("page"), section=c.get("section"), source_text=c.get("source_text"))
        for c in data.get("potential_concerns", []) if isinstance(c, dict)
    ]
    return areas, benefits, concerns


# ---------------------------------------------------------------------------
# Feature One: Important Terms (spec sections 3-7, 24, 32)
# Combines the static seed keyword list with LLM-driven, document-specific
# term discovery over retrieved evidence - never the static list alone.
# ---------------------------------------------------------------------------

IMPORTANT_TERM_QUERIES = [
    "interest rate profit rate principal repayment installment",
    "penalty late payment default collateral security guarantee guarantor",
    "termination early settlement prepayment renewal automatic renewal",
    "arbitration liability indemnity governing law dispute",
    "authorization consent debit direct debit data sharing credit check",
    "insurance takaful fees charges processing fee annual fee",
    "signature initials witness declaration acknowledgement",
]


def _analyze_important_terms(index: EvidenceIndex) -> List[ImportantTerm]:
    seed_hint = ", ".join(terms.SEED_KEYWORDS)
    evidence = index.format_for_prompt(index.search_many(IMPORTANT_TERM_QUERIES, top_k_each=3), max_chunks=14)
    prompt = f"""Using ONLY the evidence below from a bank/financial agreement, identify the important
contractual terms a customer should understand before signing.

Consider this seed list as a starting point, but you MUST also find document-specific important terms
that are not on this list if the evidence supports them (e.g. a specific product name, a specific
committee, a specific rate name used in this agreement). Do not report a term found in the seed list
unless it actually appears with meaningful context in the evidence.
Seed list: {seed_hint}

Do NOT report generic/common words (the, and, is, customer, agreement, shall, will, etc.) even if they
appear in the seed list context - only report terms that materially help understanding the agreement.

EVIDENCE:
{evidence}

Return a JSON array, each item:
{{
  "term": "the term/phrase exactly as it should be searched for in the document text (short, 1-4 words)",
  "category": "Financial|Obligation|Bank Right|Customer Responsibility|Penalty|Default|Termination|Authorization|Consent|Signature|Date|Payment|Security|Guarantee|Data Privacy|Dispute Resolution|Renewal|Other Important Term",
  "importance": "high|medium|low",
  "page": integer,
  "section": "string or null",
  "source_text": "short paraphrase (not verbatim) of the surrounding text",
  "explanation": "one sentence, plain language, on what this term means IN THIS agreement",
  "confidence": number between 0 and 1
}}
Return at most 40 items, prioritizing high-importance, document-specific terms. Only include terms
clearly supported by the evidence."""
    items = llm.call_json(prompt, max_tokens=3500)
    out: List[ImportantTerm] = []
    for it in items if isinstance(items, list) else []:
        category = it.get("category", "Other Important Term")
        if category not in (
            "Financial", "Obligation", "Bank Right", "Customer Responsibility", "Penalty", "Default",
            "Termination", "Authorization", "Consent", "Signature", "Date", "Payment", "Security",
            "Guarantee", "Data Privacy", "Dispute Resolution", "Renewal", "Other Important Term",
        ):
            category = "Other Important Term"
        importance = it.get("importance", "medium")
        if importance not in ("high", "medium", "low"):
            importance = "medium"
        term_text = (it.get("term") or "").strip()
        if not term_text or term_text.lower() in terms.STOPWORDS_NEVER_HIGHLIGHT:
            continue
        out.append(ImportantTerm(
            term=term_text, category=category, importance=importance, page=it.get("page"),
            section=it.get("section"), source_text=it.get("source_text"), explanation=it.get("explanation"),
            confidence=float(it.get("confidence", 0.6) or 0.6),
        ))
    return out


def _attach_bboxes(doc: ParsedDocument, important_terms: List[ImportantTerm]) -> None:
    """Best-effort: locate each important term's bounding box on its page so the
    frontend can draw a highlight overlay without ever touching the original PDF."""
    pages_by_number = {p.number: p for p in doc.pages}
    for term in important_terms:
        if not term.page:
            continue
        page = pages_by_number.get(term.page)
        if not page:
            continue
        bbox = terms.find_bbox_for_phrase(page, term.term)
        if bbox:
            term.bbox = bbox


# ---------------------------------------------------------------------------
# Feature Three: Pre-Signing Voice Summary (spec sections 12-16)
# Built ONLY from the already-validated, evidence-backed AgreementAnalysis -
# never from a fresh, ungrounded LLM pass over the raw document.
# ---------------------------------------------------------------------------

# ---- UPDATED FUNCTION ----
def _generate_voice_summary(analysis: AgreementAnalysis, language: str = "en") -> Optional[str]:
    """Generate a SHORT key-points-only voice summary in English or Urdu.
    Never reads the full report word-by-word. Only covers:
    Overall Summary, Risk Level, Major Risks, Red Flags, Financial Obligations,
    Penalties, Important Dates, Termination, Final Warning.
    """
    compact = {
        "agreement_type": analysis.document_overview.agreement_type,
        "institution": analysis.document_overview.institution,
        "executive_summary": analysis.executive_summary,
        "financial_terms": [
            {"label": f.label, "amount_or_rule": f.amount_or_rule} for f in analysis.financial_terms
        ],
        "obligations": [{"obligation": o.obligation, "consequence": o.consequence} for o in analysis.obligations],
        "bank_rights": [r.right for r in analysis.bank_rights],
        "default_clauses": [{"trigger": d.trigger, "consequence": d.stated_consequence} for d in analysis.default_clauses],
        "termination_clauses": [
            {"who": t.who_can_terminate, "notice": t.notice_required, "fee": t.fee} for t in analysis.termination_clauses
        ],
        "early_settlement": analysis.early_settlement.model_dump() if analysis.early_settlement else None,
        "attention_score": analysis.attention_score.level if analysis.attention_score else "MODERATE",
        "potential_concerns": [c.title for c in analysis.potential_concerns],
        "important_dates": [d for d in analysis.questions_to_consider if "date" in d.lower()],
        "questions": analysis.questions_to_consider[:3],
    }

    if language == "ur":
        prompt = f"""مندرجہ ذیل تجزیے کی بنیاد پر ایک انتہائی مختصر اور اہم نکات پر مبنی اردو آوازی خلاصہ تیار کریں۔
یہ خلاصہ صرف ان اہم نکات پر مشتمل ہونا چاہیے:
1. مجموعی جائزہ (Overall Summary)
2. رسک لیول (Risk Level)
3. بڑے خطرات (Major Risks)
4. ریڈ فلیگز (Red Flags)
5. مالی ذمہ داریاں (Financial Obligations)
6. جرمانے (Penalties)
7. اہم تاریخیں (Important Dates)
8. معاہدہ ختم کرنے کی شرائط (Termination)
9. آخری وارننگ (Final Warning)

پوری رپورٹ کو لفظ بہ لفظ نہ پڑھیں۔ صرف اوپر دیے گئے نکات کو مختصر جملوں میں بیان کریں تاکہ سننے میں 30 سے 45 سیکنڈ لگیں۔
تجزیہ کا ڈیٹا (JSON):
{compact}

صرف اردو میں خلاصہ تحریر کریں، کوئی اضافی متن نہ ڈالیں۔"""
    else:
        prompt = f"""Based on the analysis below, generate a very short, key-points-only English voice summary.
Cover ONLY these points:
1. Overall Summary
2. Risk Level
3. Major Risks
4. Red Flags
5. Financial Obligations
6. Penalties
7. Important Dates
8. Termination Conditions
9. Final Warning

Do NOT read the full report word-by-word. Keep it concise so it takes 30 to 45 seconds to speak.
Analysis data (JSON):
{compact}

Return only the plain text summary, no extra commentary."""

    try:
        data = llm.call_json(prompt, max_tokens=800)  # Short token limit to force brevity
        if isinstance(data, dict):
            return data.get("voice_summary") or data.get("summary") or str(data)
        return str(data)
    except Exception as exc:
        logger.warning("Voice summary generation failed for %s: %s", language, exc)
        return None


# ---------------------------------------------------------------------------
# Category 6: Signatures / checkboxes / consents (grounded in detector candidates)
# ---------------------------------------------------------------------------

def _analyze_actions(candidates: List[Candidate]) -> tuple[List[SignatureField], List[Checkbox], List[Consent], List[ActionItem]]:
    sig_or_initial_or_date = [c for c in candidates if c.kind in ("signature", "initial", "date")]
    checkbox_candidates = [c for c in candidates if c.kind == "checkbox"]

    signatures: List[SignatureField] = []
    checkboxes: List[Checkbox] = []
    consents: List[Consent] = []
    actions: List[ActionItem] = []

    # Batch signature/initial/date candidates through the LLM for explanation + classification.
    if sig_or_initial_or_date:
        listing = "\n".join(
            f"{i}. page={c.page} kind={c.kind} blank={c.appears_blank} line=\"{c.line}\"\n   context: {c.context}"
            for i, c in enumerate(sig_or_initial_or_date)
        )
        prompt = f"""Below is a list of candidate signature/initial/date fields detected by regex in a
bank agreement, each with its surrounding text as context (this IS the evidence - use only this).

CANDIDATES:
{listing}

For each numbered candidate, return one JSON object in an array, in the same order:
{{
  "index": integer matching the candidate number,
  "field": "human label, e.g. Customer Signature / Guarantor Signature / Date",
  "section": "nearest section heading/name if visible in context, else null",
  "status": "Required|Appears Required|Needs Review",
  "meaning": "one sentence: what signing/dating this section indicates, grounded in the context",
  "attention_note": "one short sentence telling the reader what to review before signing, or null"
}}
If the context doesn't make the meaning clear, use status "Needs Review" and say so plainly."""
        try:
            results = llm.call_json(prompt, max_tokens=3500)
        except Exception as exc:
            logger.warning("Signature analysis LLM call failed: %s", exc)
            results = []
        results_by_index = {r.get("index"): r for r in results if isinstance(r, dict)}
        for i, c in enumerate(sig_or_initial_or_date):
            r = results_by_index.get(i, {})
            signatures.append(SignatureField(
                field=r.get("field", c.kind.title()), page=c.page, section=r.get("section"),
                status=r.get("status", "Needs Review") if r.get("status") in ("Required", "Appears Required", "Needs Review") else "Needs Review",
                meaning=r.get("meaning"), attention_note=r.get("attention_note"), appears_blank=c.appears_blank,
            ))
            action_type = "Initial Required" if c.kind == "initial" else ("Information Required" if c.kind == "date" else "Signature Required")
            actions.append(ActionItem(action_type=action_type, label=r.get("field", c.line[:60]), page=c.page,
                                       source=_source_or_none(c.page)))

    # Batch checkbox candidates through the LLM.
    if checkbox_candidates:
        listing = "\n".join(
            f"{i}. page={c.page} line=\"{c.line}\"\n   context: {c.context}" for i, c in enumerate(checkbox_candidates)
        )
        prompt = f"""Below is a list of candidate checkbox/consent lines detected in a bank agreement,
each with surrounding context (this IS the evidence - use only this).

CANDIDATES:
{listing}

For each numbered candidate, return one JSON object in an array, in the same order:
{{
  "index": integer,
  "classification": "Consent|Authorization|Acknowledgement|Selection Required|Optional|Unknown",
  "what_it_means": "one sentence, grounded in context",
  "what_happens_if_selected": "one sentence describing the stated consequence, or null if the document does not specify",
  "mandatory_or_optional_evidence": "quote-free explanation of why you classified it this way, or null",
  "is_consent_or_authorization_topic": "short topic label if this is a consent/authorization (e.g. 'Data-sharing consent', 'Credit-check authorization'), else null"
}}
Never assume optional vs mandatory without evidence - use "Unknown" and say so if unclear."""
        try:
            results = llm.call_json(prompt, max_tokens=3500)
        except Exception as exc:
            logger.warning("Checkbox analysis LLM call failed: %s", exc)
            results = []
        results_by_index = {r.get("index"): r for r in results if isinstance(r, dict)}
        for i, c in enumerate(checkbox_candidates):
            r = results_by_index.get(i, {})
            classification = r.get("classification", "Unknown")
            if classification not in ("Consent", "Authorization", "Acknowledgement", "Selection Required", "Optional", "Unknown"):
                classification = "Unknown"
            checkboxes.append(Checkbox(
                text=c.line, page=c.page, classification=classification,
                what_it_means=r.get("what_it_means"), what_happens_if_selected=r.get("what_happens_if_selected"),
                mandatory_or_optional_evidence=r.get("mandatory_or_optional_evidence"),
            ))
            topic = r.get("is_consent_or_authorization_topic")
            if topic:
                consents.append(Consent(topic=topic, description=r.get("what_it_means"), source=_source_or_none(c.page)))
            action_type = classification if classification in (
                "Consent", "Authorization", "Acknowledgement", "Selection Required", "Optional") else "Needs Review"
            actions.append(ActionItem(action_type=action_type, label=c.line[:60], page=c.page, source=_source_or_none(c.page)))

    return signatures, checkboxes, consents, actions


# ---------------------------------------------------------------------------
# Missing / blank fields (deterministic - no LLM needed, spec sections 26-27)
# ---------------------------------------------------------------------------

def _missing_fields(candidates: List[Candidate]) -> List[MissingField]:
    out = []
    for c in candidates:
        if c.appears_blank:
            out.append(MissingField(field=f"{c.kind.title()} ({c.line[:60]})", page=c.page,
                                     reason="This field appears incomplete."))
    return out


# ---------------------------------------------------------------------------
# Attention score - transparent, rule-based (spec section 24: NOT an LLM legal verdict)
# ---------------------------------------------------------------------------

def _attention_score(analysis: AgreementAnalysis) -> AttentionScore:
    factors: List[str] = []
    points = 0

    if len(analysis.financial_terms) >= 3:
        factors.append("Multiple financial obligations")
        points += 1
    if analysis.early_settlement and analysis.early_settlement.present:
        factors.append("Early settlement conditions")
        points += 1
    if analysis.default_clauses:
        factors.append("Default provisions present")
        points += 1
    if any(a.classification == "Authorization" for a in analysis.checkboxes):
        factors.append("Customer authorization requested")
        points += 1
    if any(t.notice_required or t.fee for t in analysis.termination_clauses):
        factors.append("Termination restrictions or fees")
        points += 1
    if sum(1 for a in analysis.attention_areas if a.level == "Important") >= 2:
        factors.append("Multiple 'Important' attention areas")
        points += 1

    level = "HIGH" if points >= 4 else ("MODERATE" if points >= 2 else "LOW")
    return AttentionScore(
        level=level,
        factors=factors or ["No significant attention factors were detected from the available evidence."],
        methodology_note=(
            "Calculated from a fixed checklist of document characteristics (fees, early settlement, "
            "default clauses, authorizations, termination restrictions, high-severity attention areas). "
            "Each present characteristic adds one point; 0-1 = LOW, 2-3 = MODERATE, 4+ = HIGH."
        ),
    )


def _checklist(analysis: AgreementAnalysis) -> List[str]:
    items = ["Verify your personal information (name, address, account/CNIC number)"]
    if analysis.financial_terms:
        items.append("Review the fees and charges listed in Financial Terms")
    if analysis.early_settlement and analysis.early_settlement.present:
        items.append("Review the early settlement / prepayment conditions")
    if analysis.default_clauses:
        items.append("Review what can put you in default")
    if analysis.termination_clauses:
        items.append("Review how and when this agreement can be terminated")
    if analysis.obligations:
        items.append("Review your obligations under this agreement")
    if analysis.bank_rights:
        items.append("Review the rights granted to the bank/institution")
    if analysis.consents or any(c.classification in ("Consent", "Authorization") for c in analysis.checkboxes):
        items.append("Review data-sharing, credit-check, and debit authorizations")
    if analysis.signatures:
        items.append("Review every signature, initial, and date field before signing")
    if analysis.missing_fields:
        items.append(f"Complete the {len(analysis.missing_fields)} field(s) that appear incomplete")
    return items


def _attach_action_bboxes(doc: ParsedDocument, signatures: List[SignatureField], checkboxes: List[Checkbox]) -> None:
    pages_by_number = {p.number: p for p in doc.pages}
    for s in signatures:
        page = pages_by_number.get(s.page) if s.page else None
        if page:
            s.bbox = terms.find_bbox_for_phrase(page, s.field)
    for c in checkboxes:
        page = pages_by_number.get(c.page) if c.page else None
        if page:
            c.bbox = terms.find_bbox_for_phrase(page, c.text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Ordered so the frontend's "Processing Agreement..." checklist (spec section 34)
# can show real, backend-reported progress instead of a fake progress bar.
PIPELINE_STAGES = [
    "Document uploaded",
    "Text extracted",
    "OCR completed",
    "Document structure detected",
    "Important terms detected",
    "Financial terms detected",
    "Obligations detected",
    "Signatures detected",
    "Checkboxes detected",
    "RAG verification completed",
    "Benefits analyzed",
    "Potential concerns analyzed",
    "Report generated",
    "Voice summary ready",
]

ProgressFn = Callable[[str], None]


def run_full_analysis(doc: ParsedDocument, on_progress: Optional[ProgressFn] = None) -> AgreementAnalysis:
    def progress(stage: str):
        if on_progress:
            try:
                on_progress(stage)
            except Exception:
                pass

    progress(PIPELINE_STAGES[1])  # Text extracted
    if any(p.used_ocr for p in doc.pages):
        progress(PIPELINE_STAGES[2])  # OCR completed

    chunks = build_chunks(doc)
    index = EvidenceIndex(chunks)
    candidates = detect_candidates(doc)
    progress(PIPELINE_STAGES[3])  # Document structure detected

    notes = []
    if not chunks:
        notes.append("No extractable text was found in this document, even after OCR. Results may be incomplete.")

    # Independent categories run in parallel (spec section 37: don't run
    # every AI task sequentially if they can safely run in parallel).
    results: Dict[str, Any] = {}
    jobs = {
        "overview": lambda: _analyze_overview(index, doc.page_count),
        "financial": lambda: _analyze_financial(index),
        "obligations_rights": lambda: _analyze_obligations_rights(index),
        "default_termination": lambda: _analyze_default_termination(index),
        "attention": lambda: _analyze_attention(index),
        "important_terms": lambda: _analyze_important_terms(index),
        "actions": lambda: _analyze_actions(candidates),
    }
    stage_for_job = {
        "important_terms": PIPELINE_STAGES[4],
        "financial": PIPELINE_STAGES[5],
        "obligations_rights": PIPELINE_STAGES[6],
        "actions": PIPELINE_STAGES[7],
        "attention": PIPELINE_STAGES[10],
    }
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CALLS) as pool:
        future_to_name = {pool.submit(fn): name for name, fn in jobs.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                logger.warning("Category '%s' failed: %s", name, exc)
                notes.append(f"The '{name.replace('_', ' ')}' section could not be fully analyzed ({exc}).")
                results[name] = None
            if name in stage_for_job:
                progress(stage_for_job[name])
    progress(PIPELINE_STAGES[8])  # Checkboxes detected (bundled with actions)
    progress(PIPELINE_STAGES[9])  # RAG verification completed

    overview_raw = results.get("overview") or {}
    financial = results.get("financial") or []
    obligations, bank_rights = results.get("obligations_rights") or ([], [])
    default_clauses, termination_clauses, early_settlement = results.get("default_termination") or ([], [], EarlySettlement())
    attention_areas, benefits, concerns = results.get("attention") or ([], [], [])
    important_terms = results.get("important_terms") or []
    signatures, checkboxes, consents, actions = results.get("actions") or ([], [], [], [])
    missing = _missing_fields(candidates)

    _attach_bboxes(doc, important_terms)
    _attach_action_bboxes(doc, signatures, checkboxes)
    progress(PIPELINE_STAGES[11])  # Potential concerns analyzed

    overview = DocumentOverview(
        agreement_type=overview_raw.get("agreement_type"),
        parties=overview_raw.get("parties", []) or [],
        institution=overview_raw.get("institution"),
        product=overview_raw.get("product"),
        version=overview_raw.get("version"),
        effective_date=overview_raw.get("effective_date"),
        page_count=doc.page_count,
    )

    analysis = AgreementAnalysis(
        document_overview=overview,
        executive_summary=overview_raw.get("executive_summary"),
        financial_terms=financial,
        obligations=obligations,
        bank_rights=bank_rights,
        default_clauses=default_clauses,
        termination_clauses=termination_clauses,
        early_settlement=early_settlement,
        attention_areas=attention_areas,
        important_terms=important_terms,
        potential_benefits=benefits,
        potential_concerns=concerns,
        signatures=signatures,
        checkboxes=checkboxes,
        consents=consents,
        actions=actions,
        missing_fields=missing,
        questions_to_consider=overview_raw.get("questions_to_consider", []) or [],
        processing_notes=notes,
    )
    analysis.attention_score = _attention_score(analysis)
    analysis.before_you_sign_checklist = _checklist(analysis)
    progress(PIPELINE_STAGES[12])  # Report generated

    analysis.voice_summary = _generate_voice_summary(analysis)
    progress(PIPELINE_STAGES[13])  # Voice summary ready

    return analysis


def answer_question(doc: ParsedDocument, question: str) -> Dict[str, Any]:
    """Grounded Q&A for 'What am I agreeing to?' / 'What happens if I sign?' / free-form questions."""
    chunks = build_chunks(doc)
    index = EvidenceIndex(chunks)
    evidence_chunks = index.search(question, top_k=8)
    evidence = index.format_for_prompt(evidence_chunks)
    prompt = f"""A customer is asking a question about a bank agreement they have not yet signed.
Answer ONLY using the evidence below. If the evidence does not clearly answer the question, say so
plainly rather than guessing. Never tell them whether they should sign.

EVIDENCE:
{evidence}

QUESTION: {question}

Return JSON:
{{
  "answer": "plain-language answer grounded only in the evidence",
  "pages_referenced": [list of page numbers actually used],
  "confident": true/false
}}"""
    data = llm.call_json(prompt, max_tokens=1500)
    return data