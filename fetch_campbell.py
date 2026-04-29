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


def fetch_campbell_emails():
    """
    Fetch Campbell Oil price emails using Gmail's X-GM-RAW IMAP extension.
    This allows us to use the exact same search that works in Gmail web UI.
    """
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    results = []
    seen_message_ids = set()

    # Use [Gmail]/All Mail for X-GM-RAW searches (covers all labels)
    rv, _ = mail.select('"[Gmail]/All Mail"', readonly=True)
    print(f"Campbell: Select [Gmail]/All Mail: {rv}")

    if rv == 'OK':
        # Use Gmail's X-GM-RAW extension to search exactly like Gmail web UI
        # This bypasses Gmail's non-standard IMAP FROM behavior
        gmail_queries = [
            f'(SINCE {since_date} X-GM-RAW "from:accounts@campbelloilco.com")',
            f'(SINCE {since_date} X-GM-RAW "subject:Daily Price Update from:campbelloilco")',
            f'(SINCE {since_date} X-GM-RAW "subject:\"Campbell oil: Daily Price Update\"")',
        ]

        all_ids = set()
        for query in gmail_queries:
            try:
                _, data = mail.search(None, query)
                ids = data[0].split() if data[0] else []
                print(f"Campbell: {query[:80]} => {len(ids)} ids")
                all_ids.update(ids)
            except Exception as e:
                print(f"Campbell: Search error: {e}")

        # Fallback: try standard IMAP searches
        if not all_ids:
            print("Campbell: X-GM-RAW failed or found nothing, trying standard searches...")
            fallback_searches = [
                f'(SINCE {since_date} FROM "accounts@campbelloilco.com")',
                f'(SINCE {since_date} FROM "@campbelloilco.com")',
                f'(SINCE {since_date} SUBJECT "Campbell oil: Daily Price")',
            ]
            for search in fallback_searches:
                try:
                    _, data = mail.search(None, search)
                    ids = data[0].split() if data[0] else []
                    print(f"Campbell: fallback {search[:60]} => {len(ids)}")
                    all_ids.update(ids)
                except Exception as e:
                    print(f"Campbell: Fallback error: {e}")

        print(f"Campbell: Total unique message IDs from All Mail: {len(all_ids)}")

        for eid in list(all_ids):
            try:
                _, msg_data = mail.fetch(eid, '(RFC822)')
                msg = email.message_from_bytes(msg_data[0][1])
                frm = msg.get('From', '')
                subj = msg.get('Subject', '')
                mid = msg.get('Message-ID', eid)
                print(f"Campbell: id={eid} From={frm[:80]} Subj={subj[:80]}")

                if mid in seen_message_ids:
                    continue
                seen_message_ids.add(mid)

                try:
                    email_date = parsedate_to_datetime(msg['Date']).date()
                except Exception:
                    email_date = datetime.date.today()
                msg._campbell_date = email_date
                results.append(msg)
            except Exception as e:
                print(f"Campbell: Error fetching {eid}: {e}")

    # Also try INBOX
    rv2, _ = mail.select('INBOX', readonly=True)
    if rv2 == 'OK':
        try:
            _, data = mail.search(None, f'(SINCE {since_date} X-GM-RAW "from:accounts@campbelloilco.com")')
            inbox_ids = data[0].split() if data[0] else []
            print(f"Campbell: INBOX X-GM-RAW => {len(inbox_ids)} ids")
            for eid in inbox_ids:
                _, msg_data = mail.fetch(eid, '(RFC822)')
                msg = email.message_from_bytes(msg_data[0][1])
                mid = msg.get('Message-ID', str(eid))
                frm = msg.get('From', '')
                subj = msg.get('Subject', '')
                print(f"Campbell: INBOX id={eid} From={frm[:80]} Subj={subj[:80]}")
                if mid not in seen_message_ids:
                    seen_message_ids.add(mid)
                    try:
                        email_date = parsedate_to_datetime(msg['Date']).date()
                    except Exception:
                        email_date = datetime.date.today()
                    msg._campbell_date = email_date
                    results.append(msg)
        except Exception as e:
            print(f"Campbell: INBOX X-GM-RAW error: {e}")

    mail.logout()
    print(f"Campbell: Total emails found: {len(results)}")
    return results


def parse_pdf_prices(pdf_bytes):
    """Parse Campbell Oil PDF price quotation using pdfminer."""
    try:
        text = pdf_extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f"Campbell: PDF extract failed: {e}")
        return None

    if not text or len(text.strip()) < 50:
        return None

    print(f"Campbell PDF text (first 300): {text[:300]}")

    # Validate this is a Campbell Oil price quotation
    if 'campbelloilco' not in text.lower() and 'price quotation' not in text.lower():
        print("Campbell: PDF is not a price quotation, skipping")
        return None

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    prices = {}

    for line in lines:
        if re.search(r'Regular\s+87', line, re.IGNORECASE) or (re.search(r'Regular', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['reg'] = float(nums[-1])
                print(f"Campbell: reg={prices['reg']}")

        if re.search(r'Premium\s+93', line, re.IGNORECASE) or (re.search(r'Premium', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['prem'] = float(nums[-1])
                print(f"Campbell: prem={prices['prem']}")

        if re.search(r'Diesel\s+Clr', line, re.IGNORECASE) or re.search(r'^Diesel', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['dsl'] = float(nums[-1])
                print(f"Campbell: dsl={prices['dsl']}")

    pdf_date = None
    m = re.search(r'Start\s*Date\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', text, re.IGNORECASE)
    if m:
        try:
            raw = m.group(1).strip().replace(',', '')
            pdf_date = datetime.datetime.strptime(raw, '%b %d %Y').strftime('%Y-%m-%d')
        except Exception:
            pass

    print(f"Campbell: Parsed prices={prices}, pdf_date={pdf_date}")
    return prices, pdf_date


def get_pdf_from_msg(msg):
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        if ct == 'application/pdf' or fn.lower().endswith('.pdf'):
            print(f"Campbell: Found PDF: {fn[:60]}")
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print("Campbell: No emails found, skipping")
        return

    all_data = {}
    for msg in emails:
        frm = msg.get('From', '')
        subj = msg.get('Subject', '')

        # Only process emails from campbelloilco.com
        if 'campbelloilco' not in frm.lower():
            print(f"Campbell: Skipping non-Campbell email: {frm[:60]}")
            continue

        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            date_str = getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
            print(f"Campbell: No PDF in email {date_str}: {subj[:60]}")
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
        print(f"Campbell: Stored prices for {date_str}: {prices}")

    if not all_data:
        print("Campbell: No valid Campbell Oil price data to store")
        return

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

    if updated:
        with open(prices_file, 'w') as f:
            json.dump(existing, f, indent=2, sort_keys=True)
        print("Campbell: prices.json saved successfully")
    else:
        print("Campbell: No changes to prices.json needed")


if __name__ == '__main__':
    print("Starting Campbell Oil price fetch...")
    process_campbell_emails()
