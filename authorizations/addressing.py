import re

from django.core.exceptions import ValidationError


STATE_PROVINCE_NORMALIZATION = {
    "OR": "Oregon",
    "OREGON": "Oregon",
    "WA": "Washington",
    "WASHINGTON": "Washington",
    "ID": "Idaho",
    "IDAHO": "Idaho",
    "BC": "British Columbia",
    "B.C.": "British Columbia",
    "BRITISH COLUMBIA": "British Columbia",
}
COUNTRY_NORMALIZATION = {
    "USA": "United States",
    "US": "United States",
    "U.S.": "United States",
    "UNITED STATES": "United States",
    "UNITED STATES OF AMERICA": "United States",
    "CAN": "Canada",
    "CA": "Canada",
    "CANADA": "Canada",
}
US_STATES = {"Oregon", "Washington", "Idaho"}
CANADIAN_PROVINCE = "British Columbia"
US_AN_TIR_POSTAL_PREFIXES = ("97", "98", "990", "991", "992", "993", "994", "835", "838")
POSTAL_PREFIXES_BY_STATE_PROVINCE = {
    "Oregon": ("97",),
    "Washington": ("98", "990", "991", "992", "993", "994"),
    "Idaho": ("835", "838"),
    "British Columbia": ("V",),
}

US_POSTAL_CODE_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
CANADIAN_POSTAL_CODE_RE = re.compile(
    r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] \d[ABCEGHJ-NPRSTV-Z]\d$"
)


def normalize_state_province(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return STATE_PROVINCE_NORMALIZATION.get(raw.upper(), raw.title())


def normalize_country(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return COUNTRY_NORMALIZATION.get(raw.upper(), "")


def jurisdiction_for_state(state_province):
    normalized = normalize_state_province(state_province)
    if normalized == CANADIAN_PROVINCE:
        return "Canada"
    if normalized in US_STATES:
        return "United States"
    return ""


def normalize_postal_code(value, *, required=True):
    raw = str(value or "").strip().upper()
    if not raw:
        if required:
            raise ValidationError("Postal code is required.")
        return ""

    compact = re.sub(r"[\s-]", "", raw)
    if compact.isdigit():
        if len(compact) == 5:
            return compact
        if len(compact) == 9:
            return f"{compact[:5]}-{compact[5:]}"
    if len(compact) == 6 and re.fullmatch(
        r"[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d",
        compact,
    ):
        return f"{compact[:3]} {compact[3:]}"

    raise ValidationError(
        "Enter a valid five-digit ZIP code, optional ZIP+4, or Canadian postal code."
    )


def postal_code_jurisdiction(postal_code):
    if US_POSTAL_CODE_RE.fullmatch(postal_code or ""):
        return "United States"
    if CANADIAN_POSTAL_CODE_RE.fullmatch(postal_code or ""):
        return "Canada"
    return ""


def postal_code_within_an_tir(postal_code):
    jurisdiction = postal_code_jurisdiction(postal_code)
    if jurisdiction == "United States":
        return postal_code.startswith(US_AN_TIR_POSTAL_PREFIXES)
    if jurisdiction == "Canada":
        return postal_code.startswith("V")
    return False


def postal_code_matches_state(postal_code, state_province):
    normalized_state = normalize_state_province(state_province)
    prefixes = POSTAL_PREFIXES_BY_STATE_PROVINCE.get(normalized_state)
    if not prefixes:
        return False
    return postal_code.startswith(prefixes)


def validate_postal_code_for_state(value, state_province, *, require_an_tir=True):
    postal_code = normalize_postal_code(value)
    state_jurisdiction = jurisdiction_for_state(state_province)
    postal_jurisdiction = postal_code_jurisdiction(postal_code)

    if state_jurisdiction and (
        postal_jurisdiction != state_jurisdiction
        or not postal_code_matches_state(postal_code, state_province)
    ):
        raise ValidationError("Postal code does not match the selected state/province.")
    if require_an_tir and not postal_code_within_an_tir(postal_code):
        raise ValidationError("Postal code must be within An Tir.")
    return postal_code
