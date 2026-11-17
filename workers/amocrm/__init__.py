"""amoCRM export: pushes HOT leads into the CRM as deals.

Uses a long-term token, so there is no OAuth dance here - no code exchange,
no refresh, no 20-minute window. The token is issued once in the
integration card and carries its own expiry (mid-2028 for the current one).

What one lead becomes in amoCRM:

  Сделка   - named after the company, with the lead score in the name so a
             list view sorts usefully
  Контакт  - the person to ask for when one is known (responsible official
             from a госзакупка notice, or the CEO from ЕГРЮЛ), carrying the
             phone and email
  Примечание - why this is a lead, in plain words: the scoring reasons and
             the specific site failures. This is what a caller reads before
             dialling, and it is the whole point of the export.

Idempotent by construction: the core hands out only HOT leads with no
`outreach` row for this channel, and writes that row when the worker reports
the amoCRM deal id back. A run that dies halfway leaves the rest pending
rather than silently skipped.
"""
