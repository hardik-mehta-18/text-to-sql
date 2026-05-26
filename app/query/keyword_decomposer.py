# app/query/keyword_decomposer.py
import json
from app.utils.llm_provider import generate_with_fallback

async def decompose_to_keywords(question: str) -> list[str]:
    """Lightweight LLM call — returns 4-6 search keywords from the question."""
    prompt = (
        "Extract 4-6 short search keywords from this database question to help "
        "find relevant tables and columns. Focus on entity names, column concepts, "
        "and filter values.\n"
        "Return ONLY a JSON array of strings. No explanation.\n\n"
        f"Question: {question}"
    )
    try:
        resp_str = await generate_with_fallback(
            prompt,
            temperature=0,
            max_output_tokens=100,
            label="keyword_decomposer"
        )
        return json.loads(resp_str)
    except Exception:
        return [question]   # fallback: full question as single keyword