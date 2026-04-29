import imaplib
import email
import re
import json
import os
import datetime
import io
from email.utils import parsedate_to_datetime


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
    for mb in mailboxes[:30]:
        print(f"  {mb.decode('utf-8', errors='replace')}")

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    results = []
    seen_ids = set()

    # Search strategies across multiple mailboxes
    mailboxes_to_search = ['INBOX', '"[Gmail]/All Mail"']

    for folder in mailboxes_to_search:
        try:
            rv, _ = mail.select(folder, readonly=True)
            if rv != 'OK':
                print(f"Campbell: Could not select {folder}")
                continue

            # Try multiple subject searches
            searches = [
                f'(SINCE {since_date} SUBJECT "Price Update")',
                f'(SINCE {since_date} SUBJECT "Pop N Go")',
                f'(SINCE {since_date} FROM "accounts@campbelloilco.com")',
                f'(SINCE {since_date} FROM "campbelloilco.com")',
            ]

            for search in searches:
                _, data = mail.search(None, search)
                ids = data[0].split()
                print(f"Campbell: folder={folder} search={search} found {len(ids)} emails")
                for eid in ids:
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        try:
                            _, msg_data = mail.fetch(eid, '(RFC822)')
                            msg = email.message_from_bytes(msg_data[0][1])
                            print(f"Campbell: Email {eid} - From: {msg.get('From')} | Subject: {msg.get('Subject')} | Date: {msg.get('Date')}")
                            try:
                                email_date = parsedate_to_datetime(msg['Date']).date()
                            except Exception:
                                email_date = datetime.date.today()
                            msg._campbell_date = email_date
                            results.append(msg)
                        except Exception as e:
                            print(f"Campbell: Error fetching {eid}: {e}")
        except Exception as e:
            print(f"Campbell: Error with folder {folder}: {e}")

    mail.logout()
    print(f"Campbell: Total emails found: {len(results)}")
    return results


def parse_pdf_prices(pdf_bytes):
    """Parse Campbell Oil PDF price quotation."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = ''
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
        print(f"Campbell PDF text (first 500): {text[:500]}")
    except ImportError:
        # Fallback: try pypdf
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            text = ''
            for page in reader.pages:
                text += page.extract_text() + '\n'
            print(f"Campbell PDF text (pypdf, first 500): {text[:500]}")
        except Exception as e:
            print(f"Campbell: pypdf failed: {e}")
            return None

    prices = {}

    # Look for Regular 87 / Regular Eth
    m = re.search(r'Regular\s+87[^\n]*?([\d]+\.[\d]{4,6})', text, re.IGNORECASE)
    if not m:
        m = re.search(r'Regular[^\n]*?([\d]+\.[\d]{4,6})', text, re.IGNORECASE)
    if m:
        try:
            # Find the Total Quote column value (last number on the row)
            line = next(l for l in text.split('\n') if re.search(r'Regular\s*87', l, re.IGNORECASE) or re.search(r'Regular\s+Eth', l, re.IGNORECASE))
            nums = re.findall(r'[\d]+\.[\d]{4,6}', line)
            if nums:
                prices['reg'] = float(nums[-1])  # Total Quote is last
                print(f"Campbell: Regular price = {prices['reg']}")
        except Exception as e:
            print(f"Campbell: Regular parse error: {e}")

    # Look for Premium 93
    try:
        line = next((l for l in text.split('\n') if re.search(r'Premium', l, re.IGNORECASE)), None)
        if line:
            nums = re.findall(r'[\d]+\.[\d]{4,6}', line)
            if nums:
                prices['prem'] = float(nums[-1])
                print(f"Campbell: Premium price = {prices['prem']}")
    except Exception as e:
        print(f"Campbell: Premium parse error: {e}")

    # Look for Diesel
    try:
        line = next((l for l in text.split('\n') if re.search(r'Diesel', l, re.IGNORECASE)), None)
        if line:
            nums = re.findall(r'[\d]+\.[\d]{4,6}', line)
            if nums:
                prices['dsl'] = float(nums[-1])
                print(f"Campbell: Diesel price = {prices['dsl']}")
    except Exception as e:
        print(f"Campbell: Diesel parse error: {e}")

    # Extract date from PDF text (Start Date field)
    date_str = None
    m = re.search(r'Start\s+Date[^\n]*?(\w+\s+\d+,?\s+\d{4})', text, re.IGNORECASE)
    if m:
        try:
            from datetime import datetime as dt
            date_str = dt.strptime(m.group(1).strip().rstrip(','), '%b %d %Y').strftime('%Y-%m-%d')
        except Exception:
            pass
    if not date_str:
        m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', text)
        if m:
            try:
                from datetime import datetime as dt
                date_str = dt.strptime(m.group(1), '%m/%d/%Y').strftime('%Y-%m-%d')
            except Exception:
                pass

    print(f"Campbell: Parsed prices={prices}, date={date_str}")
    return prices, date_str


def get_pdf_from_msg(msg):
    """Extract PDF bytes from email message."""
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        disp = str(part.get('Content-Disposition', ''))
        if ct == 'application/pdf' or fn.lower().endswith('.pdf') or 'attachment' in disp and fn.endswith('.pdf'):
            print(f"Campbell: Found PDF attachment: {fn}")
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print("Campbell: No emails found, skipping update")
        return

    all_data = {}
    for msg in emails:
        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            date_str = getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')
            print(f"Campbell: No PDF in email dated {date_str}")
            continue

        result = parse_pdf_prices(pdf_bytes)
        if not result:
            continue
        prices, pdf_date = result
        if not prices:
            print("Campbell: No prices parsed from PDF")
            continue

        # Use PDF start date if available, else email date
        date_str = pdf_date if pdf_date else getattr(msg, '_campbell_date', datetime.date.today()).strftime('%Y-%m-%d')

        if date_str not in all_data:
            all_data[date_str] = {}
        all_data[date_str].update(prices)
        print(f"Campbell: Stored prices for {date_str}: {prices}")

    if not all_data:
        print("Campbell: No data found to store")
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
            print(f"Campbell: Updated prices.json for {date_str}")

    if updated:
        with open(prices_file, 'w') as f:
            json.dump(existing, f, indent=2, sort_keys=True)
        print("Campbell: prices.json saved")
    else:
        print("Campbell: No changes to prices.json")


if __name__ == '__main__':
    print("Starting Campbell Oil price fetch...")
    process_campbell_emails()
