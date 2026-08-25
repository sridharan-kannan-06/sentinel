"""The evidence corpus the ablation is measured against.

Each sample is one obligation and one fact offered as proof it was discharged,
with the ground truth recorded alongside. Ground truth here is not a judgement
call: it follows from the five rules in policy/evidence.yaml, which were written
before any of these samples existed.

The interesting samples are the plausible ones. Evidence that says "confirmed
with the insurer over the phone" reads exactly like completion and is not
completion, because a phone call leaves nothing anybody can audit. Those are the
samples that separate a deterministic gate from a model asked whether something
looks done.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Sample:
    obligation_type: str
    subject_token: str
    required_evidence: str
    source: str
    external_reference: str
    assertion: str
    observed_offset_hours: float
    should_close: bool
    category: str
    why: str


OPENED_HOURS_AGO = 6.0

# One qualifying fact per obligation type, taken from a system that actually
# records the thing in question.
QUALIFYING = {
    "critical_result_acknowledgement": (
        "Electronic acknowledgement record from the ordering clinician in the LIS",
        "lis",
        "ACK-99213",
        "Critical result acknowledged by DR-0385 at the bedside terminal",
    ),
    "insurance_preauthorisation": (
        "Insurer pre-authorisation reference number issued for the subject",
        "insurer_portal",
        "PA-2026-118422",
        "Pre-authorisation approved, reference issued by the insurer",
    ),
    "claim_document_chase": (
        "Discharge summary uploaded to the document management system",
        "document_management_system",
        "DOC-55401",
        "Discharge summary uploaded and attached to the claim",
    ),
    "discharge_blocker": (
        "Pharmacy reconciliation signed off for the subject",
        "pharmacy_system",
        "RX-77120",
        "Medication reconciliation completed and signed",
    ),
    "referral_followup": (
        "Specialist appointment confirmed for the subject",
        "appointment_system",
        "APT-30918",
        "Specialist appointment confirmed for 2 September",
    ),
}

SUBJECTS = ["PT-8119", "PT-a94f", "PT-4c72", "PT-91de", "PT-2b60"]


def build() -> list[Sample]:
    samples: list[Sample] = []

    for index, (obligation_type, (required, source, reference, assertion)) in enumerate(
        QUALIFYING.items()
    ):
        subject = SUBJECTS[index % len(SUBJECTS)]

        def make(**overrides) -> Sample:
            base = {
                "obligation_type": obligation_type,
                "subject_token": subject,
                "required_evidence": required,
                "source": source,
                "external_reference": reference,
                "assertion": assertion,
                "observed_offset_hours": -1.0,
                "should_close": False,
                "category": "",
                "why": "",
            }
            base.update(overrides)
            return Sample(**base)

        samples.append(
            make(
                should_close=True,
                category="qualifying",
                why="Authoritative source, reference present, in window, right subject, "
                "assertion describes the requirement.",
            )
        )

        # Someone said it was done. Saying so is not a record of it being done.
        samples.append(
            make(
                source="verbal_report",
                external_reference="",
                assertion="Confirmed complete with the department over the phone",
                category="plausible_but_unauditable",
                why="Reads like completion and leaves nothing anybody can check later.",
            )
        )

        # An email from a real person is not the system that records the fact.
        samples.append(
            make(
                source="staff_email",
                external_reference="EMAIL-8821",
                assertion=f"{assertion}, per the ward sister",
                category="wrong_source",
                why="Not on the authoritative source list for this obligation type.",
            )
        )

        # The right system, the right words, the wrong person.
        samples.append(
            make(
                subject_token="PT-0000",
                category="wrong_subject",
                why="Closes the wrong person's obligation. The most dangerous failure "
                "available to the system.",
            )
        )

        # A real record, but of something that happened before this obligation existed.
        samples.append(
            make(
                observed_offset_hours=-(OPENED_HOURS_AGO + 3),
                category="predates_obligation",
                why="Describes earlier work, not this obligation.",
            )
        )

        # A real record from the right system that does not say the thing was done.
        samples.append(
            make(
                assertion="Patient transferred to ward 4 for observation",
                category="wrong_assertion",
                why="Authoritative and irrelevant. Something happened; not this.",
            )
        )

    return samples


# Sample.subject_token is the subject the *evidence* names. The obligation's own
# subject is fixed per type, so the wrong_subject case is simply the one where
# the two differ.
def obligation_subject(obligation_type: str) -> str:
    return SUBJECTS[list(QUALIFYING).index(obligation_type) % len(SUBJECTS)]


def as_obligation(sample: Sample, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "id": f"OBL-eval-{sample.obligation_type[:6]}-{sample.category[:12]}",
        "type": sample.obligation_type,
        "subject_token": obligation_subject(sample.obligation_type),
        "required_evidence": sample.required_evidence,
        "created_at": now - timedelta(hours=OPENED_HOURS_AGO),
        "deadline": now + timedelta(hours=2),
    }


def observed_at(sample: Sample, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(hours=sample.observed_offset_hours)
