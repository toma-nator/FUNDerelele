"""
One-off script: extract transactions from TD PDF statements and write a CSV.
Usage: python extract_pdf_csv.py
Output: extracted_transactions.csv
"""
import io, re, csv, sys
from datetime import datetime

_MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
_MONEY2 = r'-?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)'
_BUY_SELL_TAIL = re.compile(
    r'\s+(-?\d+)\s+(\d+\.\d{3})\s+(' + _MONEY2 + r')\s+(' + _MONEY2 + r')\s*$'
)
# Fallback for "Pending activity" section: no Cash Balance column
_BUY_SELL_TAIL_NOCASH = re.compile(
    r'\s+(-?\d+)\s+(\d+\.\d{3})\s+(' + _MONEY2 + r')\s*$'
)
_DIV_WHT_TAIL = re.compile(
    r'\s+(-?\d+)\s+(' + _MONEY2 + r')\s+(' + _MONEY2 + r')\s*$'
)
_CASH_TAIL = re.compile(
    r'\s+(' + _MONEY2 + r')\s+(' + _MONEY2 + r')\s*$'
)
_DATE_LINE = re.compile(
    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:\s+(.*))?$',
    re.IGNORECASE
)

PDF_FILES = [
    "Statement_59WBM0 Part1.pdf",
    "Statement_59WBM0 Part2.pdf",
]

all_rows = []
skipped_lines = []

import pdfplumber

for pdf_path in PDF_FILES:
    print(f"\n=== Processing: {pdf_path} ===")
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            if not text.strip():
                continue

            year_m = re.search(r'statement:\s+\w+\s+\d+,\s+(\d{4})', text, re.I)
            if not year_m:
                year_m = re.search(r'\b(20\d{2})\b', text)
            if not year_m:
                print(f"  Page {page_num}: no year found, skipping")
                continue
            year = int(year_m.group(1))

            account_name = None
            num_m = re.search(r'Account number:\s*(\S+)', text)
            typ_m = re.search(r'Account type:\s*(.+)', text)
            if num_m and typ_m:
                account_name = f"{typ_m.group(1).strip()} ({num_m.group(1).strip()})"

            sec_m = re.search(
                r'(?:Pending activity|Date\s+Activity\s+Description).*?\n(.*?)(?=(?:Your \w+ contribution|'
                r'Ending cash balance|Details of investment|Order-Execution-Only|\Z))',
                text, re.DOTALL
            )
            if not sec_m:
                continue

            lines = [l.strip() for l in sec_m.group(1).split('\n') if l.strip()]
            chunks = []
            cur = None
            for line in lines:
                dm = _DATE_LINE.match(line)
                if dm:
                    if cur:
                        chunks.append(cur)
                    cur = {'mon': dm.group(1), 'day': int(dm.group(2)),
                           'first': dm.group(3) or '', 'extra': []}
                elif cur:
                    cur['extra'].append(line)
            if cur:
                chunks.append(cur)

            print(f"  Page {page_num} (year={year}): {len(chunks)} transaction chunks found")

            for chunk in chunks:
                first = chunk['first']
                extra = chunk['extra']
                fl = first.lower()

                if fl.startswith('buy'):
                    action, tail = 'Buy', first[3:].strip()
                elif fl.startswith('sell'):
                    action, tail = 'Sell', first[4:].strip()
                elif fl.startswith('dividends'):
                    action, tail = 'DIV', first[9:].strip()
                elif fl.startswith('dividend '):
                    action, tail = 'DIV', first[8:].strip()
                elif fl.startswith('withholding tax'):
                    action, tail = 'WHTX02', first[15:].strip()
                elif fl.startswith('contribution'):
                    action, tail = 'CONT', first[12:].strip()
                elif fl.startswith('canada disability'):
                    combined = (first + ' ' + ' '.join(extra)).lower()
                    action = 'CDSG' if 'savings grant' in combined else 'CDSB'
                    tail = first[17:].strip()
                elif fl.startswith('reinvested distribution'):
                    action, tail = 'DIV', first[23:].strip()
                elif fl.startswith('gst charged'):
                    action, tail = 'FEE', first[11:].strip()
                elif fl.startswith('admin fee'):
                    action, tail = 'FEE', first[9:].strip()
                else:
                    skipped_lines.append(f"  SKIPPED (p{page_num}): {chunk['mon']} {chunk['day']}: {first[:80]}")
                    continue

                qty = price = amount = cash_bal = None
                desc_first = tail

                if action in ('Buy', 'Sell'):
                    m = _BUY_SELL_TAIL.search(tail)
                    if m:
                        qty    = abs(int(m.group(1)))
                        price  = float(m.group(2))
                        amount = float(m.group(3).replace(',', ''))
                        cash_bal = float(m.group(4).replace(',', ''))
                        desc_first = tail[:m.start()].strip()
                    else:
                        m = _BUY_SELL_TAIL_NOCASH.search(tail)
                        if m:
                            qty    = abs(int(m.group(1)))
                            price  = float(m.group(2))
                            amount = float(m.group(3).replace(',', ''))
                            cash_bal = None
                            desc_first = tail[:m.start()].strip()
                        else:
                            m = _DIV_WHT_TAIL.search(tail)
                            if m:
                                qty    = abs(int(m.group(1)))
                                amount = float(m.group(2).replace(',', ''))
                                cash_bal = float(m.group(3).replace(',', ''))
                                desc_first = tail[:m.start()].strip()
                            else:
                                skipped_lines.append(f"  NO-PARSE (p{page_num}): {chunk['mon']} {chunk['day']} {action}: {tail[:80]}")
                                continue

                elif action in ('DIV', 'WHTX02'):
                    m = _DIV_WHT_TAIL.search(tail)
                    if m:
                        qty    = abs(int(m.group(1)))
                        amount = float(m.group(2).replace(',', ''))
                        cash_bal = float(m.group(3).replace(',', ''))
                        desc_first = tail[:m.start()].strip()
                    else:
                        m = _CASH_TAIL.search(tail)
                        if m:
                            amount = float(m.group(1).replace(',', ''))
                            cash_bal = float(m.group(2).replace(',', ''))
                            desc_first = tail[:m.start()].strip()
                        else:
                            skipped_lines.append(f"  NO-PARSE (p{page_num}): {chunk['mon']} {chunk['day']} {action}: {tail[:80]}")
                            continue

                else:  # CONT, CDSG, CDSB, FEE
                    m = _CASH_TAIL.search(tail)
                    if m:
                        amount = float(m.group(1).replace(',', ''))
                        cash_bal = float(m.group(2).replace(',', ''))
                        desc_first = tail[:m.start()].strip()
                    else:
                        skipped_lines.append(f"  NO-PARSE (p{page_num}): {chunk['mon']} {chunk['day']} {action}: {tail[:80]}")
                        continue

                if action in ('CDSG', 'CDSB'):
                    desc_extra = [l for l in extra if 'savings' not in l.lower()]
                else:
                    desc_extra = extra
                description = ' '.join(filter(None, [desc_first] + desc_extra))

                month = _MONTH_MAP.get(chunk['mon'].lower())
                if not month:
                    continue
                try:
                    txn_date = datetime(year, month, chunk['day']).date()
                except ValueError:
                    continue

                # Back-calculate commission for Buy/Sell
                commission_str = ''
                if action in ('Buy', 'Sell') and qty is not None and price is not None and amount is not None:
                    is_usd = 'CONV TO CAD' in description.upper()
                    if is_usd:
                        commission_str = '9.99'
                    else:
                        calc = round(abs(amount) - qty * price, 2)
                        commission_str = str(calc) if 0 < calc < 50 else '9.99'

                all_rows.append({
                    'Trade Date':   txn_date.strftime('%Y-%m-%d'),
                    'Settle Date':  txn_date.strftime('%Y-%m-%d'),
                    'Description':  description,
                    'Action':       action,
                    'Quantity':     str(qty)      if qty      is not None else '',
                    'Price':        str(price)    if price    is not None else '',
                    'Commission':   commission_str,
                    'Net Amount':   str(amount)   if amount   is not None else '',
                    'Cash Balance': str(cash_bal) if cash_bal is not None else '',
                    'Currency':     'CAD',
                    'Account':      account_name or '',
                    'Source PDF':   pdf_path,
                })

# Sort by date
all_rows.sort(key=lambda r: r['Trade Date'])

# Deduplicate: same date + action + qty + price = same transaction
# (pending-section rows duplicate settled-section rows for the same trade)
seen = set()
deduped = []
for r in all_rows:
    key = (r['Trade Date'], r['Action'], r['Quantity'], r['Price'], r['Net Amount'])
    if key not in seen:
        seen.add(key)
        deduped.append(r)
    else:
        print(f"  DEDUP removed: {r['Trade Date']} {r['Action']} qty={r['Quantity']} price={r['Price']} — {r['Description'][:50]}")
all_rows = deduped

out_file = 'extracted_transactions.csv'
fieldnames = ['Trade Date', 'Settle Date', 'Description', 'Action',
              'Quantity', 'Price', 'Commission', 'Net Amount',
              'Cash Balance', 'Currency', 'Account', 'Source PDF']

with open(out_file, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(all_rows)

print(f"\n=== DONE ===")
print(f"Total transactions extracted: {len(all_rows)}")
print(f"Output written to: {out_file}")

if skipped_lines:
    print(f"\nSkipped / unparsed lines ({len(skipped_lines)}):")
    for s in skipped_lines:
        print(s)
else:
    print("No skipped lines.")
