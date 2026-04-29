import imaplib
import email
import re
import json
import os
import datetime
import io
from email.utils import parsedate_to_datetime
from pdfminer.high_level import extract_text as pdf_extract_text


# Use Campbell-specific credentials (popngo786@gmail.com)
GMAIL_ADDRESS = os.environ['CAMPBELL_GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['CAMPBELL_GMAIL_APP_PASSWORD']

# Campbell Oil - Pop N Go store
CAMPBELL_STORE_KEY = 'pn'


def fetch_campbell_emails():
    """Fetch Campbell Oil price emails from popngo786@gmail.com."""
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    try:
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    except Exception as e:
        print(f'Campbell: Login failed: {e}')
        print('Campbell: Check CAMPBELL_GMAIL_ADDRESS and CAMPBELL_GMAIL_APP_PASSWORD secrets')
        mail.logout()
        return []
    print('Campbell: Logged in successfully')

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    all_ids = set()
    seen_message_ids = set()
    results = []

    # Search only for Daily Price Update emails - avoids processing hundreds of invoices
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
            print(f'Campbell: Search error for {mailbox}: {e}')

    print(f'Campbell: Total unique IDs found: {len(all_ids)}')

    # Fetch all matched emails
    mail.select('"[Gmail]/All Mail"', readonly=True)
    for eid in list(all_ids):
        try:
            _, msg_data = mail.fetch(eid, '(RFC822)')
            msg = email.message_from_bytes(msg_data[0][1])
            frm = msg.get('From', '')
            subj = msg.get('Subject', '')
            mid = msg.get('Message-ID', str(eid))
            print(f'Campbell: id={eid} From={frm[:80]} Subj={subj[:80]}')

            if mid in seen_message_ids:
                continue
            seen_message_ids.add(mid)

            # Only process emails from campbelloilco.com
            if 'campbelloilco' not in frm.lower():
                print('  Skipping non-Campbell sender')
                continue

            # Only process Daily Price Update emails
            if 'price update' not in subj.lower():
                print('  Skipping non-price-update email')
                continue

            try:
                email_date = parsedate_to_datetime(msg['Date']).date()
            except Exception:
                email_date = datetime.date.today()
            msg._campbell_date = email_date
            results.append(msg)
        except Exception as e:
            print(f'Campbell: Error fetching {eid}: {e}')

    mail.logout()
    print(f'Campbell: Total Price Update emails found: {len(results)}')
    return results


def parse_pdf_prices(pdf_bytes):
    """Parse Campbell Oil PDF price quotation using pdfminer."""
    try:
        text = pdf_extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f'Campbell: PDF extract failed: {e}')
        return None

    if not text or len(text.strip()) < 50:
        return None

    # Validate this is a Campbell Oil price quotation
    text_lower = text.lower()
    if 'campbelloilco' not in text_lower and 'price quotation' not in text_lower:
        return None

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    prices = {}

    for line in lines:
        if re.search(r'Regular\s+87', line, re.IGNORECASE) or (re.search(r'Regular', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['reg'] = float(nums[-1])
                print(f'Campbell: reg={prices["reg"]}')

        if re.search(r'Premium\s+93', line, re.IGNORECASE) or (re.search(r'Premium', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['prem'] = float(nums[-1])
                print(f'Campbell: prem={prices["prem"]}')

        if re.search(r'Diesel\s+Clr', line, re.IGNORECASE) or re.search(r'^Diesel', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['dsl'] = float(nums[-1])
                print(f'Campbell: dsl={prices["dsl"]}')

    pdf_date = None
    m = re.search(r'Start\s*Date\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', text, re.IGNORECASE)
    if m:
        try:
            raw = m.group(1).strip().replace(',', '')
            pdf_date = datetime.datetime.strptime(raw, '%b %d %Y').strftime('%Y-%m-%d')
        except Exception:
            pass

    print(f'Campbell: Parsed prices={prices}, pdf_date={pdf_date}')
    return prices, pdf_date


def get_pdf_from_msg(msg):
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        if ct == 'application/pdf' or fn.lower().endswith('.pdf'):
            print(f'Campbell: Found PDF: {fn[:60]}')
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print('Campbell: No Campbell Oil price update emails found, skipping')
        return

    all_data = {}
    for msg in emails:
        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            date_str = getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
            print(f'Campbell: No PDF in email {date_str}')
            continue

        result = parse_pdf_prices(pdf_bytes)
        if not result:
            continue
        prices, pdf_date = result
        if not prices:
            continue

        date_str = pdf_date if pdf_date else getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
        if date_str not in all_data:
            all_data[date_str] = {}
        all_data[date_str].update(prices)
        print(f'Campbell: Stored prices for {date_str}: {prices}')

    if not all_data:
        print('Campbell: No valid Campbell Oil price data extracted')
        return

    # Read existing prices.json
    prices_file = 'prices.json'
    if os.path.exists(prices_file):
        with open(prices_file, 'r') as f:
            existing = json.load(f)
    else:
        existing = {}

    # Merge Campbell data under 'pn' key
    for date_str, prices in all_data.items():
        if date_str not in existing:
            existing[date_str] = {}
        existing[date_str][CAMPBELL_STORE_KEY] = prices
        print(f'Campbell: Updated prices.json for {date_str} pn={prices}')

    with open(prices_file, 'w') as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    print(f'Campbell: prices.json updated with {len(all_data)} date(s)')


if __name__ == '__main__':
    print('Starting Campbell Oil price fetch...')
    process_campbell_emails()
