---
name: hemlane
description: Draft Hemlane lease agreements from saved state templates and split disclosure clauses. Use when creating or auditing Hemlane lease drafts, e-sign packets, state-specific standard clauses, additional disclosures, late-fee settings, or deposit-bank defaults.
---

# Hemlane Lease Drafting

Use this skill for Hemlane lease-generation workflows that must preserve saved
lease templates, additional disclosures, and standard payment/deposit rules.

## Auth Rules

- Treat cookies, bearer tokens, CSRF tokens, session identifiers, and captured
  auth JSON files as runtime secrets.
- Do not store live tokens, auth files, tenant IDs, application IDs, or lease IDs
  in this skill.
- Use env vars or ephemeral local files such as `/tmp/hemlane-auth.json` when
  replaying captured requests.

## Default Workflow

Use `scripts/create_hemlane_lease.py` for lease generation:

```bash
python3 skills/hemlane/scripts/create_hemlane_lease.py \
  --tenant-group-id "<tenant-group-id>" \
  --auth-file /tmp/hemlane-auth.json \
  --state NY \
  --create-esign
```

For every state with a saved Hemlane template, always include the saved lease
text before generating the draft or e-sign packet. Passing `--state XX`
auto-loads:

- `Dropbox/Real Estate/Resources/Lease Documents/Hemlane Lease Template - XX.txt`
- `Dropbox/Real Estate/Resources/Lease Documents/Hemlane Lease Disclosures.txt`

When the canonical workspace files are not present, the wrapper can load the
same defaults from `skills/hemlane/templates/lease-documents/`. Keep this
skill-local template folder synchronized with the canonical resource files
before pushing workflow changes.

Saved state templates currently exist for AR, CO, FL, IL, NY, OH, and TN. The
state template file must be parsed into `survey.standardClauses`. The shared
`Hemlane Lease Disclosures.txt` file must be parsed into separate
`survey.additionalDisclosures` entries, one entry per top-level numbered
disclosure. Do not insert the entire disclosures file as one unformatted body.

Do not create an e-sign packet until the dry-run or live request shows both
sections populated. If auditing a draft created before this rule, inspect
`leaseAgreement.survey`; if `standardClauses` is missing, empty, still using
Hemlane defaults instead of the saved state template, or if
`additionalDisclosures` contains one large numbered disclosure block, do not send
it to tenants. Revert/recreate or update the draft with the state template in
`standardClauses` and split shared disclosures in `additionalDisclosures`.

Keep inserted behavioral/maintenance standard clauses in logical lease order.
For NY, OH, and IL templates, `Drug Free Housing` belongs immediately after
`Use of Premises`, and `Plumbing Stoppage & Drain Maintenance` belongs
immediately after `Maintenance & Repairs`.

Late fees are standardized by default: $25 on the 6th day after rent is due,
then $5 per day, capped at 5% of monthly rent. In Hemlane survey terms, use
`lateFeeStatus: "Pending"`, `lateFeeType: "Daily"`,
`lateFeeMonthlyAnchor: 6`, `lateFeeAmountInCents: null`,
`lateFeeDailyStartingAmountInCents: 2500`,
`lateFeeDailyAmountInCents: 500`, and `lateFeeMaxAmountInCents` equal to 5%
of `monthlyRentInCents`. Use `--no-standard-late-fee` only for a deliberate,
documented exception.

Set `refundableDepositBank` to `Thread Bank` unless the user gives a different
deposit-holding institution.

The wrapper refuses empty `survey.standardClauses` and empty
`survey.additionalDisclosures` by default. Use `--allow-empty-standard-clauses`
or `--allow-empty-additional-disclosures` only for a deliberate, documented
exception.

## Files

- `scripts/create_hemlane_lease.py` - GraphQL lease/e-sign wrapper with dry-run
  validation, template parsing, disclosure splitting, late-fee defaults, and
  deposit-bank defaults.
- `templates/lease-documents/` - saved generic lease templates and shared
  disclosure text for AR, CO, FL, IL, NY, OH, and TN.
