# ADR-006: Money preserves original currency and adds EUR reference values

- **Status:** Accepted; implementation pending
- **Date:** 2026-07-20

## Context

Road trips cross currency zones. Summing PLN, SEK and EUR directly is incorrect, while replacing the paid amount with a converted value loses auditability.

## Decision

Every expense preserves the confirmed original amount and currency.

An optional EUR reference conversion is stored separately with enough provenance to reproduce or explain it:

```text
original amount
original ISO 4217 currency
EUR reference amount
exchange rate
rate date
rate source
conversion mode
conversion timestamp
```

Supported conversion modes are planned as:

- daily rate for the expense date,
- trip-start rate,
- user-defined fixed rate.

The default mode is the daily rate for the expense date. If no trustworthy rate is available, Roadplanner keeps the original expense and excludes it from the EUR total rather than inventing a conversion.

Recalculation never changes the original amount. Manual conversion overrides remain distinguishable from provider rates.

## Consequences

- The cost book can show both `126 PLN` and `≈ 29.43 EUR`.
- Trip totals can be expressed in EUR without losing original payment facts.
- Historical totals remain explainable when rates change later.
- A future exchange-rate provider is replaceable and non-authoritative.

## Rejected alternatives

- Overwrite every expense with EUR.
- Add different currencies without conversion.
- Fetch the current rate every time the UI opens without storing provenance.
