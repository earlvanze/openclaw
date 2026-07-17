#!/usr/bin/env python3
"""Create a Hemlane lease agreement via GraphQL API."""
import argparse
import json
import re
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests required: pip install requests")
    sys.exit(1)

HEMLANE_API = "https://api.hemlane.com/graphql"
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEASE_DOCS_DIR = WORKSPACE_ROOT / "Dropbox" / "Real Estate" / "Resources" / "Lease Documents"
SKILL_LEASE_DOCS_DIR = SKILL_ROOT / "templates" / "lease-documents"
STANDARD_LATE_FEE_START_DAY = 6
STANDARD_LATE_FEE_INITIAL_CENTS = 2500
STANDARD_LATE_FEE_DAILY_CENTS = 500
STANDARD_LATE_FEE_CAP_RATIO = 0.05
STANDARD_REFUNDABLE_DEPOSIT_BANK = "Thread Bank"

# GraphQL mutation for creating lease agreement
CREATE_LEASE_MUTATION = """
mutation ODCreateLeaseAgreement($input: LeaseAgreementCreateInput!) {
  leaseAgreementCreate(input: $input) {
    error
    leaseAgreement {
      createdAt
      createdByUser {
        id
        __typename
      }
      id
      status
      survey
      tenantGroup {
        id
        __typename
      }
      updatedAt
      __typename
    }
    __typename
  }
}
"""

# GraphQL mutation for creating e-sign packet from lease
CREATE_ESIGN_MUTATION = """
mutation ODCreateLeaseAgreementTemplate($input: EsignDocumentCreateLeaseAgreementTemplateInput!) {
  esignDocumentCreateLeaseAgreementTemplate(input: $input) {
    error
    esignPacket {
      id
      sourceSignable {
        __typename
        ... on LeaseAgreement {
          id
          status
          __typename
        }
      }
      __typename
    }
    __typename
  }
}
"""


def load_auth(auth_file: str) -> dict:
    """Load auth headers from captured auth file."""
    with open(auth_file) as f:
        return json.load(f)


def normalize_disclosure(item) -> dict:
    """Normalize a disclosure entry into Hemlane's {body: text} shape."""
    if isinstance(item, str):
        body = item.strip()
        if not body:
            raise ValueError("Disclosure body cannot be empty")
        return {"body": body}
    if isinstance(item, dict):
        body = item.get("body")
        if isinstance(body, str) and body.strip():
            normalized = dict(item)
            normalized["body"] = body.strip()
            return normalized
    raise ValueError("Each disclosure must be a string or an object with a non-empty body")


def load_disclosures_json(path: str) -> list:
    """Load disclosures from JSON in list, string, or survey-shaped form."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "additionalDisclosures" in data:
        data = data["additionalDisclosures"]
    if not isinstance(data, list):
        data = [data]
    return [normalize_disclosure(item) for item in data]


def load_disclosure_text(path: str | Path) -> dict:
    """Load one unstructured text file as a Hemlane additional disclosure body."""
    path = Path(path)
    body = path.read_text(encoding="utf-8-sig").strip()
    if not body:
        raise ValueError(f"Disclosure text file is empty: {path}")
    return {"body": body}


def parse_numbered_clause_text(path: str | Path) -> list[dict]:
    """Parse a sequentially numbered text file into {title, body} clauses."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    headers = []
    expected = 1
    for idx, line in enumerate(lines):
        match = re.match(r"^(\d+)\.\s+(.+?)\s*$", line)
        if match and int(match.group(1)) == expected:
            headers.append((idx, match.group(2).strip()))
            expected += 1
    clauses = []
    for pos, (line_idx, title) in enumerate(headers):
        body_start = line_idx + 1
        body_end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        body = "\n".join(lines[body_start:body_end]).strip()
        if body:
            clauses.append({"title": title, "body": body})
    return clauses


def load_standard_clauses_text(path: str | Path) -> list[dict]:
    """Parse a numbered lease template text file into Hemlane standard clauses."""
    path = Path(path)
    clauses = parse_numbered_clause_text(path)
    if not clauses:
        raise ValueError(f"No numbered standard clauses found in lease template: {path}")
    return clauses


def load_additional_disclosures_text(path: str | Path) -> list[dict]:
    """Parse numbered disclosures into separate Hemlane disclosure entries."""
    path = Path(path)
    clauses = parse_numbered_clause_text(path)
    if clauses:
        return clauses
    return [load_disclosure_text(path)]


def discover_default_templates() -> dict[str, Path]:
    """Discover saved state lease templates by filename."""
    templates = {}
    for docs_dir in (SKILL_LEASE_DOCS_DIR, DEFAULT_LEASE_DOCS_DIR):
        for path in docs_dir.glob("Hemlane Lease Template - *.txt"):
            state = path.stem.rsplit(" - ", 1)[-1].upper()
            if state:
                templates[state] = path
    return templates


def default_disclosures_path() -> Path:
    """Return the first available default disclosures file."""
    for docs_dir in (DEFAULT_LEASE_DOCS_DIR, SKILL_LEASE_DOCS_DIR):
        path = docs_dir / "Hemlane Lease Disclosures.txt"
        if path.exists():
            return path
    return DEFAULT_LEASE_DOCS_DIR / "Hemlane Lease Disclosures.txt"


def default_template_path_for_state(state: str | None) -> Path | None:
    """Return the saved lease template path for a state."""
    if not state:
        return None
    state = state.upper()
    templates = discover_default_templates()
    template = templates.get(state)
    if not template:
        known = ", ".join(sorted(templates)) or "none"
        raise ValueError(f"No saved Hemlane lease template for state {state}; known states: {known}")
    return template


def build_survey(
    additional_disclosures: list | None = None,
    survey_data: dict | None = None,
    standard_clauses: list | None = None,
) -> dict:
    """Build the Hemlane survey payload without dropping existing clauses/disclosures."""
    survey = dict(survey_data or {})
    existing_clauses = survey.pop("standardClauses", None)
    clauses = []
    if existing_clauses:
        if not isinstance(existing_clauses, list):
            raise ValueError("survey.standardClauses must be a list")
        clauses.extend(existing_clauses)
    if standard_clauses:
        clauses.extend(standard_clauses)
    if clauses:
        survey["standardClauses"] = clauses

    existing = survey.pop("additionalDisclosures", None)
    disclosures = []
    if existing:
        if not isinstance(existing, list):
            existing = [existing]
        disclosures.extend(normalize_disclosure(item) for item in existing)
    if additional_disclosures:
        disclosures.extend(normalize_disclosure(item) for item in additional_disclosures)
    if disclosures:
        survey["additionalDisclosures"] = disclosures
    return survey


def apply_standard_late_fee(survey: dict) -> dict:
    """Apply the standard late-fee policy used for new Hemlane lease drafts."""
    monthly_rent = survey.get("monthlyRentInCents")
    max_amount = None
    if isinstance(monthly_rent, int) and monthly_rent > 0:
        max_amount = round(monthly_rent * STANDARD_LATE_FEE_CAP_RATIO)
    survey.update({
        "lateFeeStatus": "Pending",
        "lateFeeType": "Daily",
        "lateFeeMonthlyAnchor": STANDARD_LATE_FEE_START_DAY,
        "lateFeeAmountInCents": None,
        "lateFeeDailyAmountInCents": STANDARD_LATE_FEE_DAILY_CENTS,
        "lateFeeDailyStartingAmountInCents": STANDARD_LATE_FEE_INITIAL_CENTS,
        "lateFeeMaxAmountInCents": max_amount,
    })
    return survey


def apply_standard_deposit_bank(survey: dict) -> dict:
    """Apply the standard bank name for refundable deposits when omitted."""
    if not survey.get("refundableDepositBank"):
        survey["refundableDepositBank"] = STANDARD_REFUNDABLE_DEPOSIT_BANK
    return survey


def create_lease_agreement(
    tenant_group_id: str,
    auth: dict,
    additional_disclosures: list = None,
    survey_data: dict = None,
    standard_clauses: list = None,
    standard_late_fee: bool = True
) -> dict:
    """Create a lease agreement for a tenant group."""
    headers = {
        "Content-Type": "application/json",
        "x-csrf-token": auth.get("x-csrf-token", ""),
        "cookie": auth.get("cookie", ""),
        "origin": auth.get("origin", "https://www.hemlane.com"),
        "referer": auth.get("referer", "https://www.hemlane.com/"),
        "user-agent": auth.get("user-agent", "Mozilla/5.0 OpenClaw Hemlane Skill"),
    }

    survey = build_survey(additional_disclosures, survey_data, standard_clauses)
    survey = apply_standard_deposit_bank(survey)
    if standard_late_fee:
        survey = apply_standard_late_fee(survey)

    variables = {
        "input": {
            "tenantGroupId": tenant_group_id,
        }
    }
    if survey:
        variables["input"]["survey"] = survey

    response = requests.post(
        HEMLANE_API,
        headers=headers,
        json={
            "operationName": "ODCreateLeaseAgreement",
            "query": CREATE_LEASE_MUTATION,
            "variables": variables,
        },
    )

    return response.json()


def create_esign_packet(
    lease_agreement_id: str,
    auth: dict
) -> dict:
    """Create an e-sign packet from a lease agreement."""
    headers = {
        "Content-Type": "application/json",
        "x-csrf-token": auth.get("x-csrf-token", ""),
        "cookie": auth.get("cookie", ""),
        "origin": auth.get("origin", "https://www.hemlane.com"),
        "referer": auth.get("referer", "https://www.hemlane.com/"),
        "user-agent": auth.get("user-agent", "Mozilla/5.0 OpenClaw Hemlane Skill"),
    }

    response = requests.post(
        HEMLANE_API,
        headers=headers,
        json={
            "operationName": "ODCreateLeaseAgreementTemplate",
            "query": CREATE_ESIGN_MUTATION,
            "variables": {
                "input": {
                    "leaseAgreementId": lease_agreement_id
                }
            },
        },
    )

    return response.json()


def main():
    ap = argparse.ArgumentParser(description="Create Hemlane lease agreement")
    ap.add_argument("--tenant-group-id", required=True, help="Tenant group ID")
    ap.add_argument("--auth-file", required=True, help="Path to auth JSON file")
    ap.add_argument("--state", help="Premises state abbreviation, e.g. NY")
    ap.add_argument("--disclosures", action="append", help="JSON file with additional disclosures; may be repeated")
    ap.add_argument("--disclosures-text", action="append", help="Text file to append as one additional disclosure body")
    ap.add_argument("--lease-template-text", action="append", help="Lease template text file to parse into survey.standardClauses")
    ap.add_argument("--standard-clauses-text", action="append", help="Lease template text file to parse into survey.standardClauses")
    ap.add_argument("--no-default-lease-docs", action="store_true", help="Do not auto-load state default lease/disclosure text")
    ap.add_argument("--allow-empty-additional-disclosures", action="store_true", help="Allow creating a lease with no survey.additionalDisclosures")
    ap.add_argument("--allow-empty-standard-clauses", action="store_true", help="Allow creating a lease with no survey.standardClauses")
    ap.add_argument("--no-standard-late-fee", action="store_true", help="Do not apply the standard late-fee policy")
    ap.add_argument("--survey", help="JSON file with survey data")
    ap.add_argument("--create-esign", action="store_true", help="Create e-sign packet after lease")
    ap.add_argument("--dry-run", action="store_true", help="Print mutation without executing")

    args = ap.parse_args()

    auth = load_auth(args.auth_file)

    # Load optional data
    additional_disclosures = []
    standard_clauses = []
    if args.state and not args.no_default_lease_docs:
        template_path = default_template_path_for_state(args.state)
        disclosures_path = default_disclosures_path()
        if not template_path.exists():
            raise FileNotFoundError(f"Required default lease template not found: {template_path}")
        if not disclosures_path.exists():
            raise FileNotFoundError(f"Required default lease disclosures not found: {disclosures_path}")
        standard_clauses.extend(load_standard_clauses_text(template_path))
        additional_disclosures.extend(load_additional_disclosures_text(disclosures_path))
    for path in (args.lease_template_text or []) + (args.standard_clauses_text or []):
        standard_clauses.extend(load_standard_clauses_text(path))
    for path in args.disclosures_text or []:
        additional_disclosures.extend(load_additional_disclosures_text(path))
    for path in args.disclosures or []:
        additional_disclosures.extend(load_disclosures_json(path))

    survey_data = None
    if args.survey:
        with open(args.survey) as f:
            survey_data = json.load(f)

    survey = build_survey(additional_disclosures, survey_data, standard_clauses)
    survey = apply_standard_deposit_bank(survey)
    if not args.no_standard_late_fee:
        survey = apply_standard_late_fee(survey)
    standard_clause_count = len(survey.get("standardClauses", []))
    disclosure_count = len(survey.get("additionalDisclosures", []))
    if not standard_clause_count and not args.allow_empty_standard_clauses:
        print(
            "Refusing to create Hemlane lease without survey.standardClauses. "
            "Pass --state XX or explicit --lease-template-text/--standard-clauses-text, "
            "or override with --allow-empty-standard-clauses.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not disclosure_count and not args.allow_empty_additional_disclosures:
        print(
            "Refusing to create Hemlane lease without survey.additionalDisclosures. "
            "Pass --state XX or explicit --disclosures-text/--disclosures, "
            "or override with --allow-empty-additional-disclosures.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.dry_run:
        print(f"Would create lease for tenant group: {args.tenant_group_id}")
        print(f"With {standard_clause_count} standard clauses")
        print(f"With {disclosure_count} additional disclosure bodies")
        variables = {
            "input": {
                "tenantGroupId": args.tenant_group_id,
            }
        }
        if survey:
            variables["input"]["survey"] = survey
        print(json.dumps({
            "operationName": "ODCreateLeaseAgreement",
            "query": CREATE_LEASE_MUTATION[:200] + "...",
            "variables": variables
        }, indent=2))
        return

    result = create_lease_agreement(
        args.tenant_group_id,
        auth,
        additional_disclosures,
        survey_data,
        standard_clauses=standard_clauses,
        standard_late_fee=not args.no_standard_late_fee,
    )

    if "errors" in result:
        print(f"Error: {result['errors']}")
        sys.exit(1)

    lease = result.get("data", {}).get("leaseAgreementCreate", {}).get("leaseAgreement", {})
    print(f"Created lease agreement: {lease.get('id')}")
    print(f"Status: {lease.get('status')}")

    if args.create_esign and lease.get("id"):
        esign_result = create_esign_packet(lease["id"], auth)
        if "errors" in esign_result:
            print(f"E-sign error: {esign_result['errors']}")
        else:
            packet = esign_result.get("data", {}).get("esignDocumentCreateLeaseAgreementTemplate", {}).get("esignPacket", {})
            print(f"Created e-sign packet: {packet.get('id')}")


if __name__ == "__main__":
    main()
