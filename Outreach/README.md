# Outreach automation

This module reuses the existing LinkedIn browser automation stack to find investor and VC leads for ZapPay.

## What it does

- Logs into LinkedIn with the existing persistent browser session.
- Runs a set of startup-specific investor search queries.
- Extracts likely investor profiles from LinkedIn people search.
- Scores each lead against ZapPay's fintech, payments, mobility, and India focus.
- Writes the results to the existing Google Spreadsheet in a separate worksheet named `VCs`.

## Run

Use the main CLI:

- `python main.py --outreach`
- `python main.py --outreach --max-leads 25`