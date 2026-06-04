"""
CSV and PDF importers for TD Direct Investing and CIBC Investor's Edge exports.
Each parser normalises rows into Transaction objects.
"""
import io
import re
import csv
from datetime import datetime
from models import db, Transaction, Account


def parse_file_path(filepath, broker='auto'):
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        content = f.read()
    return _parse_content(content, broker)


def parse_upload(file_storage, broker='auto'):
    filename = (file_storage.filename or '').lower()
    if filename.endswith('.pdf'):
        return _import_td_pdf(file_storage.read())
    content = file_storage.read().decode('utf-8-sig', errors='replace')
    return _parse_content(content, broker)


def _parse_content(content, broker='auto'):
    lines = content.splitlines()

    # TD Direct Investing files have 3 metadata rows before the real headers.
    # Detect by scanning for the "Trade Date" header row in the first 10 lines.
    td_header_idx = None
    td_account_name = None
    for i, line in enumerate(lines[:10]):
        if re.match(r'^"?Trade Date"?,', line):
            td_header_idx = i
        if re.match(r'^"?Account"?,', line):
            parts = line.split(',')
            if len(parts) > 1:
                td_account_name = parts[1].strip().strip('"')

    if td_header_idx is not None:
        real_content = '\n'.join(lines[td_header_idx:])
        rows = list(csv.DictReader(io.StringIO(real_content)))
        if not rows:
            raise ValueError('TD file has a header but no data rows.')
        return _import_td(rows, account_name=td_account_name)

    # Standard path for CIBC and any future brokers
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        raise ValueError('File is empty or not a valid CSV.')

    if broker == 'auto':
        broker = _detect_broker(rows[0])

    if broker == 'td':
        return _import_td(rows)
    elif broker == 'cibc':
        return _import_cibc(rows)
    else:
        raise ValueError(f'Unknown broker format: {broker}. Please select TD or CIBC manually.')


def _detect_broker(first_row):
    keys = {k.lower().strip() for k in first_row.keys()}
    if 'settle date' in keys or 'settlement date' in keys:
        if 'action' in keys:
            return 'td'
    if 'transaction date' in keys and 'activity type' in keys:
        return 'cibc'
    return 'unknown'


def _parse_date(s, fmts):
    for fmt in fmts:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except Exception:
            pass
    raise ValueError(f'Cannot parse date: {s!r}')


def _ensure_account(name):
    acct = Account.query.filter_by(name=name).first()
    if not acct:
        db.session.add(Account(name=name, type='Non-Reg', cash_balance=0))
        db.session.commit()


def _td_ticker_from_desc(desc):
    """
    Clean a TD description into a usable ticker key, then check TickerMap for
    a user-defined mapping. Returns the mapped ticker if found, otherwise the
    cleaned description (which will show as unmapped on the Import page).
    """
    from models import TickerMap
    d = desc.strip().strip('"')
    d = re.sub(r'\s+CONV(?:ERT)?\s+TO\s+CAD\s+@.*', '', d, flags=re.IGNORECASE)
    d = re.sub(r'\s+CDR\s+C\$HDG', '', d, flags=re.IGNORECASE)
    d = re.sub(r'\s+[A-Z]{2}-\d+\s*$', '', d)
    d = re.sub(r'-NEW\s*$', '', d, flags=re.IGNORECASE)
    cleaned = d.strip().upper()[:40] or 'UNKNOWN'

    mapping = TickerMap.query.get(cleaned)
    return mapping.ticker if mapping else cleaned


def _import_td(rows, account_name=None):
    """
    TD Direct Investing CSV export.
    Columns: Trade Date, Settle Date, Description, Action, Quantity, Price,
             Commission, Net Amount, Security Type, Currency
    The account name is parsed from the metadata header, not a column.
    """
    count = 0
    date_fmts = ['%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y', '%d-%b-%y']

    action_map = {
        'buy': 'Buy',
        'sell': 'Sell',
        'div': 'Dividend',
        'dividend': 'Dividend',
        'split': 'Split',    # SPLIT adds shares at $0; CXLSPL cancels a split with negative qty
        'cxlspl': 'Split',
        'whtx02': 'WithholdingTax',
    }
    cash_subtypes = {
        'cont': 'Contribution',
        'cdsg': 'RDSP Grant',
        'cdsb': 'RDSP Bond',
    }
    fee_actions = {'fee', 'gstcharged', 'adminfee'}
    cash_actions = set(cash_subtypes)
    # Account transfers require source/dest mapping not available in TD CSV
    skip_actions = {'tfr', 'tfri', 'tfro'}

    default_account = account_name or 'TD Direct Investing'
    _ensure_account(default_account)

    for row in rows:
        action_raw = row.get('Action', '').strip()
        action_key = action_raw.lower()

        if action_key in skip_actions:
            continue

        # Fees (GST, admin fees) — negative cash outflows
        if action_key in fee_actions:
            try:
                date_str = (row.get('Trade Date', '') or row.get('Settle Date', '') or '').strip()
                txn_date = _parse_date(date_str, date_fmts)
                net_raw = (row.get('Net Amount', '') or '0').replace(',', '').strip()
                amount = abs(float(net_raw)) if net_raw else 0.0
                if amount == 0:
                    continue
                desc = (row.get('Description', '') or '').strip()
                existing = Transaction.query.filter_by(
                    date=txn_date, type='Fee', net_cad=-amount
                ).first()
                if existing:
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker='CASH', account=default_account,
                    type='Fee', qty=0, price=0, currency='CAD',
                    amount_native=amount, amount_cad=amount,
                    fees_cad=0, net_cad=-amount, notes=desc,
                ))
                count += 1
            except Exception:
                pass
            continue

        # Cash deposits / government grants
        if action_key in cash_actions:
            try:
                date_str = (row.get('Trade Date', '') or row.get('Settle Date', '') or '').strip()
                txn_date = _parse_date(date_str, date_fmts)
                net_raw = (row.get('Net Amount', '') or '0').replace(',', '').strip()
                amount = abs(float(net_raw)) if net_raw else 0.0
                if amount == 0:
                    continue
                desc = (row.get('Description', '') or '').strip()
                existing = Transaction.query.filter_by(
                    date=txn_date, type='Deposit', net_cad=amount
                ).first()
                if existing:
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker='CASH', account=default_account,
                    type='Deposit', qty=0, price=0, currency='CAD',
                    amount_native=amount, amount_cad=amount,
                    fees_cad=0, net_cad=amount, notes=desc,
                    subtype=cash_subtypes[action_key],
                ))
                count += 1
            except Exception:
                pass
            continue

        txn_type = action_map.get(action_key)
        if not txn_type:
            continue

        desc = row.get('Description', '') or ''

        # Skip Buy/Sell cancellation reversal rows — they zero out a prior entry
        if txn_type in ('Buy', 'Sell') and re.search(r'CANCELLATION OF', desc, re.IGNORECASE):
            continue

        # Ticker: use Symbol column if present, otherwise extract from Description
        ticker = (row.get('Symbol', '') or '').strip().upper()
        if not ticker:
            ticker = _td_ticker_from_desc(desc)
        if not ticker or ticker == 'UNKNOWN':
            continue

        try:
            date_str = (row.get('Trade Date', '') or row.get('Settle Date', '') or '').strip()
            txn_date = _parse_date(date_str, date_fmts)
            currency = (row.get('Currency', 'CAD') or 'CAD').strip().upper() or 'CAD'

            if txn_type == 'Split':
                # Preserve sign: CXLSPL rows have negative qty (reversal of a prior split)
                qty_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
                qty = float(qty_raw) if qty_raw else 0.0
                if qty == 0:
                    continue
                existing = Transaction.query.filter_by(
                    date=txn_date, ticker=ticker, type='Split', qty=qty, price=0
                ).first()
                if existing:
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker=ticker, account=default_account,
                    type='Split', qty=qty, price=0, currency=currency,
                    amount_native=0, amount_cad=0, fees_cad=0, net_cad=0,
                ))
                count += 1

            elif txn_type == 'Dividend':
                # TD doesn't provide per-share price; the total amount is in Net Amount
                net_raw = (row.get('Net Amount', '') or '0').replace(',', '').strip()
                amount = abs(float(net_raw)) if net_raw else 0.0
                if amount == 0:
                    continue
                qty_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
                qty = abs(float(qty_raw)) if qty_raw else 0.0
                existing = Transaction.query.filter_by(
                    date=txn_date, ticker=ticker, type='Dividend', qty=qty, price=0
                ).first()
                if existing:
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker=ticker, account=default_account,
                    type='Dividend', qty=qty, price=0, currency=currency,
                    amount_native=amount, amount_cad=amount, fees_cad=0, net_cad=amount,
                ))
                count += 1

            elif txn_type == 'WithholdingTax':
                net_raw = (row.get('Net Amount', '') or '0').replace(',', '').strip()
                amount = abs(float(net_raw)) if net_raw else 0.0
                if amount == 0:
                    continue
                qty_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
                qty = abs(float(qty_raw)) if qty_raw else 0.0
                existing = Transaction.query.filter_by(
                    date=txn_date, ticker=ticker, type='WithholdingTax', qty=qty, price=0
                ).first()
                if existing:
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker=ticker, account=default_account,
                    type='WithholdingTax', qty=qty, price=0, currency=currency,
                    amount_native=amount, amount_cad=amount, fees_cad=0, net_cad=-amount,
                ))
                count += 1

            else:  # Buy, Sell
                qty_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
                qty = abs(float(qty_raw)) if qty_raw else 0.0
                price_raw = (row.get('Price', '') or '0').replace(',', '').strip()
                price = abs(float(price_raw)) if price_raw else 0.0
                comm_raw = (row.get('Commission', '') or '0').replace(',', '').strip()
                fees = abs(float(comm_raw)) if comm_raw else 0.0
                amount_native = qty * price
                amount_cad = amount_native
                net_cad = (amount_cad - fees) if txn_type == 'Sell' else -(amount_cad + fees)

                existing = Transaction.query.filter_by(
                    date=txn_date, ticker=ticker, type=txn_type, qty=qty, price=price
                ).first()
                if existing:
                    continue

                db.session.add(Transaction(
                    date=txn_date, ticker=ticker, account=default_account,
                    type=txn_type, qty=qty, price=price, currency=currency,
                    amount_native=amount_native, amount_cad=amount_cad,
                    fees_cad=fees, net_cad=net_cad,
                ))
                count += 1

        except Exception:
            continue

    db.session.commit()
    return count


# ── PDF importer ───────────────────────────────────────────────────────────────
#
# TD monthly statements don't use PDF table structures for transactions.
# Activity data lives in plain-text "Activity in your account this period" sections.
# We parse these by extracting text per page, finding the section header, grouping
# lines into per-transaction chunks (each starts with "MMM DD"), and extracting
# numeric columns using regex patterns that distinguish monetary amounts (2 dec),
# prices (3 dec), quantities (integers) and FX rates (4-5 dec) by decimal count.

_PDF_MONTH_MAP = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}

# Monetary amounts: exactly 2 decimal places (never 3 or 5 like prices/FX rates).
_MONEY2 = r'-?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)'

# Buy/Sell row ends with: QTY(int)  PRICE(3dec)  AMOUNT(2dec)  CASHBAL(2dec)
_PDF_BUY_SELL_TAIL = re.compile(
    r'\s+(-?\d+)\s+(\d+\.\d{3})\s+(' + _MONEY2 + r')\s+(' + _MONEY2 + r')\s*$'
)
# Dividend/Withholding row ends with: QTY(int)  AMOUNT(2dec)  CASHBAL(2dec)
_PDF_DIV_WHT_TAIL = re.compile(
    r'\s+(-?\d+)\s+(' + _MONEY2 + r')\s+(' + _MONEY2 + r')\s*$'
)
# Cash row ends with: AMOUNT(2dec)  CASHBAL(2dec)
_PDF_CASH_TAIL = re.compile(
    r'\s+(' + _MONEY2 + r')\s+(' + _MONEY2 + r')\s*$'
)
# A transaction chunk starts when a line begins with "MMM DD "
_PDF_DATE_LINE = re.compile(
    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:\s+(.*))?$',
    re.IGNORECASE
)
# Account type abbreviations from full TD names
_PDF_ACCT_TYPES = {
    'registered disability savings plan': 'RDSP',
    'tax-free savings account': 'TFSA',
    'registered retirement savings plan': 'RRSP',
    'first home savings account': 'FHSA',
}


def _import_td_pdf(file_bytes, account_name=None):
    """
    Parse a TD Direct Investing monthly account statement PDF.
    Requires pdfplumber: pip install pdfplumber
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError(
            'pdfplumber is required for PDF import. Install it with: pip install pdfplumber'
        )

    all_rows = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            if not text.strip():
                continue

            # Year from "statement: Month DD, YYYY" header on each page.
            year_m = re.search(r'statement:\s+\w+\s+\d+,\s+(\d{4})', text, re.I)
            if not year_m:
                year_m = re.search(r'\b(20\d{2})\b', text)
            if not year_m:
                continue
            year = int(year_m.group(1))

            # Account name: build once from first page that has both fields.
            if not account_name:
                num_m = re.search(r'Account number:\s*(\S+)', text)
                typ_m = re.search(r'Account type:\s*(.+)', text)
                if num_m and typ_m:
                    raw_type = typ_m.group(1).strip().lower()
                    acct_type = next(
                        (abbr for full, abbr in _PDF_ACCT_TYPES.items() if full in raw_type),
                        typ_m.group(1).strip()
                    )
                    account_name = f"{acct_type} ({num_m.group(1).strip()})"

            # Find the activity section on this page.
            sec_m = re.search(
                r'Date\s+Activity\s+Description.*?\n(.*?)(?=(?:Your \w+ contribution|'
                r'Ending cash balance|Order-Execution-Only|\Z))',
                text, re.DOTALL
            )
            if not sec_m:
                continue

            # Group lines into per-transaction chunks.
            lines = [l.strip() for l in sec_m.group(1).split('\n') if l.strip()]
            chunks = []
            cur = None
            for line in lines:
                dm = _PDF_DATE_LINE.match(line)
                if dm:
                    if cur:
                        chunks.append(cur)
                    cur = {'mon': dm.group(1), 'day': int(dm.group(2)),
                           'first': dm.group(3) or '', 'extra': []}
                elif cur:
                    cur['extra'].append(line)
            if cur:
                chunks.append(cur)

            for chunk in chunks:
                first = chunk['first']
                extra = chunk['extra']
                fl = first.lower()

                # Detect activity and strip its label from the remaining text.
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
                    continue  # beginning/ending balance, unknown — skip

                # Extract trailing numeric columns from the first line.
                qty = price = amount = None
                desc_first = tail

                if action in ('Buy', 'Sell'):
                    m = _PDF_BUY_SELL_TAIL.search(tail)
                    if m:
                        qty   = abs(int(m.group(1)))
                        price = float(m.group(2))
                        amount = float(m.group(3).replace(',', ''))
                        desc_first = tail[:m.start()].strip()
                    else:
                        m = _PDF_DIV_WHT_TAIL.search(tail)
                        if m:
                            qty    = abs(int(m.group(1)))
                            amount = float(m.group(2).replace(',', ''))
                            desc_first = tail[:m.start()].strip()
                        else:
                            continue

                elif action in ('DIV', 'WHTX02'):
                    m = _PDF_DIV_WHT_TAIL.search(tail)
                    if m:
                        qty    = abs(int(m.group(1)))
                        amount = float(m.group(2).replace(',', ''))
                        desc_first = tail[:m.start()].strip()
                    else:
                        m = _PDF_CASH_TAIL.search(tail)
                        if m:
                            amount = float(m.group(1).replace(',', ''))
                            desc_first = tail[:m.start()].strip()
                        else:
                            continue

                else:  # CONT, CDSG, CDSB, FEE
                    m = _PDF_CASH_TAIL.search(tail)
                    if m:
                        amount = float(m.group(1).replace(',', ''))
                        desc_first = tail[:m.start()].strip()
                    else:
                        continue

                # Build full description: first-line portion + continuation lines.
                # For Canada Disability, the "Savings Bond/Grant" continuation is part
                # of the activity phrase (PDF column overflow), not the description.
                if action in ('CDSG', 'CDSB'):
                    desc_extra = [l for l in extra if 'savings' not in l.lower()]
                else:
                    desc_extra = extra

                description = ' '.join(filter(None, [desc_first] + desc_extra))

                month = _PDF_MONTH_MAP.get(chunk['mon'].capitalize())
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
                    'Trade Date':  txn_date.strftime('%Y-%m-%d'),
                    'Settle Date': txn_date.strftime('%Y-%m-%d'),
                    'Description': description,
                    'Action':      action,
                    'Quantity':    str(qty)   if qty   is not None else '',
                    'Price':       str(price) if price is not None else '',
                    'Commission':  commission_str,
                    'Net Amount':  str(amount),
                    'Currency':    'CAD',
                })

    if not all_rows:
        raise ValueError(
            'No transactions found in the PDF. '
            'Make sure this is a TD Direct Investing account statement.'
        )

    return _import_td(all_rows, account_name=account_name)


def _import_cibc(rows):
    """
    CIBC Investor's Edge CSV export format.
    Columns: Transaction Date, Settlement Date, Activity Type, Symbol, Description,
             Quantity, Price, Commission, Net Amount, Currency, Account Number, Account Type
    """
    count = 0
    date_fmts = ['%Y-%m-%d', '%m/%d/%Y', '%B %d, %Y']
    action_map = {
        'Buy': 'Buy', 'Sell': 'Sell',
        'Dividend': 'Dividend', 'Dividends': 'Dividend',
        'DIV': 'Dividend',
    }

    for row in rows:
        action_raw = row.get('Activity Type', '').strip()
        txn_type = action_map.get(action_raw)
        if not txn_type:
            continue

        ticker = (row.get('Symbol', '') or '').strip().upper()
        if not ticker:
            continue

        try:
            date_str = row.get('Transaction Date', '') or row.get('Settlement Date', '')
            txn_date = _parse_date(date_str, date_fmts)
            qty = abs(float((row.get('Quantity', '') or '0').replace(',', '')))
            price = abs(float((row.get('Price', '') or '0').replace(',', '')))
            currency = (row.get('Currency', 'CAD') or 'CAD').strip().upper()
            fees = abs(float((row.get('Commission', '') or '0').replace(',', '')))
            account_num = (row.get('Account Number', '') or '').strip()
            account_type = (row.get('Account Type', 'Non-Reg') or 'Non-Reg').strip()
            account_name = f'{account_type} ({account_num})' if account_num else account_type

            _ensure_account(account_name)

            amount_native = qty * price
            amount_cad = amount_native
            net_cad = (amount_cad - fees) if txn_type in ('Sell', 'Dividend') else -(amount_cad + fees)

            existing = Transaction.query.filter_by(
                date=txn_date, ticker=ticker, type=txn_type, qty=qty, price=price
            ).first()
            if existing:
                continue

            db.session.add(Transaction(
                date=txn_date, ticker=ticker, account=account_name,
                type=txn_type, qty=qty, price=price, currency=currency,
                amount_native=amount_native, amount_cad=amount_cad,
                fees_cad=fees, net_cad=net_cad,
            ))
            count += 1
        except Exception:
            continue

    db.session.commit()
    return count
