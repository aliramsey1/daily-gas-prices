import imaplib
import email
import re
import json
import os
import datetime
import io
from email.utils import parsedate_to_datetime
from pdfminer.high_level import extract_text as pdf_extract_text


GMAIL_ADDRESS = os.environ['CAMPBELL_GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['CAMPBELL_GMAIL_APP_PASSWORD']
CAMPBELL_STORE_KEY = 'pn'


def fetch_campbell_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    try:
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    except Exception as e:
        print(f'Campbell: Login failed: {e}')
        mail.logout()
        return []
    print('Campbell: Logged in successfully')

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    all_ids = set()
    seen_message_ids = set()
    results = []

    searches = [
        ('INBOX', f'SINCE {since_date} SUBJECT "Daily Price Update"'),
        ('"[Gmail]/All Mail"', f'SINCE {since_date} SUBJECT "Daily Price Update"'),
    ]

    for mailbox, search_criteria in searches:
        try:
            status, _ = mail.select(mailbox, readonly=True)
            if status != 'OK':
                continue
            _, data = mail.search(None, search_criteria)
            ids = data[0].split() if data[0] else []
            print(f'Campbell: {mailbox} {search_criteria[:60]} => {len(ids)} ids')
            all_ids.update(ids)
        except Exception as e:
            print(f'Campbell: Search error: {e}')

    print(f'Campbell: Total unique IDs: {len(all_ids)}')
    mail.select('"[Gmail]/All Mail"', readonly=True)
    for eid in list(all_ids):
        try:
            _, msg_data = mail.fetch(eid, '(RFC822)')
            msg = email.message_from_bytes(msg_data[0][1])
            frm = msg.get('From', '')
            subj = msg.get('Subject', '')
            mid = msg.get('Message-ID', str(eid))

            if mid in seen_message_ids:
                continue
            seen_message_ids.add(mid)

            if 'campbelloilco' not in frm.lower():
                continue
            if 'price update' not in subj.lower():
                continue

            try:
                email_date = parsedate_to_datetime(msg['Date']).date()
            except Exception:
                email_date = datetime.date.today()
            msg._campbell_date = email_date
            results.append(msg)
        except Exception as e:
            print(f'Campbell: Error: {e}')

    mail.logout()
    print(f'Campbell: Total Price Update emails: {len(results)}')
    return results


def parse_pdf_prices(pdf_bytes, debug=False):
    try:
        text = pdf_extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f'Campbell: PDF extract failed: {e}')
        return None

    if not text or len(text.strip()) < 20:
        if debug: print('Campbell: PDF text too short or empty')
        return None

    if debug:
        print(f'Campbell: PDF raw text (first 1000):')
        print(repr(text[:1000]))

    text_lower = text.lower()
    # Relaxed validation - just check for some Campbell indicator
    # Valid PDFs have MNP quote refs or price data
    has_campbell = ('campbelloilco' in text_lower or
                    'price quotation' in text_lower or
                    'mnp' in text_lower or
                    'regular' in text_lower)
    if not has_campbell:
        if debug: print('Campbell: PDF not a price quotation')
        return None

    prices = {}
    pdf_date = None

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    for i, line in enumerate(lines):
        if re.search(r'Regular.*?87.*?Eth|Regular.*?Eth', line, re.IGNORECASE):
            for check_line in lines[i:i+5]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['reg'] = float(nums[-1])
                    print(f'Campbell: reg={prices["reg"]}')
                    break

        if re.search(r'Premium.*?93.*?Eth|Premium.*?Eth', line, re.IGNORECASE):
            for check_line in lines[i:i+5]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['prem'] = float(nums[-1])
                    print(f'Campbell: prem={prices["prem"]}')
                    break

        if re.search(r'Diesel.*?Clr|^Diesel', line, re.IGNORECASE):
            for check_line in lines[i:i+5]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['dsl'] = float(nums[-1])
                    print(f'Campbell: dsl={prices["dsl"]}')
                    break

    # Try full-text multiline patterns if line parsing fails
    if 'reg' not in prices:
        m = re.search(r'Regular[^\n]*?Eth[^\n]*?(\d+\.\d{4,6})', text, re.IGNORECASE | re.DOTALL)
        if m: prices['reg'] = float(m.group(1)); print(f'Campbell: reg={prices["reg"]} (dotall)')
    if 'prem' not in prices:
        m = re.search(r'Premium[^\n]*?Eth[^\n]*?(\d+\.\d{4,6})', text, re.IGNORECASE | re.DOTALL)
        if m: prices['prem'] = float(m.group(1)); print(f'Campbell: prem={prices["prem"]} (dotall)')
    if 'dsl' not in prices:
        m = re.search(r'Diesel[^\n]*?Clr[^\n]*?(\d+\.\d{4,6})', text, re.IGNORECASE | re.DOTALL)
        if m: prices['dsl'] = float(m.group(1)); print(f'Campbell: dsl={prices["dsl"]} (dotall)')

    m = re.search(r'Start\s*Date[:\s]*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', text, re.IGNORECASE)
    if m:
        try:
            raw = m.group(1).strip().replace(',', '')
            pdf_date = datetime.datetime.strptime(raw, '%b %d %Y').strftime('%Y-%m-%d')
        except Exception:
            pass

    print(f'Campbell: prices={prices}, date={pdf_date}')
    return prices, pdf_date


def get_pdf_from_msg(msg):
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        if ct == 'application/pdf' or fn.lower().endswith('.pdf'):
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print('Campbell: No price update emails found')
        return

    all_data = {}
    debug_done = False
    for msg in emails:
        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            continue

        result = parse_pdf_prices(pdf_bytes, debug=(not debug_done))
        debug_done = True  # Only debug first PDF
        if not result:
            continue
        prices, pdf_date = result
        if not prices:
            continue

        date_str = pdf_date if pdf_date else getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
        if date_str not in all_data:
            all_data[date_str] = {}
        all_data[date_str].update(prices)
        print(f'Campbell: Stored {date_str}: {prices}')

    if not all_data:
        print('Campbell: No price data extracted')
        return

    prices_file = 'prices.json'
    if os.path.exists(prices_file):
        with open(prices_file, 'r') as f:
            existing = json.load(f)
    else:
        existing = {}

    for date_str, prices in all_data.items():
        if date_str not in existing:
            existing[date_str] = {}
        existing[date_str][CAMPBELL_STORE_KEY] = prices
        print(f'Campbell: Wrote {date_str}')

    with open(prices_file, 'w') as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    print(f'Campbell: Updated {len(all_data)} dates')


if __name__ == '__main__':
    print('Starting Campbell Oil fetch...')
    process_campbell_emails()
