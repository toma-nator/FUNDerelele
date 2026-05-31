"""
CSV importers for TD Bank and CIBC brokerage exports.
Each parser normalises rows into Transaction objects.
"""
import io
import csv
from datetime import datetime
from models import db, Transaction, Account


def parse_file_path(filepath, broker='auto'):
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        content = f.read()
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
        raise ValueError(f'Unknown broker format: {broker}')


def parse_upload(file_storage, broker='auto'):
    content = file_storage.read().decode('utf-8-sig', errors='replace')
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
    if 'settlement date' in keys and 'action' in keys:
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


def _import_td(rows):
    """
    TD Direct Investing CSV export format:
    Settlement Date, Action, Symbol, Description, Quantity, Price, Gross Amount,
    Commission, Net Amount, Currency, Account #, Account Type
    """
    count = 0
    date_fmts = ['%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y']
    action_map = {'Buy': 'Buy', 'Sell': 'Sell', 'DIV': 'Dividend', 'Dividend': 'Dividend'}

    for row in rows:
        action_raw = row.get('Action', '').strip()
        txn_type = action_map.get(action_raw)
        if not txn_type:
            continue

        ticker = (row.get('Symbol', '') or '').strip().upper()
        if not ticker:
            continue

        try:
            date_str = row.get('Settlement Date', '') or row.get('Trade Date', '')
            txn_date = _parse_date(date_str, date_fmts)
            qty = abs(float((row.get('Quantity', '') or '0').replace(',', '')))
            price = abs(float((row.get('Price', '') or '0').replace(',', '')))
            currency = (row.get('Currency', 'CAD') or 'CAD').strip().upper()
            fees = abs(float((row.get('Commission', '') or '0').replace(',', '')))
            account_num = (row.get('Account #', '') or '').strip()
            account_type = (row.get('Account Type', 'Non-Reg') or 'Non-Reg').strip()
            account_name = f'{account_type} ({account_num})' if account_num else account_type

            _ensure_account(account_name)

            amount_native = qty * price
            amount_cad = amount_native  # TD usually exports in native currency; FX applied on display
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


def _import_cibc(rows):
    """
    CIBC Investor's Edge CSV export format:
    Transaction Date, Settlement Date, Activity Type, Symbol, Description,
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
