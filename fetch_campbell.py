import imaplib
import email
import re
import json
import os
import datetime
import io
from email.utils import parsedate_to_datetime
from pdfminer.high_level import extract_text as pdf_extract_text


GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']

# Campbell Oil - Pop N Go store
CAMPBELL_STORE_KEY = 'pn'

# The actual sender email for Campbell Oil price emails
CAMPBELL_SENDER = 'accounts@campbelloilco.com'


def fetch_campbell_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    results = []
    seen_ids = set()

    # Search INBOX with multiple strategies targeting the exact Campbell Oil sender
    # Gmail IMAP FROM search requires the full email address
    folder = 'INBOX'
    rv, _ = mail.select(folder, readonly=True)
    if rv != 'OK':
        print(f"Campbell: Could not select INBOX")
        mail.logout()
        return []

    # Use exact email address in FROM
    searches = [
        f'(SINCE {since_date} FROM "accounts@campbelloilco.com")',
        f'(SINCE {since_date} FROM "campbelloilco.com")',
        f'(SINCE {since_date} SUBJECT "Campbell oil: Daily Price Update")',
        f'(SINCE {since_date} SUBJECT "campbelloilco")',
    ]

    for search in searches:
        try:
            _, data = mail.search(None, search)
            ids = data[0].split() if data[0] else []
            print(f"Campbell: INBOX {search} => {len(ids)} ids")
            for eid in ids:
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    try:
                        _, msg_data = mail.fetch(eid, '(RFC822)')
                        msg = email.message_from_bytes(msg_data[0][1])
                        frm = msg.get('From', '')
                        subj = msg.get('Subject', '')
                        dt = msg.get('Date', '')
                        print(f"Campbell: id={eid} From={frm[:80]} | Subj={subj[:80]}")
                        # Only process emails from campbelloilco.com
                        if 'campbelloilco' not in frm.lower():
                            print(f"Campbell: Skipping non-Campbell sender: {frm[:60]}")
                            continue
                        try:
                            email_date = parsedate_to_datetime(dt).date()
                        except Exception:
                            email_date = datetime.date.today()
                        msg._campbell_date = email_date
                        results.append(msg)
                    except Exception as e:
                        print(f"Campbell: Error fetching id={eid}: {e}")
        except Exception as e:
            print(f"Campbell: Search error: {e}")

    # Also try [Gmail]/All Mail with the exact address
    try:
        rv2, _ = mail.select('"[Gmail]/All Mail"', readonly=True)
        if rv2 == 'OK':
            search = f'(SINCE {since_date} FROM "accounts@campbelloilco.com")'
            _, data = mail.search(None, search)
            ids = data[0].split() if data[0] else []
            print(f"Campbell: [Gmail]/All Mail FROM exact => {len(ids)} ids")
            for eid in ids:
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    try:
                        _, msg_data = mail.fetch(eid, '(RFC822)')
                        msg = email.message_from_bytes(msg_data[0][1])
                        frm = msg.get('From', '')
                        subj = msg.get('Subject', '')
                        print(f"Campbell: AllMail id={eid} From={frm[:80]} | Subj={subj[:80]}")
                        try:
                            email_date = parsedate_to_datetime(msg['Date']).date()
                        except Exception:
                            email_date = datetime.date.today()
                        msg._campbell_date = email_date
                        results.append(msg)
                    except Exception as e:
                        print(f"Campbell: AllMail error fetching {eid}: {e}")
    except Exception as e:
        print(f"Campbell: AllMail search error: {e}")

    mail.logout()
    print(f"Campbell: Total Campbell emails found: {len(results)}")
    return results


def parse_pdf_prices(pdf_bytes):
    """Parse Campbell Oil PDF price quotation using pdfminer."""
    try:
        text = pdf_extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f"Campbell: PDF extract failed: {e}")
        return None

    if not text:
        print("Campbell: PDF text is empty")
        return None

    print(f"Campbell PDF text (first 400): {text[:400]}")

    # Validate this is a Campbell Oil price quotation
    if 'campbelloilco' not in text.lower() and 'price quotation' not in text.lower() and 'total quote' not in text.lower():
        print("Campbell: PDF does not appear to be a Campbell Oil price quotation, skipping")
        return None

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    prices = {}

    for line in lines:
        # Regular 87 Eth row - look for numbers like 3.412506
        if re.search(r'Regular\s+87', line, re.IGNORECASE) or (re.search(r'Regular', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['reg'] = float(nums[-1])  # Total Quote is last number
                print(f"Campbell: reg={prices['reg']} from: {line[:60]}")

        # Premium 93 row
        if re.search(r'Premium\s+93', line, re.IGNORECASE) or (re.search(r'Premium', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['prem'] = float(nums[-1])
                print(f"Campbell: prem={prices['prem']} from: {line[:60]}")

        # Diesel Clr row
        if re.search(r'Diesel\s+Clr', line, re.IGNORECASE) or re.search(r'^Diesel', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['dsl'] = float(nums[-1])
                print(f"Campbell: dsl={prices['dsl']} from: {line[:60]}")

    # Extract start date from PDF (format: "Apr 10, 2026 6:00:00 PM")
    pdf_date = None
    m = re.search(r'Start\s*Date\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', text, re.IGNORECASE)
    if m:
        try:
            raw = m.group(1).strip().replace(',', '')
            pdf_date = datetime.datetime.strptime(raw, '%b %d %Y').strftime('%Y-%m-%d')
        except Exception as e:
            print(f"Campbell: Date parse error on '{m.group(1)}': {e}")

    print(f"Campbell: Parsed prices={prices}, pdf_date={pdf_date}")
    if not prices:
        print("Campbell: WARNING - no prices parsed from PDF")
    return prices, pdf_date


def get_pdf_from_msg(msg):
    """Extract PDF attachment bytes from email."""
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        disp = str(part.get('Content-Disposition', ''))
        is_pdf = (ct == 'application/pdf' or fn.lower().endswith('.pdf'))
        if is_pdf:
            print(f"Campbell: Found PDF attachment: {fn[:60]}")
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print("Campbell: No Campbell Oil emails found, skipping")
        return

    all_data = {}
    processed = 0
    for msg in emails:
        subj = msg.get('Subject', '')
        frm = msg.get('From', '')

        # Must have a PDF
        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            date_str = getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
            print(f"Campbell: No PDF in email dated {date_str}, subj={subj[:60]}")
            continue

        result = parse_pdf_prices(pdf_bytes)
        if not result:
            continue
        prices, pdf_date = result
        if not prices:
            continue

        processed += 1
        date_str = pdf_date if pdf_date else getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
        if date_str not in all_data:
            all_data[date_str] = {}
        all_data[date_str].update(prices)
        print(f"Campbell: Stored prices for {date_str}: {prices}")

    print(f"Campbell: Processed {processed} price emails successfully")

    if not all_data:
        print("Campbell: No data to store in prices.json")
        return

    # Update prices.json
    prices_file = 'prices.json'
    try:
        with open(prices_file, 'r') as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    updated = False
    for date_str, price_data in all_data.items():
        if date_str not in existing:
            existing[date_str] = {}
        pn_entry = {CAMPBELL_STORE_KEY: price_data}
        if existing[date_str].get(CAMPBELL_STORE_KEY) != price_data:
            existing[date_str].update(pn_entry)
            updated = True
            print(f"Campbell: Updated {date_str} in prices.json")

    if updated:
        with open(prices_file, 'w') as f:
            json.dump(existing, f, indent=2, sort_keys=True)
        print("Campbell: prices.json saved successfully")
    else:
        print("Campbell: No changes needed in prices.json")


if __name__ == '__main__':
    print("Starting Campbell Oil price fetch...")
    process_campbell_emails()
