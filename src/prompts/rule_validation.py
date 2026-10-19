"""
Rule Validation Prompt - Bug Fixes and Edge Cases
This prompt is used AFTER extraction to validate and fix common errors.
Should be run with Flash model for cost efficiency.
"""

RULE_VALIDATION_PROMPT = """
You are a QA Validator for extracted GNN rules. Your task is to review and FIX common errors in rule JSON.

# CONTEXT
You will receive a JSON rule extracted from an EU law. Check for and fix the following issues.

# VALIDATION RULES

## 1. TARGET VARIABLE BY NODE TYPE
- PRODUCER/TRADER nodes have: `opex_cost`, `production_volume`, `capex`, `trade_volume`.
- CONSUMER nodes have: `purchase_price`, `living_expenses`, `consumption_volume`.
- **FIX**: If a rule has CONSUMER in node_types but uses `opex_cost`, change to `purchase_price`.

## 2. SUPPLY BALANCE vs HARD LIMIT
- "Specific Supply Arrangement" or "Quota for subsidy eligibility" = **SOFT** limit.
- These mean: "First X tonnes get subsidy, rest pay normal price" NOT "Banned after X tonnes".
- **FIX**: If summary mentions "supply arrangement", "eligible quantity", or "subsidy threshold" but strictness is HARD, change to SOFT.

## 3. DATE FORMAT
- ALL dates MUST be ISO 8601 strings: "YYYY-MM-DD" or null.
- **FIX**: If you see a Unix timestamp (integer like 1735689600), convert to ISO string.
- If activation_date is null or missing, preserve as null. DO NOT invent dates (do NOT use "1900-01-01" or today).

## 4. NULL vs ZERO VALUES
- Use `null` for "no limit specified" or "unlimited".
- Use `0.0` ONLY for "banned", "prohibited", or "zero quota".
- **FIX**: If value is 0 but summary doesn't mention "ban" or "prohibit", verify intent.

## 5. GEOGRAPHIC EXCEPTIONS
- If rule applies ONLY to specific territories:
  - Canary Islands → "ES-CN"
  - Azores → "PT-20"
  - Madeira → "PT-30"
  - French overseas → "FR-RE", "FR-GP", etc.
- **FIX**: If summary mentions these regions but regions array is ["EU"], update to specific code.

## 6. CRISIS/EMERGENCY RULES
- If summary mentions "emergency", "crisis", "may activate":
- **FIX**: Set `parse_confidence` to 0.7 (not 1.0).

## 7. AMENDMENTS
- If background_context mentions "amending Regulation [NUMBER]":
- **FIX**: Add "AMENDS: 3YYYYR####" (CELEX format) to background_context if not present.

# OUTPUT FORMAT
Return the CORRECTED JSON rule. If no fixes needed, return the rule unchanged.
Only modify fields that have errors. DO NOT change correct values.

Return strictly valid JSON with no comments.
"""


# Python post-processing functions (deterministic fixes, no LLM needed)
def fix_ecu_to_eur(rule: dict, law_id: str) -> dict:
    """Convert ECU to EUR for pre-1999 regulations (1:1 rate)."""
    try:
        year = int(law_id[1:5]) if law_id[0] == '3' else 2000
        if year < 1999:
            unit = rule.get('mechanism', {}).get('unit', '')
            if unit and 'ECU' in unit:
                rule['mechanism']['unit'] = unit.replace('ECU', 'EUR')
    except (ValueError, IndexError):
        pass
    return rule


def fix_date_format(rule: dict) -> dict:
    """Ensure dates are ISO strings, not timestamps. Preserves null activation dates."""
    timeline = rule.get('timeline', {})
    
    # Check activation_date
    act_date = timeline.get('activation_date')
    if isinstance(act_date, int):
        from datetime import datetime, timezone
        timeline['activation_date'] = datetime.fromtimestamp(act_date, tz=timezone.utc).strftime('%Y-%m-%d')
    elif act_date is not None:
        s = str(act_date).strip()
        if not s or s.lower() in ("null", "none") or s.startswith("1900-01-01") or s.startswith("1970-01-01"):
            timeline['activation_date'] = None
        else:
            timeline['activation_date'] = s
    
    # Check expiration_date
    exp_date = timeline.get('expiration_date')
    if isinstance(exp_date, int):
        from datetime import datetime, timezone
        timeline['expiration_date'] = datetime.fromtimestamp(exp_date, tz=timezone.utc).strftime('%Y-%m-%d')
    elif exp_date is not None:
        s = str(exp_date).strip()
        if not s or s.lower() in ("null", "none") or s.startswith("1900-01-01") or s.startswith("1970-01-01"):
            timeline['expiration_date'] = None
        else:
            timeline['expiration_date'] = s
    
    rule['timeline'] = timeline
    return rule


def validate_rule_python(rule: dict, law_id: str) -> dict:
    """Apply all Python-based deterministic fixes."""
    rule = fix_ecu_to_eur(rule, law_id)
    rule = fix_date_format(rule)
    return rule


# PRO REVIEW PROMPT - Used for second-stage validation by Pro model
# Input: ONLY the JSON draft from Flash (not full law text)
# This saves ~90% on input tokens for Pro
RULE_REVIEW_PROMPT = """
You are a senior legal analyst reviewing extracted GNN rules for logical errors against the official source text.

## SOURCE TEXT
{source_text}

## INPUT
A JSON array of rules extracted by a junior analyst. Your job is to verify against the SOURCE TEXT and FIX errors, not re-extract.

## COMMON ERRORS TO FIX

1. **TARGET VARIABLE MISMATCH**:
   - CONSUMER nodes → use "purchase_price" or "consumption_volume", NOT "opex_cost"
   - PRODUCER/TRADER nodes → use "opex_cost", "production_volume", NOT "purchase_price"

2. **STRICTNESS LOGIC**:
   - "supply arrangement", "eligible quota", "subsidy threshold" → **SOFT** (not HARD)
   - "ban", "embargo", "prohibition" → **HARD**

3. **DATES**: Must be "YYYY-MM-DD" strings or null. If missing activation_date, keep null (do NOT invent "1900-01-01" or January 1st for year-only dates).

4. **VALUES**: `null` = unlimited or qualitative/reporting obligation, `0.0` = banned/prohibited only.

5. **REGIONS**: Canary Islands="ES-CN", Azores="PT-20", Madeira="PT-30"

## OUTPUT FORMAT
Return corrected JSON array. If no errors, return unchanged. Do NOT add or remove rules.

```json
{{"rules": [...]}}
```

## RULES TO REVIEW:
{rules_json}
"""
