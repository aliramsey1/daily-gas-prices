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
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    # List all available mailboxes for diagnostics
    rv, mailboxes = mail.list()
    print("Campbell: Available mailboxes:")
    for mb in (mailboxes or [])[:40]:
        print(f"  {mb.decode('utf-8', errors='replace')}")

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    results = []
    seen_ids = set()

    # Search INBOX first (confirmed emails are there), then All Mail
    mailboxes_to_search = ['INBOX', '"[Gmail]/All Mail"']

    for folder in mailboxes_to_search:
        try:
            rv, _ = mail.select(folder, readonly=True)
            if rv != 'OK':
                print(f"Campbell: Could not select {folder}, rv={rv}")
                continue

            # Try multiple subject searches
            search_terms = [
                f'(SINCE {since_date} SUBJECT "Price Update")',
                f'(SINCE {since_date} SUBJECT "Pop N Go")',
                f'(SINCE {since_date} FROM "campbelloilco")',
                f'(SINCE {since_date} SUBJECT "Campbell")',
            ]

            for search in search_terms:
                try:
                    _, data = mail.search(None, search)
                    ids = data[0].split() if data[0] else []
                    print(f"Campbell: folder={folder} {search} => {len(ids)} ids")
                    for eid in ids:
                        if eid not in seen_ids:
                            seen_ids.add(eid)
                            try:
                                _, msg_data = mail.fetch(eid, '(RFC822)')
                                msg = email.message_from_bytes(msg_data[0][1])
                                subj = msg.get('Subject', '')
                                frm = msg.get('From', '')
                                dt = msg.get('Date', '')
                                print(f"Campbell: id={eid} From={frm} | Subj={subj} | Date={dt}")
                                try:
                                    email_date = parsedate_to_datetime(msg['Date']).date()
                                except Exception:
                                    email_date = datetime.date.today()
                                msg._campbell_date = email_date
                                results.append(msg)
                            except Exception as e:
                                print(f"Campbell: Error fetching id={eid}: {e}")
                except Exception as e:
                    print(f"Campbell: Search error ({search}): {e}")
        except Exception as e:
            print(f"Campbell: Error selecting {folder}: {e}")

    mail.logout()
    print(f"Campbell: Total emails fetched: {len(results)}")
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

    print(f"Campbell PDF text (first 600): {text[:600]}")

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    prices = {}

    for line in lines:
        # Regular 87 Eth row
        if re.search(r'Regular\s+87', line, re.IGNORECASE) or re.search(r'Regular\s+Eth', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['reg'] = float(nums[-1])  # Total Quote is last number
                print(f"Campbell: reg={prices['reg']} from line: {line}")

        # Premium 93 row
        if re.search(r'Premium\s+93', line, re.IGNORECASE) or re.search(r'Premium\s+Eth', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['prem'] = float(nums[-1])
                print(f"Campbell: prem={prices['prem']} from line: {line}")

        # Diesel row
        if re.search(r'Diesel\s+Clr', line, re.IGNORECASE) or re.search(r'Diesel', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['dsl'] = float(nums[-1])
                print(f"Campbell: dsl={prices['dsl']} from line: {line}")

    # Extract start date from PDF
    pdf_date = None
    m = re.search(r'Start\s+Date\s+(\w+\s+\d+,?\s+\d{4})', text, re.IGNORECASE)
    if m:
        try:
            from datetime import datetime as dt
            date_str_raw = m.group(1).strip().replace(',', '')
            # Try formats: "Apr 10 2026" or "Apr 10, 2026"
            pdf_date = dt.strptime(date_str_raw, '%b %d %Y').strftime('%Y-%m-%d')
        except Exception as e:
            print(f"Campbell: Date parse error: {e}")

    print(f"Campbell: Parsed prices={prices}, pdf_date={pdf_date}")
    return prices, pdf_date


def get_pdf_from_msg(msg):
    """Extract PDF attachment bytes from email."""
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        disp = str(part.get('Content-Disposition', ''))
        is_pdf = (ct == 'application/pdf' or fn.lower().endswith('.pdf') or
                  (('attachment' in disp) and fn.lower().endswith('.pdf')))
        if is_pdf:
            print(f"Campbell: Found PDF: {fn} (ct={ct})")
            return part.get_payload(decode=True)
    print("Campbell: No PDF attachment found in message")
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print("Campbell: No emails found, skipping")
        return

    all_data = {}
    for msg in emails:
        subj = msg.get('Subject', '')
        # Only process actual price update emails
        if 'price update' not in subj.lower() and 'pop n go' not in subj.lower():
            print(f"Campbell: Skipping non-price email: {subj}")
            continue

        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            date_str = getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
            print(f"Campbell: No PDF in price email dated {date_str}, subj={subj}")
            continue

        result = parse_pdf_prices(pdf_bytes)
        if not result:
            continue
        prices, pdf_date = result
        if not prices:
            print("Campbell: No prices parsed")
            continue

        date_str = pdf_date if pdf_date else getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
        if date_str not in all_data:
            all_data[date_str] = {}
        all_data[date_str].update(prices)
        print(f"Campbell: Stored prices for {date_str}: {prices}")

    if not all_data:
        print("Campbell: No data to store")
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
