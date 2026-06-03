"""
CSV importers for TD Direct Investing and CIBC Investor's Edge exports.
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

    # Case-insensitive action lookup
    action_map = {
        'buy': 'Buy',
        'sell': 'Sell',
        'div': 'Dividend',
        'dividend': 'Dividend',
    }
    # Cash/grant actions — recorded as Deposit with ticker CASH
    cash_subtypes = {
        'cont': 'Contribution',
        'cdsg': 'RDSP Grant',
        'cdsb': 'RDSP Bond',
    }
    cash_actions = set(cash_subtypes)
    # Admin-only actions with no financial value to track
    skip_actions = {'whtx02', 'split', 'cxlspl', 'tfr', 'tfri', 'tfro'}

    default_account = account_name or 'TD Direct Investing'
    _ensure_account(default_account)

    for row in rows:
        action_raw = row.get('Action', '').strip()
        action_key = action_raw.lower()

        if action_key in skip_actions:
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

        # Skip cancellation reversal rows — they zero out a prior entry
        if re.search(r'CANCELLATION OF', desc, re.IGNORECASE):
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

            qty_raw = (row.get('Quantity', '') or '0').replace(',', '').strip()
            qty = abs(float(qty_raw)) if qty_raw else 0.0

            price_raw = (row.get('Price', '') or '0').replace(',', '').strip()
            price = abs(float(price_raw)) if price_raw else 0.0

            currency = (row.get('Currency', 'CAD') or 'CAD').strip().upper() or 'CAD'

            comm_raw = (row.get('Commission', '') or '0').replace(',', '').strip()
            fees = abs(float(comm_raw)) if comm_raw else 0.0

            amount_native = qty * price
            amount_cad = amount_native
            net_cad = (amount_cad - fees) if txn_type in ('Sell', 'Dividend') else -(amount_cad + fees)

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
