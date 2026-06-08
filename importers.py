"""
CSV and PDF importers for TD Direct Investing and CIBC Investor's Edge exports.
Each parser normalises rows into Transaction objects.
"""
import io
import os
import re
import csv
import shutil
from datetime import datetime
from sqlalchemy import event
from models import db, Transaction, Account


def scan_import_folder(folder):
    """Import every CSV/PDF in `folder`, then move each into a processed/
    subfolder so it isn't re-imported. Returns a summary dict."""
    summary = {'files': 0, 'imported': 0, 'skipped': 0, 'errors': []}
    if not folder or not os.path.isdir(folder):
        return summary
    processed_dir = os.path.join(folder, 'processed')
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or not name.lower().endswith(('.csv', '.pdf', '.txt')):
            continue
        try:
            res = parse_file_path(path)
            summary['files'] += 1
            summary['imported'] += res.get('imported', 0)
            summary['skipped'] += res.get('skipped', 0)
            os.makedirs(processed_dir, exist_ok=True)
            dest = os.path.join(processed_dir, name)
            if os.path.exists(dest):
                base, ext = os.path.splitext(name)
                dest = os.path.join(processed_dir, f'{base}_{datetime.now().strftime("%H%M%S")}{ext}')
            shutil.move(path, dest)
        except Exception as e:
            summary['errors'].append(f'{name}: {e}')
    return summary


def _run_import(parse_fn):
    """Run an import, stamping every new transaction with one batch id and
    returning a summary: {batch, imported, skipped, accounts, date_min, date_max}."""
    batch = datetime.now().strftime('%Y%m%d%H%M%S%f')

    def _stamp(mapper, connection, target):
        if getattr(target, 'import_batch', None) is None:
            target.import_batch = batch

    event.listen(Transaction, 'before_insert', _stamp)
    try:
        res = parse_fn() or {}
    finally:
        event.remove(Transaction, 'before_insert', _stamp)

    txns = Transaction.query.filter_by(import_batch=batch).all()
    dates = [t.date for t in txns if t.date]
    return {
        'batch': batch,
        'imported': res.get('imported', len(txns)),
        'skipped': res.get('skipped', 0),
        'accounts': sorted({t.account for t in txns}),
        'date_min': min(dates) if dates else None,
        'date_max': max(dates) if dates else None,
    }


def parse_file_path(filepath, broker='auto', account_override=None):
    if filepath.lower().endswith('.pdf'):
        with open(filepath, 'rb') as f:
            data = f.read()
        return _run_import(lambda: _import_td_pdf(data, account_override=account_override))
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        content = f.read()
    return _run_import(lambda: _parse_content(content, broker, account_override=account_override))


def parse_upload(file_storage, broker='auto', account_override=None):
    filename = (file_storage.filename or '').lower()
    if filename.endswith('.pdf'):
        data = file_storage.read()
        return _run_import(lambda: _import_td_pdf(data, account_override=account_override))
    content = file_storage.read().decode('utf-8-sig', errors='replace')
    return _run_import(lambda: _parse_content(content, broker, account_override=account_override))


def _parse_content(content, broker='auto', account_override=None):
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
        return _import_td(rows, account_name=td_account_name, account_override=account_override)

    # CIBC Investor's Edge Transaction History files have a metadata block (account
    # number+type, holder, export date, From/To range) before the real header row.
    # Detect by scanning for the "Transaction Date" header; the account label is the
    # first non-empty line, e.g. "62729276 FHSA".
    cibc_header_idx = None
    cibc_account = None
    for i, line in enumerate(lines[:20]):
        if re.match(r'^"?Transaction Date"?,', line):
            cibc_header_idx = i
            break
    if cibc_header_idx is not None:
        for line in lines[:cibc_header_idx]:
            first = line.split(',')[0].strip().strip('"')
            if re.match(r'^\d{4,}\s+\S', first):
                cibc_account = first
                break
        real_content = '\n'.join(lines[cibc_header_idx:])
        rows = list(csv.DictReader(io.StringIO(real_content)))
        if not rows:
            raise ValueError('CIBC file has a header but no data rows.')
        return _import_cibc(rows, account_name=cibc_account, account_override=account_override)

    # Standard path for the app's own export and any future brokers
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        raise ValueError('File is empty or not a valid CSV.')

    if broker == 'auto':
        broker = _detect_broker(rows[0])

    if broker == 'td':
        return _import_td(rows, account_override=account_override)
    elif broker == 'cibc':
        return _import_cibc(rows, account_override=account_override)
    elif broker == 'native':
        return _import_native(rows, account_override=account_override)
    else:
        raise ValueError(f'Unknown broker format: {broker}. Please select TD or CIBC manually.')


def _detect_broker(first_row):
    keys = {k.lower().strip() for k in first_row.keys()}
    # The app's own transaction export (lossless re-import).
    if {'date', 'type', 'ticker', 'net_cad'} <= keys:
        return 'native'
    if 'settle date' in keys or 'settlement date' in keys:
        if 'action' in keys:
            return 'td'
    # CIBC Investor's Edge — real export uses "Transaction Type"; the older
    # column map used "Activity Type". Accept either.
    if 'transaction date' in keys and ('transaction type' in keys or 'activity type' in keys):
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


def _import_td(rows, account_name=None, account_override=None):
    """
    TD Direct Investing CSV export.
    Columns: Trade Date, Settle Date, Description, Action, Quantity, Price,
             Commission, Net Amount, Security Type, Currency
    The account name is parsed from the metadata header, not a column.
    """
    count = 0
    skipped = 0
    date_fmts = ['%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y', '%d-%b-%y']

    action_map = {
        'buy': 'Buy',
        'sell': 'Sell',
        'div': 'Dividend',
        'dividend': 'Dividend',
        'split': 'Split',    # SPLIT adds new shares; CXLSPL subtracts (temp-ticker reversal)
        'cxlspl': 'Split',
        'whtx02': 'WithholdingTax',
        # Corporate removals: only negative-qty rows (share removal side) become Sell
        'exch': 'CorpRemoval',
        'disp': 'CorpRemoval',
    }
    cash_subtypes = {
        'cont': 'Contribution',
        'cdsg': 'RDSP Grant',
        'cdsb': 'RDSP Bond',
    }
    fee_actions = {'fee', 'gstcharged', 'adminfee'}
    cash_actions = set(cash_subtypes)
    # Account transfers — pure in-kind moves with no cash impact, skip entirely.
    # (ROC/CXLROC and CXLDIV/CXLWHTX02 are handled below as real cash events.)
    skip_actions = {'tfr', 'tfri', 'tfro'}

    default_account = account_override or account_name or 'TD Direct Investing'
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
                    skipped += 1
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
                    skipped += 1
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

        # Return of capital — real cash credited to the account (ROC) and its
        # reversal (CXLROC). Net Amount is already signed (ROC +, CXLROC −).
        # Booked as cash-only; the underlying position, if disposed, no longer
        # appears in holdings. (ACB reduction for still-held positions is not modelled.)
        if action_key in ('roc', 'cxlroc'):
            try:
                date_str = (row.get('Trade Date', '') or row.get('Settle Date', '') or '').strip()
                txn_date = _parse_date(date_str, date_fmts)
                net_raw = (row.get('Net Amount', '') or '0').replace(',', '').strip()
                amount = float(net_raw) if net_raw else 0.0  # keep sign
                if amount == 0:
                    continue
                desc = (row.get('Description', '') or '').strip()
                existing = Transaction.query.filter_by(
                    date=txn_date, type='ReturnOfCapital', net_cad=amount
                ).first()
                if existing:
                    skipped += 1
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker='CASH', account=default_account,
                    type='ReturnOfCapital', qty=0, price=0, currency='CAD',
                    amount_native=abs(amount), amount_cad=amount,
                    fees_cad=0, net_cad=amount, notes=desc,
                ))
                count += 1
            except Exception:
                pass
            continue

        # Dividend / withholding-tax cancellations — reverse a previously booked
        # amount so cancelled (or cancelled-and-reissued) distributions net out in
        # both cash and dividend income.
        #   CXLDIV    → negative Dividend  (income down, cash out)
        #   CXLWHTX02 → withholding refund (cash back in)
        if action_key in ('cxldiv', 'cxlwhtx02'):
            try:
                date_str = (row.get('Trade Date', '') or row.get('Settle Date', '') or '').strip()
                txn_date = _parse_date(date_str, date_fmts)
                net_raw = (row.get('Net Amount', '') or '0').replace(',', '').strip()
                amount = abs(float(net_raw)) if net_raw else 0.0
                if amount == 0:
                    continue
                desc = (row.get('Description', '') or '').strip()
                ticker = (row.get('Symbol', '') or '').strip().upper() or _td_ticker_from_desc(desc)
                if not ticker or ticker == 'UNKNOWN':
                    continue
                currency = (row.get('Currency', 'CAD') or 'CAD').strip().upper() or 'CAD'
                qty_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
                qty = abs(float(qty_raw)) if qty_raw else 0.0
                if action_key == 'cxldiv':
                    rtype, amt_cad, ncad = 'Dividend', -amount, -amount
                else:  # cxlwhtx02 — reverses a withholding tax, cash returns
                    rtype, amt_cad, ncad = 'WithholdingTax', -amount, amount
                existing = Transaction.query.filter_by(
                    date=txn_date, ticker=ticker, type=rtype, qty=qty, price=0, net_cad=ncad
                ).first()
                if existing:
                    skipped += 1
                    continue
                db.session.add(Transaction(
                    date=txn_date, ticker=ticker, account=default_account,
                    type=rtype, qty=qty, price=0, currency=currency,
                    amount_native=amount, amount_cad=amt_cad, fees_cad=0, net_cad=ncad,
                ))
                count += 1
            except Exception:
                pass
            continue

        txn_type = action_map.get(action_key)
        if not txn_type:
            continue

        desc = row.get('Description', '') or ''

        # CorpRemoval (EXCH/DISP): only the negative-qty row removes shares from the position.
        # The positive-qty counterpart is a temp/receipt entry — skip it.
        # EXCH is an in-kind exchange (paired rows net to zero → no cash). DISP is a
        # disposition that pays cash-in-lieu, so its Net Amount must be credited.
        is_corp_removal = False
        is_inkind = False
        if txn_type == 'CorpRemoval':
            qty_sign_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
            try:
                qty_signed = float(qty_sign_raw)
            except Exception:
                continue
            if qty_signed >= 0:
                continue
            is_inkind = (action_key == 'exch')
            txn_type = 'Sell'
            is_corp_removal = True

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
                    skipped += 1
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
                    date=txn_date, ticker=ticker, type='Dividend', qty=qty, price=0, net_cad=amount
                ).first()
                if existing:
                    skipped += 1
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
                    date=txn_date, ticker=ticker, type='WithholdingTax', qty=qty, price=0, net_cad=-amount
                ).first()
                if existing:
                    skipped += 1
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
                net_raw = (row.get('Net Amount', '') or '').replace(',', '').strip()

                amount_native = qty * price
                # Net Amount already holds the actual CAD value at purchase-day FX.
                # Use it for both Buy cost and Sell proceeds so cash and book cost
                # are correct regardless of whether the ticker is CAD- or USD-priced.
                # In-kind removals (EXCH) carry no cash; DISP keeps its cash-in-lieu.
                if net_raw and not is_inkind:
                    try:
                        net_amount_cad = abs(float(net_raw))
                    except Exception:
                        net_amount_cad = None
                else:
                    net_amount_cad = None

                if txn_type == 'Buy':
                    amount_cad = (net_amount_cad - fees) if net_amount_cad else amount_native
                    net_cad = -(amount_cad + fees)
                else:  # Sell
                    amount_cad = amount_native
                    net_cad = net_amount_cad if net_amount_cad else (amount_cad - fees)

                existing = Transaction.query.filter_by(
                    date=txn_date, ticker=ticker, type=txn_type, qty=qty, price=price
                ).first()
                if existing:
                    skipped += 1
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
    return {'imported': count, 'skipped': skipped}


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


def _import_td_pdf(file_bytes, account_name=None, account_override=None):
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

    return _import_td(all_rows, account_name=account_name, account_override=account_override)


# CIBC account-type tokens (from the "<number> <TYPE>" header line) → app types.
_CIBC_ACCT_TYPES = {
    'tfsa': 'TFSA', 'fhsa': 'FHSA', 'rdsp': 'RDSP', 'resp': 'RESP',
    'rrsp': 'RRSP', 'rsp': 'RRSP', 'rrif': 'RRIF', 'rif': 'RRIF',
    'lira': 'LIRA', 'lif': 'LIF',
}


def _cibc_account(account_name, account_override):
    """Turn the CIBC header label (e.g. "62729276 FHSA") into a clean account name
    and registration type. Returns (name, type)."""
    if account_override:
        existing = Account.query.filter_by(name=account_override).first()
        return account_override, (existing.type if existing else None)
    if not account_name:
        return 'CIBC Investor\'s Edge', 'Non-Reg'
    m = re.match(r'^(\d+)\s+(.+)$', account_name.strip())
    if not m:
        return account_name.strip(), 'Non-Reg'
    number, raw_type = m.group(1), m.group(2).strip()
    acct_type = _CIBC_ACCT_TYPES.get(raw_type.lower(), 'Non-Reg')
    return f'{acct_type} ({number})', acct_type


def _cibc_ticker(symbol, description, market):
    """Resolve a CIBC symbol to a yfinance ticker. Canadian listings need a suffix:
    CDRs trade on Cboe Canada (.NE), other CDN-listed names on the TSX (.TO). A
    user TickerMap on the bare symbol overrides the heuristic. US names stay plain."""
    from models import TickerMap
    sym = (symbol or '').strip().upper()
    if not sym:
        return ''
    # A user mapping on the bare symbol wins (set it before importing).
    mapping = TickerMap.query.get(sym)
    if mapping:
        return mapping.ticker
    if '.' in sym:  # already suffixed
        return sym
    desc = (description or '').upper()
    mkt = (market or '').upper()
    if 'CDR' in desc:
        guess = sym + '.NE'
    elif mkt in ('CDN', 'CAD', 'TSX', 'TSXV', 'NEO', 'CSE'):
        guess = sym + '.TO'
    else:
        guess = sym
    # Also honour a mapping on the suffixed guess, so a correction made after an
    # import (which remaps e.g. "T.TO" → the right symbol) survives a re-import.
    remap = TickerMap.query.get(guess)
    return remap.ticker if remap else guess


def _import_cibc(rows, account_name=None, account_override=None):
    """
    CIBC Investor's Edge "Transaction History" CSV export.
    Real columns: Transaction Date, Settlement Date, Currency of Sub-account Held In,
        Transaction Type, Symbol, Market, Description, Quantity, Currency of Price,
        Price, Commission, Exchange Rate, Currency of Amount, Amount,
        Settlement Instruction, Exchange Rate (Canadian Equivalent), Canadian Equivalent
    The account number/type comes from the file's metadata header, not a column.
    Older column names (Activity Type / Net Amount / Currency) are still accepted.
    Amount is signed native cash (negative for buys); Canadian Equivalent holds the
    CAD value for USD trades.
    """
    count = 0
    skipped = 0
    date_fmts = ['%Y-%m-%d', '%m/%d/%Y', '%B %d, %Y', '%b %d, %Y', '%d-%b-%y', '%d-%b-%Y']
    action_map = {
        'buy': 'Buy', 'sell': 'Sell',
        'dividend': 'Dividend', 'dividends': 'Dividend', 'div': 'Dividend',
        'interest': 'Interest',
        'contrib': 'Deposit', 'contribution': 'Deposit', 'deposit': 'Deposit',
        'withholding tax': 'WithholdingTax', 'withholding': 'WithholdingTax',
        'nr tax': 'WithholdingTax',
    }

    acct_name, acct_type = _cibc_account(account_name, account_override)
    _ensure_account(acct_name)
    if acct_type:  # stamp the registration type so the Tax tab treats it correctly
        acct = Account.query.filter_by(name=acct_name).first()
        if acct and (acct.type or 'Non-Reg') in ('Non-Reg', '') and acct_type != 'Non-Reg':
            acct.type = acct_type
            db.session.commit()

    def num(v):
        try:
            return float((v or '0').replace(',', '').replace('$', '').strip())
        except (TypeError, ValueError):
            return 0.0

    for row in rows:
        action_raw = (row.get('Transaction Type') or row.get('Activity Type') or '').strip()
        txn_type = action_map.get(action_raw.lower())
        if not txn_type:
            continue

        try:
            date_str = (row.get('Transaction Date', '') or row.get('Settlement Date', '')).strip()
            txn_date = _parse_date(date_str, date_fmts)

            qty = abs(num(row.get('Quantity')))
            price = abs(num(row.get('Price')))
            fees_native = abs(num(row.get('Commission')))
            currency = (row.get('Currency of Amount') or row.get('Currency') or 'CAD').strip().upper() or 'CAD'

            # Amount is signed native cash; Canadian Equivalent is the CAD value (USD trades).
            amount_signed = num(row.get('Amount') if row.get('Amount') not in (None, '') else row.get('Net Amount'))
            ce = num(row.get('Canadian Equivalent'))
            if currency != 'CAD' and ce:
                net_cad = ce
                rate = abs(ce) / abs(amount_signed) if amount_signed else 1.0
            else:
                net_cad = amount_signed
                rate = 1.0
            fees_cad = round(fees_native * rate, 2)
            amount_native = qty * price

            if txn_type in ('Buy', 'Sell'):
                ticker = _cibc_ticker(row.get('Symbol'), row.get('Description'), row.get('Market'))
                if not ticker:
                    continue
                if net_cad == 0:  # fall back to qty*price if Amount was blank
                    net_cad = -(amount_native + fees_native) * rate if txn_type == 'Buy' \
                        else (amount_native - fees_native) * rate
                if txn_type == 'Buy':
                    net_cad = -abs(net_cad)
                    amount_cad = abs(net_cad) - fees_cad
                else:
                    net_cad = abs(net_cad)
                    amount_cad = abs(net_cad) + fees_cad

            elif txn_type == 'Dividend':
                ticker = _cibc_ticker(row.get('Symbol'), row.get('Description'), row.get('Market'))
                if not ticker:
                    continue
                amount_cad = abs(net_cad) or abs(amount_signed)
                if amount_cad == 0:
                    continue
                net_cad = amount_cad
                qty = price = 0.0

            elif txn_type == 'WithholdingTax':
                ticker = _cibc_ticker(row.get('Symbol'), row.get('Description'), row.get('Market'))
                if not ticker:
                    continue
                amount_cad = abs(net_cad)
                if amount_cad == 0:
                    continue
                net_cad = -amount_cad
                qty = price = 0.0

            elif txn_type == 'Interest':
                # Cash/money-market interest income. Tie to CASH unless a security
                # symbol is given (e.g. bond interest), so no phantom holding appears.
                sym = (row.get('Symbol') or '').strip()
                ticker = _cibc_ticker(sym, row.get('Description'), row.get('Market')) if sym else 'CASH'
                amount_cad = abs(net_cad)
                if amount_cad == 0:
                    continue
                net_cad = amount_cad
                qty = price = 0.0

            else:  # Deposit (Contribution)
                ticker = 'CASH'
                amount_cad = abs(net_cad)
                if amount_cad == 0:
                    continue
                net_cad = amount_cad
                qty = price = 0.0

            subtype = 'Contribution' if txn_type == 'Deposit' else ''

            existing = Transaction.query.filter_by(
                date=txn_date, account=acct_name, ticker=ticker,
                type=txn_type, net_cad=round(net_cad, 2)
            ).first()
            if existing:
                skipped += 1
                continue

            db.session.add(Transaction(
                date=txn_date, ticker=ticker, account=acct_name,
                type=txn_type, qty=qty, price=price, currency=currency,
                amount_native=amount_native, amount_cad=round(amount_cad, 2),
                fees_cad=fees_cad, net_cad=round(net_cad, 2), subtype=subtype,
            ))
            count += 1
        except Exception:
            continue

    db.session.commit()
    return {'imported': count, 'skipped': skipped}


def _import_native(rows, account_override=None):
    """Re-import the app's own transaction CSV export — a lossless round-trip.
    Unlike the broker parsers this writes every field verbatim (subtype, the
    historical CAD amounts, fees) and does NOT re-derive anything via live FX.
    Columns: date, type, subtype, ticker, account, qty, price, currency,
             amount_native, amount_cad, fees_cad, net_cad, notes."""
    count = skipped = 0
    date_fmts = ['%Y-%m-%d', '%m/%d/%Y']

    def num(v):
        try:
            return float((v or '0').replace(',', '').strip())
        except (TypeError, ValueError):
            return 0.0

    for row in rows:
        try:
            ttype = (row.get('type', '') or '').strip()
            if not ttype:
                continue
            txn_date = _parse_date(row.get('date', ''), date_fmts)
            ticker = (row.get('ticker', '') or '').strip()
            account = account_override or (row.get('account', '') or '').strip()
            if not account:
                continue
            _ensure_account(account)
            # Restore the account's registration type (TFSA/RDSP/…) — it lives on
            # the account, not the transaction, so carry it for a faithful restore.
            atype = (row.get('account_type', '') or '').strip()
            if atype:
                acct = Account.query.filter_by(name=account).first()
                if acct and (acct.type or '') != atype:
                    acct.type = atype

            net_cad = num(row.get('net_cad'))
            qty = num(row.get('qty'))
            price = num(row.get('price'))
            # Amount- and quantity-aware dedup so re-importing into the same DB is
            # idempotent. qty/price are part of the key so cash-neutral pairs that
            # only differ in shares — e.g. a Split (+120) and its reversal (−120),
            # both net_cad=0 — aren't collapsed into one (which would corrupt the
            # share count on a full CSV restore).
            existing = Transaction.query.filter_by(
                date=txn_date, account=account, ticker=ticker, type=ttype,
                net_cad=net_cad, qty=qty, price=price
            ).first()
            if existing:
                skipped += 1
                continue

            db.session.add(Transaction(
                date=txn_date, ticker=ticker, account=account, type=ttype,
                subtype=(row.get('subtype', '') or '').strip(),
                qty=qty, price=price,
                currency=(row.get('currency', 'CAD') or 'CAD').strip().upper(),
                amount_native=num(row.get('amount_native')),
                amount_cad=num(row.get('amount_cad')),
                fees_cad=num(row.get('fees_cad')),
                net_cad=net_cad,
                notes=(row.get('notes', '') or '').strip(),
            ))
            count += 1
        except Exception:
            continue

    db.session.commit()
    return {'imported': count, 'skipped': skipped}
