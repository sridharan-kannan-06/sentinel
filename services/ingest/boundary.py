"""The hospital trust boundary.

Everything that enters the system passes through here in a fixed order: screen
the raw text with Model Armor, and only then de-identify it. Nothing downstream
ever receives text that has not been through both steps, which is why `process`
is the only public entry point and the two stages are not separately callable
from outside this module.

Tokenisation is deterministic, so the same person maps to the same token across
weeks. That is what makes correlating a multi-day obligation possible without
holding a name anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache

from google.cloud import dlp_v2, modelarmor_v1, secretmanager

from config import get_settings

# Sensitive Data Protection emits surrogates as INFOTYPE(length):base64. That is
# faithful but unreadable on screen, so each surrogate is aliased to a short
# stable handle like PT-a94f. The alias is derived from the surrogate, so it
# inherits determinism rather than needing its own counter.
SURROGATE = re.compile(r"([A-Z_]+)\((\d+)\):([A-Za-z0-9+/=]+)")

CLINICIAN_PATTERN = r"(?:Dr\.?|Doctor|Consultant)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*"


# Model Armor's prompt injection detection weakens as the injection is diluted by
# surrounding legitimate text. Measured against fixtures/injection_claim.txt: the
# injected paragraph alone (320 characters) is blocked, and the identical
# paragraph inside a plausible 1318 character insurer letter is not. Screening
# that letter in windows finds it at 300 characters per window and misses it at
# 400 and at 600.
#
# So long documents are screened whole and again in overlapping windows, and a
# match in any window blocks the document. The overlap matters: an injection
# straddling a boundary would otherwise be split into halves that each look
# harmless.
#
# This is a mitigation and not a guarantee. An injection spread thinly enough
# would still pass, which is why it is not what the system relies on. What
# actually prevents an injected instruction from closing an obligation is
# structural: the interpreter holds no tools, closure is not an action any agent
# can request, and the Evidence Gate requires an authoritative external fact that
# no document can talk its way past.
WINDOW_CHARS = 300
WINDOW_OVERLAP = 150
WINDOW_THRESHOLD = 700


@dataclass
class ScreeningResult:
    blocked: bool
    match_state: str
    reason: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    windows_screened: int = 1
    matched_window: str | None = None


@dataclass
class DeidentifyResult:
    text: str
    # alias -> full Sensitive Data Protection surrogate. Only services/reid may
    # read this map, and only it can turn a surrogate back into a name.
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def tokens(self) -> list[str]:
        return sorted(self.aliases)


@lru_cache(maxsize=1)
def _dlp() -> dlp_v2.DlpServiceClient:
    return dlp_v2.DlpServiceClient()


@lru_cache(maxsize=1)
def _model_armor() -> modelarmor_v1.ModelArmorClient:
    settings = get_settings()
    return modelarmor_v1.ModelArmorClient(
        client_options={"api_endpoint": f"modelarmor.{settings.region}.rep.googleapis.com"}
    )


@lru_cache(maxsize=1)
def _wrapped_key() -> bytes:
    settings = get_settings()
    client = secretmanager.SecretManagerServiceClient()
    name = (
        f"projects/{settings.project_id}/secrets/"
        f"{settings.wrapped_key_secret}/versions/latest"
    )
    payload = client.access_secret_version(name=name).payload.data.decode("utf-8").strip()
    return base64.b64decode(payload)


def _alias_for(surrogate: str, prefix: str) -> str:
    """Short stable handle for a surrogate.

    Derived from the surrogate itself, so the same person yields the same alias
    on every call without storing a counter or coordinating between instances.
    """
    digest = hashlib.sha256(surrogate.encode("utf-8")).hexdigest()[:4]
    return f"{prefix}-{digest}"


PREFIX_FOR = {
    "PT": "PT",
    "DR": "DR",
    "MRN": "MRN",
}


def windows(text: str) -> list[str]:
    """Overlapping slices of a long document, or the document itself if short."""
    if len(text) <= WINDOW_THRESHOLD:
        return [text]
    step = WINDOW_CHARS - WINDOW_OVERLAP
    slices = [text[start : start + WINDOW_CHARS] for start in range(0, len(text), step)]
    return [s for s in slices if s.strip()]


def _screen(text: str) -> ScreeningResult:
    """Screen the whole document, and long ones window by window as well.

    Screening the whole document first means a short payload costs one call. A
    long one costs one per window on top, which is the price of not being fooled
    by an injection buried in three paragraphs of real correspondence.
    """
    whole = _screen_once(text)
    if whole.blocked or len(text) <= WINDOW_THRESHOLD:
        return whole

    slices = windows(text)
    for index, chunk in enumerate(slices):
        result = _screen_once(chunk)
        if result.blocked:
            return ScreeningResult(
                blocked=True,
                match_state=result.match_state,
                reason=(
                    f"{result.reason} Detected in window {index + 1} of "
                    f"{len(slices)}; the document as a whole did not trigger the "
                    f"filter, which is what a diluted injection looks like."
                ),
                filters=result.filters,
                windows_screened=len(slices) + 1,
                matched_window=chunk.strip()[:400],
            )

    return ScreeningResult(
        blocked=False,
        match_state=whole.match_state,
        filters=whole.filters,
        windows_screened=len(slices) + 1,
    )


def _screen_once(text: str) -> ScreeningResult:
    settings = get_settings()
    template = (
        f"projects/{settings.project_id}/locations/{settings.region}"
        f"/templates/{settings.model_armor_template}"
    )
    response = _model_armor().sanitize_user_prompt(
        request=modelarmor_v1.SanitizeUserPromptRequest(
            name=template,
            user_prompt_data=modelarmor_v1.DataItem(text=text),
        )
    )
    result = response.sanitization_result
    filters: dict[str, str] = {}
    triggered: list[str] = []
    for name, filter_result in result.filter_results.items():
        state = "UNSPECIFIED"
        for attribute in (
            "pi_and_jailbreak_filter_result",
            "malicious_uri_filter_result",
            "sdp_filter_result",
            "csam_filter_filter_result",
        ):
            inner = getattr(filter_result, attribute, None)
            if inner is not None and getattr(inner, "match_state", None) is not None:
                state = inner.match_state.name
                break
        filters[name] = state
        if state == "MATCH_FOUND":
            triggered.append(name)

    blocked = result.filter_match_state.name == "MATCH_FOUND"
    reason = None
    if blocked:
        reason = "Model Armor blocked the payload. Filters triggered: " + ", ".join(
            sorted(triggered) or ["unspecified"]
        )
    return ScreeningResult(
        blocked=blocked,
        match_state=result.filter_match_state.name,
        reason=reason,
        filters=filters,
    )


def _deidentify(text: str) -> DeidentifyResult:
    settings = get_settings()
    crypto_key = {
        "kms_wrapped": {"wrapped_key": _wrapped_key(), "crypto_key_name": settings.kms_key}
    }

    def transformation(info_type: str, surrogate: str) -> dict:
        return {
            "info_types": [{"name": info_type}],
            "primitive_transformation": {
                "crypto_deterministic_config": {
                    "crypto_key": crypto_key,
                    "surrogate_info_type": {"name": surrogate},
                }
            },
        }

    # CLINICIAN_NAME is a custom type so that a doctor becomes DR- rather than
    # PT-. It is declared VERY_LIKELY and its pattern includes the title, so its
    # finding is longer and stronger than the overlapping PERSON_NAME finding.
    custom_info_types = [
        {
            "info_type": {"name": "CLINICIAN_NAME"},
            "regex": {"pattern": CLINICIAN_PATTERN},
            "likelihood": dlp_v2.Likelihood.VERY_LIKELY,
        }
    ]

    inspect_config = {
        "info_types": [
            {"name": "PERSON_NAME"},
            {"name": "MEDICAL_RECORD_NUMBER"},
        ],
        "custom_info_types": custom_info_types,
        "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
    }

    deidentify_config = {
        "info_type_transformations": {
            "transformations": [
                transformation("CLINICIAN_NAME", "DR"),
                transformation("PERSON_NAME", "PT"),
                transformation("MEDICAL_RECORD_NUMBER", "MRN"),
            ]
        }
    }

    response = _dlp().deidentify_content(
        request={
            "parent": f"projects/{settings.project_id}/locations/{settings.region}",
            "deidentify_config": deidentify_config,
            "inspect_config": inspect_config,
            "item": {"value": text},
        }
    )

    aliases: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        surrogate = match.group(0)
        prefix = PREFIX_FOR.get(match.group(1), match.group(1))
        alias = _alias_for(surrogate, prefix)
        aliases[alias] = surrogate
        return alias

    return DeidentifyResult(text=SURROGATE.sub(replace, response.item.value), aliases=aliases)


def process(text: str) -> tuple[ScreeningResult, DeidentifyResult | None]:
    """Screen then de-identify. The only supported way through the boundary.

    Returns the de-identified result only when screening passed. A blocked
    payload yields None so that a caller cannot accidentally forward content
    that Model Armor rejected.
    """
    screening = _screen(text)
    if screening.blocked:
        return screening, None
    return screening, _deidentify(text)
