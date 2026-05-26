import json
import logging
from dataclasses import dataclass
from typing import Optional
 
from app.utils.llm_provider import generate_with_fallback
 
logger = logging.getLogger(__name__)
 
 
@dataclass
class CorrectedQuestion:
    """Result from the corrector — corrected text plus optional user-entity metadata."""
    text: str                          # spell-corrected question
    user_entity_name: Optional[str]    # e.g. "Louis" if a person's name was detected
    has_user_reference: bool           # True if question is about a person / user
 
 
class QuestionCorrector:
    """Corrects spelling/grammar in user questions and detects person/user references."""
 
    async def correct(self, question: str) -> CorrectedQuestion:
        """Return a spell-corrected question and detect any user/person references.
 
        Detection rules (handled by Gemini):
        - Fixes typos in common English words only.
        - Detects if the question mentions a specific person by name
          (e.g. "Louis incidents", "show me Sarah's records").
        - Does NOT change proper nouns / names.
        - Returns structured JSON so both results come from one LLM call.
        """
        prompt = f"""
You are a spell-checker and entity detector for a database query assistant.
 
The user has typed a question that will be converted into SQL.
 
YOUR TWO JOBS:
1. Fix spelling mistakes in common English words only.
2. Detect if the question refers to a specific person / user by name.
 
SPELLING RULES:
1. Fix spelling mistakes in common English words only (e.g. "incedent" → "incident").
2. Do NOT change proper nouns — names of people, places, or organisations must stay EXACTLY as written.
3. Do NOT rephrase, reword, or change the meaning.
4. Do NOT add or remove words unless fixing a clear typo in a common word.
5. Numbers, dates, and domain-specific terms must be preserved exactly.
6. If unsure whether something is a name or a typo → leave it unchanged.
 
USER ENTITY DETECTION RULES:
- Set has_user_reference=true if the question is asking about a specific named person
  (e.g. "Louis incidents", "show records for Sarah", "breakdown of John's activity").
- Set user_entity_name to the detected person's name exactly as written (e.g. "Louis").
- Set has_user_reference=false if the question is about generic user types
  (e.g. "show all staff", "list visitors") — no specific name present.
- Set has_user_reference=false if no person is mentioned at all.
 
USER QUESTION:
{question}
 
Respond ONLY with this JSON (no markdown, no explanation):
{{
  "corrected": "<corrected question as plain text>",
  "has_user_reference": true or false,
  "user_entity_name": "<name if detected, else null>"
}}
"""
        try:
            raw = await generate_with_fallback(
                prompt,
                temperature=0.0,
                max_output_tokens=256,
                label="spelling_corrector_json"
            )
 
            # Strip markdown fences if present
            for fence in ("```json", "```"):
                if raw.startswith(fence):
                    raw = raw[len(fence):]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
 
            data = json.loads(raw)
            corrected_text = data.get("corrected", "").strip() or question
 
            # Safety: if response is suspiciously long, fall back to original
            if len(corrected_text) > len(question) * 3:
                logger.warning("Corrector returned unexpectedly long output, using original.")
                corrected_text = question
 
            if corrected_text != question:
                logger.info(f"Question corrected: '{question}' → '{corrected_text}'")
 
            result = CorrectedQuestion(
                text=corrected_text,
                has_user_reference=bool(data.get("has_user_reference", False)),
                user_entity_name=data.get("user_entity_name") or None,
            )
            logger.info(
                f"[corrector] has_user_reference={result.has_user_reference}, "
                f"user_entity_name={result.user_entity_name!r}"
            )
            return result
 
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Corrector JSON parse failed ({e}), falling back to plain correct.")
            return await self._fallback_correct(question)
 
        except Exception as e:
            logger.warning(f"Spell correction failed, using original question: {e}")
            return CorrectedQuestion(text=question, has_user_reference=False, user_entity_name=None)
 
    # ------------------------------------------------------------------
    # Fallback: plain text correction (original behaviour) if JSON fails
    # ------------------------------------------------------------------
    async def _fallback_correct(self, question: str) -> CorrectedQuestion:
        prompt = f"""
You are a spell-checker for a database query assistant.
Fix obvious spelling mistakes in common English words only.
Do NOT change proper nouns, names, numbers, or domain terms.
If nothing needs fixing, return the question exactly as-is.
 
USER QUESTION:
{question}
 
Return ONLY the corrected question as plain text. No explanation. No quotes. No JSON.
"""
        try:
            corrected = await generate_with_fallback(
                prompt,
                temperature=0.0,
                max_output_tokens=256,
                label="spelling_corrector_fallback"
            )
            if not corrected or len(corrected) > len(question) * 3:
                corrected = question
            return CorrectedQuestion(text=corrected, has_user_reference=False, user_entity_name=None)
        except Exception:
            return CorrectedQuestion(text=question, has_user_reference=False, user_entity_name=None)
