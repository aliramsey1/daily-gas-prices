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

def parse_body_prices(msg):
    body_text = ''
    for part in msg.walk():
        ct = part.get_content_type()
        if ct in ('text/plain', 'text/html'):
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    decoded = payload.decode(charset, errors='replace')
                    if ct == 'text/html':
                        decoded = re.sub(r'<[^>]+>', ' ', decoded)
                        decoded = re.sub(r'&nbsp;', ' ', decoded)
                        decoded = re.sub(r'&amp;', '&', decoded)
                    body_text += decoded + '\n'
            except Exception:
                pass
    if not body_text:
        return None, None
    prices = {}
    email_date = None
    m = re.search(r'effective from\s+(\d{2}-\d{2}-\d{4})', body_text, re.IGNORECASE)
    if m:
        try:
            email_date = datetime.datetime.strptime(m.group(1), '%m-%d-%Y').strftime('%Y-%m-%d')
            print(f'Campbell: Body date={email_date}')
        except Exception:
            pass
    reg_match = re.search(r'Regular\s+87\s+Eth\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', body_text, re.IGNORECASE)
    if reg_match:
        prices['reg'] = float(reg_match.group(3))
        print(f'Campbell: Body reg={prices["reg"]}')
    prem_match = re.search(r'Premium\s+93\s+Eth\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', body_text, re.IGNORECASE)
    if prem_match:
        prices['prem'] = float(prem_match.group(3))
        print(f'Campbell: Body prem={prices["prem"]}')
    dsl_match = re.search(r'Diesel\s+Clr\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', body_text, re.IGNORECASE)
    if dsl_match:
        prices['die'] = float(dsl_match.group(3))
        print(f'Campbell: Body dsl={prices["dsl"]}')
    if prices:
        print(f'Campbell: Body prices={prices}')
        return prices, email_date
    return None, None

def parse_pdf_prices(pdf_bytes, debug=False):
    try:
        text = pdf_extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f'Campbell: PDF extract failed: {e}')
        return None
    if not text or len(text.strip()) < 20:
        if debug:
            print('Campbell: PDF text too short or empty')
        return None
    text_lower = text.lower()
    has_campbell = ('campbelloilco' in text_lower or 'price quotation' in text_lower or
                    'mnp' in text_lower or 'regular' in text_lower)
    if not has_campbell:
        if debug:
            print('Campbell: PDF not a price quotation')
        return None
    prices = {}
    pdf_date = None
    text_lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(text_lines):
        if re.search(r'Regular.*?87.*?Eth|Regular.*?Eth', line, re.IGNORECASE):
            for check_line in text_lines[i:i+5]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['reg'] = float(nums[-1])
                    print(f'Campbell: PDF reg={prices["reg"]}')
                    break
        if re.search(r'Premium.*?93.*?Eth|Premium.*?Eth', line, re.IGNORECASE):
            for check_line in text_lines[i:i+5]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['prem'] = float(nums[-1])
                    print(f'Campbell: PDF prem={prices["prem"]}')
                    break
        if re.search(r'Diesel.*?Clr|^Diesel', line, re.IGNORECASE):
            for check_line in text_lines[i:i+5]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['die'] = float(nums[-1])
                    print(f'Campbell: PDF dsl={prices["dsl"]}')
                    break
    print(f'Campbell: PDF prices={prices}, date={pdf_date}')
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
        prices, email_date = parse_body_prices(msg)
        if not prices:
            pdf_bytes = get_pdf_from_msg(msg)
            if pdf_bytes:
                result = parse_pdf_prices(pdf_bytes, debug=(not debug_done))
                debug_done = True
                if result:
                    prices, pdf_date = result
                    if not email_date and pdf_date:
                        email_date = pdf_date
        if not prices:
            print(f'Campbell: No prices extracted from email dated {getattr(msg, "_campbell_date", "unknown")}')
            continue
        date_str = email_date if email_date else getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
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
