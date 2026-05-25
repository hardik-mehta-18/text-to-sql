import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import google.generativeai as genai
from dateutil import parser as dateutil_parser
import json

from app.config import get_settings
from app.utils.gemini_key_manager import get_key_manager
from app.utils.pii_vault import pii_vault

logger = logging.getLogger(__name__)

ResponseMode = Literal["empty", "single", "conversational", "mixed", "table", "paginated"]
PAGE_SIZE = 50


# ── Humanistic system prompt ────────────────────────────────────────────────
CHAT_SYSTEM_PROMPT = """You are a friendly, conversational data assistant embedded in a chat interface.
Your job is to answer the user's question using data from a database query — but you should talk like a 
helpful human colleague, NOT like a formal report or a machine.

Guidelines:
- Answer the question FIRST, directly and confidently. Lead with the insight.
- Use "you", "your", "I found", "looks like", "turns out" — natural conversational language.
- Vary your openers. Don't always start with "I found" or "Based on". Mix it up naturally.
- Mention specific numbers and names from the data — be concrete, not vague.
- If something is surprising or notable, say so! ("Wow, that's higher than expected" / "Interesting —")
- Keep it SHORT. 1-3 sentences for small data, 2-4 sentences for bigger summaries.
- Do NOT use bullet points, headers, or markdown formatting.
- Do NOT repeat the question back to the user.
- Do NOT say "the database returned" or "the query resulted in" — that's robotic.
- Do NOT start with "Certainly!", "Sure!", "Of course!" or other filler affirmations.
- Sound like a knowledgeable friend who just looked something up for you."""


# ── Response openers for variety ────────────────────────────────────────────
SINGLE_VALUE_OPENERS = [
    "So,", "Looks like", "That comes out to", "Your answer:", "Right, so —",
    "Turns out,", "Quick answer:", "Got it —",
]


# ── Universal date formatter ─────────────────────────────────────────────────

def _parse_and_format_date(value: Any) -> Any:
    """
    Try to parse `value` as a date/datetime and return it formatted as dd-mm-yyyy.

    Uses python-dateutil (dateutil.parser.parse) instead of a hardcoded format list,
    so it handles virtually any date/datetime string automatically — ISO 8601, slash-
    separated, dot-separated, with or without time, with or without timezone, etc.

    Safe guards:
    - Non-string / non-datetime values are returned unchanged.
    - Pure integers and decimals are skipped (they'd be misread as timestamps).
    - Strings with no digit cluster that looks date-like are skipped quickly.
    - Any parse failure silently returns the original value.

    dayfirst=True ensures ambiguous dates like "01/02/2024" are read as
    01-Feb-2024 (dd/mm) rather than Jan-02-2024 (mm/dd).
    """
    if value is None:
        return value

    # Native datetime object from a DB driver — format directly
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")

    # Only strings beyond this point
    if not isinstance(value, str):
        return value

    raw = value.strip()
    if not raw:
        return value

    # Quick pre-filter: must look vaguely date-like
    # (4-digit year OR two-digit groups separated by / . -)
    if not re.search(r'\d{4}|\d{2}[\/\.\-]\d{2}', raw):
        return value

    # Skip bare numbers — they would be silently mis-parsed as timestamps
    if re.fullmatch(r'\d+(\.\d+)?', raw):
        return value

    try:
        dt = dateutil_parser.parse(raw, dayfirst=True)
        return dt.strftime("%d-%m-%Y")
    except Exception:
        # Not a recognisable date — return the original value untouched
        return value


def _clean_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    - Converts boolean values to Yes/No
    (ID removal is handled by _humanize_and_filter_columns_dynamic via LLM)
    """
    cleaned_rows = []
    for row in rows:
        new_row = {}
        for key, value in row.items():
            if isinstance(value, bool):
                new_row[key] = "Yes" if value else "No"
            else:
                new_row[key] = value
        cleaned_rows.append(new_row)
    return cleaned_rows


def _clean_text_summary(text: str) -> str:
    """
    - Removes UUID-like patterns
    - Converts true/false words to Yes/No
    """
    if not text:
        return text

    # ❌ Remove UUIDs (like 0700F872-E8EF-...)
    text = re.sub(
        r'\b[0-9a-fA-F]{8}-[0-9a-fA-F\-]{27,}\b',
        '',
        text
    )

    # 🔁 Convert true/false → Yes/No (case insensitive)
    text = re.sub(r'\btrue\b', 'Yes', text, flags=re.IGNORECASE)
    text = re.sub(r'\bfalse\b', 'No', text, flags=re.IGNORECASE)

    # Cleanup extra spaces
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def _format_dates_in_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Walk every cell in every row and apply date formatting where applicable.
    Returns a new list of rows; originals are not mutated.
    """
    return [
        {key: _parse_and_format_date(val) for key, val in row.items()}
        for row in rows
    ]


@dataclass
class FormattedResponse:
    mode: ResponseMode
    text_summary: str
    table_data: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    total_rows: int = 0
    stats: Dict[str, Any] = field(default_factory=dict)
    visualization_hint: str = "none"
    chart_x_axis: str = ""
    chart_y_axis: str = ""
    sql_used: str = ""
    explanation: str = ""
    page: int = 1
    pages_total: int = 1


class SmartFormatter:
    """Formats raw query results into humanistic, conversational chat responses."""

    def __init__(self):
        self.settings = get_settings()
        self._opener_index = 0  # Rotate openers to avoid repetition

    def _next_opener(self, openers: list) -> str:
        opener = openers[self._opener_index % len(openers)]
        self._opener_index += 1
        return opener

    async def format(
        self,
        question: str,
        rows: List[Dict[str, Any]],
        columns: List[str],
        sql: str,
        explanation: str = "",
        page: int = 1,
        response_intent: str = "data",
        session_id: str = None,
        vault_map: Dict[str, str] = None
    ) -> FormattedResponse:
        """
        Choose display mode and generate a human, chat-style response.

        Thresholds:
          0 rows       -> empty  (friendly "nothing found" message)
          1 row, 1 col -> single value (one punchy sentence)
          1-3 rows     -> conversational (Gemini paragraph, like a friend telling you)
          4-20 rows    -> mixed (short insight + table)
          21+ rows     -> table/paginated (stats summary, no Gemini)
        """
        logger.info(f"fetched Intent : {response_intent}")
        logger.info(f"Session Id : {session_id}")
        total = len(rows)
        if vault_map is None:
            vault_map = {}

        # Normalise any date/datetime cell to dd-mm-yyyy before anything else
        rows = _format_dates_in_rows(rows)

        rows = _clean_rows(rows)

        # Apply PII Pseudonymization mapping
        masked_rows = pii_vault.anonymize_rows(rows, vault_map)

        logger.info(f"masked_rows: {masked_rows}")
        # Let the LLM dynamically decide which columns to show and how to name them
        # Pass masked_rows so Gemini doesn't see real PII
        columns, masked_rows_filtered = await self._humanize_and_filter_columns_dynamic(columns, masked_rows, question)
        
        # Existence intent → just confirm yes/no with brief description, no table dump
        if response_intent == "existence":
            summary = await self._humanize_existence(question, masked_rows_filtered, columns, total)
            logger.info(f"Existence summary before de-anonymization: {summary}")
            summary = pii_vault.deanonymize(summary, vault_map)
            logger.info(f"Existance Summary : {summary}")
            summary = _clean_text_summary(summary)
            logger.info(f"Existence summary: {summary}")
            return FormattedResponse(
                mode="conversational",
                text_summary=summary,
                total_rows=total,
                sql_used=sql,
                explanation=explanation,
            )

        if total == 0:
            summary = await self._humanize_no_data(question)
            return FormattedResponse(
                mode="empty",
                text_summary=summary,
                sql_used=sql,
                explanation=explanation,
            )

        if total == 1 and len(columns) == 1:
            val = masked_rows_filtered[0].get(columns[0])
            summary = await self._humanize_single(question, columns[0], val)
            logger.info(f"Single value summary before encryption: {summary}")
            summary = pii_vault.deanonymize(summary, vault_map)
            logger.info(f"Single value summary: {summary}")
            return FormattedResponse(
                mode="single",
                text_summary=summary,
                table_data=rows, # Real rows to UI
                columns=columns,
                total_rows=1,
                sql_used=sql,
                explanation=explanation,
                visualization_hint="number",
            )

        stats = self._compute_stats(rows, columns) # Compute stats on real data
        viz = self._determine_viz(question, rows, columns, stats)
        logger.info(f"Visualization : {viz}")
        x_axis, y_axis = "", ""
        if viz in ("chart", "pie_chart"):
            x_axis, y_axis = await self._get_chart_axes(question, columns, stats)
            logger.info(f"X axis : {x_axis} and Y axis : {y_axis}")
            prompt = f"{CHAT_SYSTEM_PROMPT}\n\nUser asked: \"{question}\"\nData shows {y_axis} by {x_axis}.\nWrite a clean, professional, and completely non-technical title for this chart. MAX 5 WORDS. NO QUOTES. ONLY the title."
            fallback_title = f"{y_axis} by {x_axis}".replace('_', ' ').title()
            chart_title = await self._gemini_call(prompt, fallback=fallback_title)
            stats["_chart_title"] = chart_title.replace('"', '').strip()

        if total <= 3:
            summary = await self._humanize_rows(question, masked_rows_filtered, columns)
            logger.info(f"Conversational summary before de-anonymization: {summary}")
            summary = pii_vault.deanonymize(summary, vault_map)
            logger.info(f"Conversational summary: {summary}")
            return FormattedResponse(
                mode="conversational",
                text_summary=summary,
                table_data=rows, # Real rows to UI
                columns=columns,
                total_rows=total,
                stats=stats,
                visualization_hint=viz,
                chart_x_axis=x_axis,
                chart_y_axis=y_axis,
                sql_used=sql,
                explanation=explanation,
            )

        if total <= 20:
            summary = await self._generate_mixed_summary(question, masked_rows_filtered, columns, stats, total)
            logger.info(f"Mixed summary before de-anonymization: {summary}")
            summary = pii_vault.deanonymize(summary, vault_map)
            logger.info(f"Mixed summary: {summary}")
            return FormattedResponse(
                mode="mixed",
                text_summary=summary,
                table_data=rows, # Real rows to UI
                columns=columns,
                total_rows=total,
                stats=stats,
                visualization_hint=viz,
                chart_x_axis=x_axis,
                chart_y_axis=y_axis,
                sql_used=sql,
                explanation=explanation,
            )

        # 21+ rows: no Gemini call, friendly stats summary
        paginated_rows, pages_total = self._paginate(rows, page)
        stats_summary = self._friendly_stats_summary(total, stats, question)
        mode: ResponseMode = "paginated" if pages_total > 1 else "table"

        return FormattedResponse(
            mode=mode,
            text_summary=stats_summary,
            table_data=paginated_rows,
            columns=columns,
            total_rows=total,
            stats=stats,
            visualization_hint=viz,
            chart_x_axis=x_axis,
            chart_y_axis=y_axis,
            sql_used=sql,
            explanation=explanation,
            page=page,
            pages_total=pages_total,
        )

    # ── Gemini helpers (humanistic prompts) ────────────────────────────────

    async def _get_chart_axes(self, question: str, columns: List[str], stats: Dict) -> tuple[str, str]:
        logger.info(f"Columns : {columns}")
        prompt = f"""{CHAT_SYSTEM_PROMPT}

The user asked: "{question}"
Available Columns: {', '.join(columns)}

You need to pick the best X and Y axes for a chart.
Rules:
1. X-axis is usually a category, date, or short name.
2. Y-axis MUST be a numeric column containing quantities or amounts.
3. CRITICAL: NEVER pick long text or paragraph-based description columns for either axis. The chart will instantly break.
4. Return ONLY a single line with the two exact column names separated by a comma (X,Y). No other text.
"""
        fallback_y = next((c for c, s in stats.items() if isinstance(s, dict) and s.get("type") == "numeric"), columns[0])
        fallback_x = next((c for c in columns if c != fallback_y), columns[0])
        fallback = f"{fallback_x},{fallback_y}"
        
        try:
            resp = await self._gemini_call(prompt, fallback=fallback)
            parts = [p.strip() for p in resp.split(",")]
            if len(parts) >= 2 and parts[0] in columns and parts[1] in columns:
                return parts[0], parts[1]
        except Exception:
            pass
        return fallback_x, fallback_y

    async def _humanize_single(self, question: str, col: str, val: Any) -> str:
        """One punchy sentence answering the user's question with the single value."""
        prompt = f"""{CHAT_SYSTEM_PROMPT}

The user asked: "{question}"
The database came back with exactly one value: {col} = {val}

Write ONE short, direct sentence answering their question. Use the actual value. 
Be conversational — like you're texting a colleague the answer."""

        fallback = f"{self._next_opener(SINGLE_VALUE_OPENERS)} {col} is {val}."
        return await self._gemini_call(prompt, fallback=fallback)

    async def _humanize_no_data(self, question: str, existence_reason: Optional[str] = None) -> str:
        """Generate a warm, conversational no-data response using Gemini."""
        base_context = (
            "The database query ran successfully but returned absolutely no results. "
            "Write 1-2 short, friendly sentences telling the user no data was found... "
            "Suggest they try rephrasing, broadening filters, or checking if the data exists."
        )

        if existence_reason:
            base_context = (
                f"CRITICAL DISCOVERY: {existence_reason}\n"
                "You MUST directly inform the user that the primary entity they are looking for "
                "does NOT exist in the database at all! Do not say 'no data was found', say "
                "that the specific person, item, or entity they asked about is completely missing from the system."
            )

        prompt = f"""{CHAT_SYSTEM_PROMPT}

                    The user asked: "{question}"
                    {base_context}
                    
                    Sound warm and helpful, not robotic. Do NOT say 
                    "the database returned 0 rows" or anything technical like that.
                """

        fallback = "Hmm, I couldn't find anything for that. Maybe try broadening your filters or rephrasing the question?"
        return await self._gemini_call(prompt, fallback=fallback)

    async def _humanize_error(self, question: str) -> str:
        """Generate a warm, conversational error response using Gemini."""
        prompt = f"""{CHAT_SYSTEM_PROMPT}

The user asked: "{question}"

The system was unable to answer the question due to complexity or an internal error.

Write 1-2 short, friendly sentences apologizing that you couldn't pull the data right now. Suggest they try rephrasing the question slightly. Let them know they can try again. Sound warm and helpful, NOT robotic. 
Do NOT mention "SQL", "database errors", "max retries", "internal error", or anything technical."""
        
        fallback = "I hit a snag trying to pull that data right now. Could you try rephrasing your question or asking in a different way?"
        return await self._gemini_call(prompt, fallback=fallback)

    async def _humanize_permission_error(self, question: str) -> str:
        """Generate a friendly response when user doesn't have access to the data."""
        
        prompt = f"""{CHAT_SYSTEM_PROMPT}

            The user asked: "{question}"

            The system could not return results because the user does not have permission to access this data.

            Write 1-2 short, friendly sentences explaining that the data can't be shown due to access restrictions. 
            Suggest they try a different request or contact the admin if needed. 
            Sound polite, helpful, and natural — NOT technical.

            DO NOT mention:
            - SQL
            - filters
            - permissions logic
            - security rules
            - internal systems
            - errors

            Keep it simple and user-friendly.
        """

        fallback = (
            "I’m not able to show that information right now due to access restrictions. "
            "You can try a different request, or reach out to your admin if you think you should have access."
        )

        return await self._gemini_call(prompt, fallback=fallback)

    async def _humanize_system_down(self) -> str:
        """Generate a friendly response when the AI service is unavailable (API key issues, quota, etc)."""
        # Note: We don't use Gemini here because this is called when Gemini is likely DOWN.
        return "I'm sorry, but my AI services are currently unavailable due to system maintenance or technical issues. Please try again in a few minutes."

    async def _humanize_rows(self, question: str, rows: List[Dict], columns: List[str]) -> str:
        rows_text = "\n".join(
            ", ".join(f"{k}: {v}" for k, v in row.items()) for row in rows
        )
        prompt = f"""{CHAT_SYSTEM_PROMPT}

    The user asked: "{question}"
    The query returned {len(rows)} result(s). Here is the data:
    {rows_text}

    IMPORTANT: This data IS the answer. Do NOT say no data was found.
    Do NOT re-evaluate or question the data. Just describe what you see naturally.
    Write 1-3 natural sentences directly answering their question using the actual values.
    Don't use bullet points. Sound like you're telling a friend what you found."""

        fallback = "; ".join(
            ", ".join(f"{k}: {v}" for k, v in row.items()) for row in rows
        )
        return await self._gemini_call(prompt, fallback=fallback)

    async def _generate_existence_negative(self,question: str) -> str:
        """
        When an existence query returns 0 rows, generate a natural
        'No, X is not Y' response instead of a generic 'no data found'.
        """
        prompt = f"""The user asked: "{question}"
    A SQL query was executed to check this condition and returned 0 rows,
    meaning the condition is definitively NOT true.

    Write a single short, natural, conversational sentence that directly
    answers NO to the user's question.

    Examples:
    - "Is Hardik on maternity leave?"  → "No, Hardik is not currently on maternity leave."
    - "Are there any incidents at Site A?" → "There are no incidents recorded at Site A."
    - "Is John active?"  → "John does not appear to be active in the system."
    - "Does Sarah have any risk assessments?" → "Sarah has no risk assessments on record."

    Return ONLY the final sentence. No preamble, no explanation."""

        try:
            response = await get_key_manager().generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=80,
                ),
            )
            return response.text.strip()
        except Exception:
            return "Based on the available data, the answer to your question appears to be no."
            
    async def _generate_mixed_summary(
        self,
        question: str,
        rows: List[Dict],
        columns: List[str],
        stats: Dict,
        total: int,
    ) -> str:
        """4-20 rows: a brief insight + the table follows. Like a quick briefing."""
        sample_text = "\n".join(
            ", ".join(f"{k}: {v}" for k, v in row.items()) for row in rows[:5]
        )
        stats_text = self._stats_text(stats)

        prompt = f"""{CHAT_SYSTEM_PROMPT}

            The user asked: "{question}"
            There are {total} results total. Here are the first few rows:
            {sample_text}

            {stats_text}

            IMPORTANT: This data IS the answer. Do NOT say no data was found. Do NOT re-evaluate.
            Write 1-2 sentences giving the KEY insight from this data.
            The full table will be shown below — just give the most interesting takeaway."""

        fallback = f"Found {total} records — here's the full breakdown:"
        return await self._gemini_call(prompt, fallback=fallback)

    async def _gemini_call(self, prompt: str, fallback: str) -> str:
        try:
            response = await get_key_manager().generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.7,       # slightly warmer for natural tone
                    max_output_tokens=300,
                ),
            )
            return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini formatter call failed: {e}")
            return fallback


    def _friendly_stats_summary(self, total: int, stats: Dict, question: str) -> str:
        """
        For 21+ rows: build a friendly stats summary without Gemini.
        Reads like a quick brief, not a report header.
        """
        parts = []

        # Lead with count in a natural way
        if total > 1000:
            parts.append(f"That's a big one — {total:,} records matching your query.")
        elif total > 100:
            parts.append(f"Found {total:,} results for you.")
        else:
            parts.append(f"Got {total} results.")

        # Add the most interesting stat (highest variation / biggest range)
        best_stat = None
        best_range = 0
        for col, s in stats.items():
            if not isinstance(s, dict): continue
            if s.get("type") == "numeric" and s["max"] != s["min"]:
                r = s["max"] - s["min"]
                if r > best_range:
                    best_range = r
                    best_stat = (col, s)

        if best_stat:
            col, s = best_stat
            parts.append(
                f"{col.replace('_', ' ').title()} ranges from {s['min']:,} to {s['max']:,}, "
                f"averaging {s['avg']:,}."
            )

        # Add category insight if present
        for col, s in stats.items():
            if not isinstance(s, dict): continue
            if s.get("type") == "categorical" and s["unique_count"] > 1:
                parts.append(
                    f"There are {s['unique_count']} unique {col.replace('_', ' ')} values."
                )
                break  # just one categorical note is enough

        return " ".join(parts)

    def _compute_stats(self, rows: List[Dict], columns: List[str]) -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        for col in columns:
            values = [r[col] for r in rows if r.get(col) is not None]
            if not values:
                continue
            if all(isinstance(v, (int, float)) for v in values):
                stats[col] = {
                    "type": "numeric",
                    "min": min(values),
                    "max": max(values),
                    "avg": round(sum(values) / len(values), 2),
                    "sum": round(sum(values), 2),
                    "count": len(values),
                }
            else:
                uniq = list({str(v) for v in values})
                stats[col] = {
                    "type": "categorical",
                    "unique_count": len(uniq),
                    "sample_values": uniq[:5],
                }
        return stats

    def _stats_text(self, stats: Dict) -> str:
        """Build a compact stats block for Gemini context (not shown to user)."""
        lines = []
        for col, s in stats.items():
            if not isinstance(s, dict): continue
            if s.get("type") == "numeric":
                lines.append(
                    f"  {col}: min={s['min']}, max={s['max']}, avg={s['avg']}, sum={s['sum']}"
                )
            else:
                lines.append(
                    f"  {col}: {s['unique_count']} unique values, e.g. {', '.join(s['sample_values'][:3])}"
                )
        return ("Data stats:\n" + "\n".join(lines)) if lines else ""

    def _determine_viz(self, question: str, rows: List[Dict], columns: List[str], stats: Optional[Dict] = None) -> str:
        q_lower = question.lower()
        wants_pie = "pie" in q_lower
        wants_chart = any(w in q_lower for w in ["chart", "graph", "plot", "visualize"])

        if wants_pie:
            return "pie_chart"
        elif wants_chart:
            return "chart"
        elif len(rows) == 1 and len(columns) == 1:
            return "number"
        else:
            return "table"

    def _paginate(self, rows: List[Dict], page: int):
        pages_total = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(1, min(page, pages_total))
        start = (page - 1) * PAGE_SIZE
        return rows[start: start + PAGE_SIZE], pages_total

    async def _humanize_existence(self, question: str, rows: List[Dict], columns: List[str], total: int) -> str:
        """User asked if data exists — answer yes/no with a brief description."""
        sample_text = "\n".join(
            ", ".join(f"{k}: {v}" for k, v in row.items()) for row in rows[:3]
        )

        prompt = f"""{CHAT_SYSTEM_PROMPT}

    The user asked: "{question}"
    The database found {total} matching record(s). Here are a few examples:
    {sample_text}

    The user is asking WHETHER this data exists, not asking to see all of it.
    Write 1-2 sentences confirming YES the data exists, mention how many records 
    were found, and give a brief natural description using the sample values.
    Sound like a helpful colleague confirming something for them."""

        fallback = f"Yes, that data exists! Found {total} matching record(s) in the database."
        return await self._gemini_call(prompt, fallback=fallback)

    async def _humanize_and_filter_columns_dynamic(self, columns: List[str], rows: List[Dict[str, Any]], question: str):
        if not columns or not rows:
            return columns, rows

        sample_row = rows[0]
        sample_context = {k: str(v)[:50] for k, v in sample_row.items()}

        prompt = f"""You are formatting data for a non-technical user.
User question: "{question}"
Raw columns: {columns}
Sample data: {sample_context}

Return ONLY a valid JSON object mapping the exact original column name to either:
- null (if the column is a technical field like 'id', 'created_at', 'updated_at', 'is_deleted', backend ids, timestamps that are irrelevant to the question)
- a human-readable title-cased string (e.g. "firstname" -> "First Name", "total_sales" -> "Total Sales") if it's useful to the user.
Do not output markdown or backticks."""

        mapping = None
        try:
            response = await get_key_manager().generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=1000,
                ),
            )
            raw_text = response.text.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            
#             import json
            mapping = json.loads(raw_text.strip())
        except Exception as e:
            logger.warning(f"Error calling LLM for dynamic column formatting: {e}")

        if not mapping:
            return columns, rows

        # Normalize mapping keys to lowercase for case-insensitive lookup
        # since LLMs often return keys in different casing than the DB columns
        norm_mapping = {k.lower(): v for k, v in mapping.items()}

        new_columns = []
        for col in columns:
            mapped_val = norm_mapping.get(col.lower())
            if mapped_val is not None:
                new_columns.append(mapped_val)

        if not new_columns:
            return columns, rows

        new_rows = []
        for row in rows:
            new_row = {}
            for col in columns:
                mapped_val = norm_mapping.get(col.lower())
                if mapped_val is not None:
                    new_row[mapped_val] = row.get(col)
            new_rows.append(new_row)

        return new_columns, new_rows