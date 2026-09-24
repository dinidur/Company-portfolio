"""
Guardrails - three places:

1. INPUT     check_user_input()   -> instruction override, data exfiltration, tool abuse
2. CONTEXT   sanitize_retrieved() -> indirect prompt injection hidden inside documents
3. OUTPUT    validate_answer()    -> hallucinated citations, leaks, brand rules, empty/invalid answers

Approach: fast rule-based checks (regex + scoring). They are cheap, explainable
(the UI shows WHICH rule fired) and they can't be "talked out of" like an LLM judge.
Trade-off: regex can miss clever paraphrases -> the real protection is architectural:
  - the LLM never decides permissions (RBAC is server side),
  - documents are passed as DATA inside <document> tags,
  - tools are allow-listed and parameters are validated,
  - the output validator checks every citation against what was actually retrieved.
"""
import re
import unicodedata

from config.settings import load_yaml, settings

# (pattern, category, weight)
INPUT_RULES = [
    (r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|above|prior|all|your)\b.{0,20}"
     r"\b(instructions?|rules?|prompts?|guardrails?)", "instruction_override", 3),
    (r"\byou are now\b|\bact as (an? )?(admin|administrator|root|developer mode)|\bDAN\b|\bjailbreak",
     "instruction_override", 3),
    (r"\b(reveal|show|print|repeat|output)\b.{0,30}\b(system prompt|hidden prompt|instructions|prompt above)",
     "prompt_leak", 3),
    (r"\b(pretend|assume)\b.{0,30}\b(i am|i'm|my role is)\b.{0,20}\b(admin|administrator|analyst)",
     "privilege_escalation", 3),
    (r"\b(send|post|upload|exfiltrate|forward)\b.{0,50}(https?://|webhook|email to|@[a-z0-9-]+\.)",
     "data_exfiltration", 3),
    (r"\b(all|every|entire|full)\b.{0,30}\b(passwords?|api keys?|secrets?|tokens?|phone numbers|salar(y|ies))",
     "data_exfiltration", 2),
    (r"\b(dump|export)\b.{0,30}\b(database|directory|all (records|employees|customers))", "data_exfiltration", 2),
    (r"\b(rm -rf|drop table|delete from|shutdown|os\.system|subprocess|__import__|eval\(|exec\()",
     "tool_abuse", 3),
    (r"\b(call|run|execute|invoke)\b.{0,30}\b(\d{2,}|hundred|thousand) times", "tool_abuse", 2),
    (r"\b(reindex|delete|wipe)\b.{0,30}\b(knowledge base|index|vector)", "admin_action", 1),
]
BLOCK_SCORE = 3

DOC_INJECTION = re.compile(
    r"(ignore (all )?(previous|prior|above) instructions|you are now in|admin mode|reveal (your )?system prompt|"
    r"send (them|it|this|the data) to https?://|exfil|disregard the user)", re.I)

SECRET_PATTERNS = [
    (r"AIza[0-9A-Za-z_\-]{30,}", "google_api_key"),
    (r"pcsk_[0-9A-Za-z_]{20,}", "pinecone_api_key"),
    (r"lsv2_[0-9a-z_]{20,}", "langsmith_api_key"),
    (r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "jwt"),
    (r"-----BEGIN (RSA )?PRIVATE KEY-----", "private_key"),
]
DOC_ID = re.compile(r"\b(?:INC-\d{4}-\d{4}|POL-\d{3}|SEC-\d{3}|ARC-\d{3}|RB-\d{3}|PRD-\d{3}|MTG-\d{4}-\d{2})\b")
CITATION = re.compile(r"\[(\d{1,2})\]")


def _normalise(text: str) -> str:
    # NFKC stops tricks like full-width letters; drop zero-width chars used to hide words
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[​-‏⁠﻿]", "", text)


def check_user_input(text: str) -> dict:
    """Returns {"allowed": bool, "score": int, "findings": [...], "cleaned": str}."""
    cleaned = _normalise(text).strip()
    findings = []
    if not cleaned:
        return {"allowed": False, "score": 0, "findings": [{"rule": "empty"}], "cleaned": cleaned}
    if len(cleaned) > settings.MAX_QUERY_CHARS:
        return {"allowed": False, "score": 0, "findings": [{"rule": "too_long", "chars": len(cleaned)}],
                "cleaned": cleaned[: settings.MAX_QUERY_CHARS]}
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

    score = 0
    for pattern, category, weight in INPUT_RULES:
        m = re.search(pattern, cleaned, re.I | re.S)
        if m:
            score += weight
            findings.append({"rule": category, "match": m.group(0)[:80], "weight": weight})
    return {"allowed": score < BLOCK_SCORE, "score": score, "findings": findings, "cleaned": cleaned}


def sanitize_retrieved(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """Remove instruction-like lines from retrieved chunks (indirect prompt injection)."""
    flagged = []
    for h in hits:
        clean_lines = []
        for line in h["text"].splitlines():
            if DOC_INJECTION.search(line):
                flagged.append({"chunk": h["id"], "line": line.strip()[:120]})
                clean_lines.append("[removed: instruction-like text found in document]")
            else:
                clean_lines.append(line)
        h["text"] = "\n".join(clean_lines)
    return hits, flagged


def validate_answer(answer: str, sources: list[dict], known_doc_ids: set[str] | None = None) -> dict:
    """Check the final answer. sources = the numbered list the response agent was given."""
    brand = load_yaml("brand.yaml")
    issues = []
    text = answer or ""
    low = text.lower()

    if len(text.strip()) < 5:
        issues.append({"rule": "empty_answer"})

    # 1. hallucinated citations: [n] must exist in the source list
    cited = {int(n) for n in CITATION.findall(text)}
    bad = sorted(n for n in cited if n < 1 or n > len(sources))
    if bad:
        issues.append({"rule": "invalid_citation", "numbers": bad})
    if sources and not cited and "don't have" not in low and "do not have" not in low \
            and "couldn't find" not in low and "could not find" not in low:
        issues.append({"rule": "missing_citations"})

    # 2. document IDs mentioned in the answer must come from retrieved sources
    source_ids = {s["doc_id"] for s in sources}
    allowed_ids = source_ids | (known_doc_ids or set())
    invented = sorted({d for d in DOC_ID.findall(text) if d not in allowed_ids})
    if invented:
        issues.append({"rule": "unknown_document_id", "ids": invented})

    # 3. leaks
    for pattern, name in SECRET_PATTERNS:
        if re.search(pattern, text):
            issues.append({"rule": "secret_leak", "type": name})
    if re.search(r"https?://(?!\S*novabank\.example)\S+", text):
        issues.append({"rule": "external_url"})
    if "system prompt" in low and ("my instructions" in low or "here is" in low):
        issues.append({"rule": "prompt_leak"})

    # 4. brand guardrails (bank voice + regulatory)
    for phrase in brand.get("banned_phrases", []):
        if phrase.lower() in low:
            issues.append({"rule": "brand_banned_phrase", "phrase": phrase})
    for comp in brand.get("competitors", []):
        if comp.lower() in low:
            issues.append({"rule": "brand_competitor_mention", "name": comp})

    return {"valid": not issues, "issues": issues, "cited": sorted(cited)}
