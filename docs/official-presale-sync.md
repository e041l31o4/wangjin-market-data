# Official presale project prices

The v2 public feed is `data/official_presale_project_feed.json`. It is separate from
the reviewed-only v1 feed, which remains available for existing clients.

## Recurring update

The existing Update Kaohsiung Market Data workflow downloads official data at
10:20 Asia/Taipei on the 2nd, 12th and 22nd and supports workflow_dispatch.
It then rebuilds v2 from project_transactions.json. Importer, v2-builder or registry
changes on main also trigger an update. New exact official matches update without
per-transaction reapproval.

## What qualifies

- A unique registry ID and official project name + district, with whitespace/width/case normalization only.
- Registry kind is 預售屋. No completed-home first-sale inference.
- Each included transaction has its official ID, matching internal hash, location in the same city/district, and MOI source.
- Duplicate official IDs, different locations for the same identity, unrecognizable names and invalid values are held.
- Missing-evidence records are excluded and disclosed, never invented or silently counted.
- No fuzzy matching, developer-prefix removal or automatic alias mapping.

The UI labels these as 官方案名配對, not independent base-site or coordinate verification.
Prices reflect the collected traceable sample, not all historical sales. Averages
are the arithmetic mean of individual unit prices. Latest-day prices are shown as
a range if several transactions share that day.

## Registry maintenance

data/project_identity_register.json records the 386-name website snapshot and kind.
A new project or a change of name, district, address or kind requires a reviewed
registry change. A mismatch in the website's ID/name/district/kind/address hides the
price instead of applying it to an imported unrelated project.

## Current export

2026-10-18: 56 presale identities / 859 traceable sample transactions. These are
generated counts, not fixed test expectations. See analysis/official-presale-match-audit.json
for exclusions. Non-matches are not evidence of no sales.
