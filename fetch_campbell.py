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

    # Search only for Daily Price Update emails
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

            if mid in seen_message_ids:
                continue
            seen_message_ids.add(mid)

            # Only process emails from campbelloilco.com
            if 'campbelloilco' not in frm.lower():
                continue

            # Only process Daily Price Update emails
            if 'price update' not in subj.lower():
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


def parse_pdf_prices(pdf_bytes, debug=False):
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

    if debug:
        print(f'Campbell: PDF text (first 800 chars):')
        print(repr(text[:800]))

    prices = {}
    pdf_date = None

    # Try line-by-line parsing first
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        # Check for product name then look for Total Quote number on same or next line
        if re.search(r'Regular.*87.*Eth', line, re.IGNORECASE) or re.search(r'Regular.*Eth', line, re.IGNORECASE):
            # Look for 4+ decimal numbers on same line or next 2 lines
            for check_line in lines[i:i+3]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:  # Cost, Taxes, Total Quote
                    prices['reg'] = float(nums[-1])  # Last number = Total Quote
                    print(f'Campbell: reg={prices["reg"]} from line: {check_line[:60]}')
                    break
                elif len(nums) == 1 and i + 3 < len(lines):
                    # Numbers may be split across lines (one per line in table)
                    # Look ahead for 2 more number-only lines
                    all_nums = []
                    for j in range(i, min(i+6, len(lines))):
                        ns = re.findall(r'^\d+\.\d{4,6}$', lines[j])
                        if ns:
                            all_nums.extend(ns)
                    if len(all_nums) >= 3:
                        prices['reg'] = float(all_nums[-1])
                        print(f'Campbell: reg={prices["reg"]} (split lines)')
                        break

        if re.search(r'Premium.*93.*Eth', line, re.IGNORECASE) or re.search(r'Premium.*Eth', line, re.IGNORECASE):
            for check_line in lines[i:i+3]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['prem'] = float(nums[-1])
                    print(f'Campbell: prem={prices["prem"]} from line: {check_line[:60]}')
                    break
                elif len(nums) == 1:
                    all_nums = []
                    for j in range(i, min(i+6, len(lines))):
                        ns = re.findall(r'^\d+\.\d{4,6}$', lines[j])
                        if ns:
                            all_nums.extend(ns)
                    if len(all_nums) >= 3:
                        prices['prem'] = float(all_nums[-1])
                        print(f'Campbell: prem={prices["prem"]} (split lines)')
                        break

        if re.search(r'Diesel.*Clr', line, re.IGNORECASE) or re.search(r'^Diesel', line, re.IGNORECASE):
            for check_line in lines[i:i+3]:
                nums = re.findall(r'\d+\.\d{4,6}', check_line)
                if len(nums) >= 3:
                    prices['dsl'] = float(nums[-1])
                    print(f'Campbell: dsl={prices["dsl"]} from line: {check_line[:60]}')
                    break
                elif len(nums) == 1:
                    all_nums = []
                    for j in range(i, min(i+6, len(lines))):
                        ns = re.findall(r'^\d+\.\d{4,6}$', lines[j])
                        if ns:
                            all_nums.extend(ns)
                    if len(all_nums) >= 3:
                        prices['dsl'] = float(all_nums[-1])
                        print(f'Campbell: dsl={prices["dsl"]} (split lines)')
                        break

    # Also try full-text search as fallback if line parsing fails
    if not prices:
        # Try searching entire text for number patterns near product names
        m = re.search(r'Regular.*?Eth.*?(\d+\.\d{4,6})\s*$', text, re.IGNORECASE | re.MULTILINE)
        if m:
            prices['reg'] = float(m.group(1))
            print(f'Campbell: reg={prices["reg"]} (fulltext)')
        m = re.search(r'Premium.*?Eth.*?(\d+\.\d{4,6})\s*$', text, re.IGNORECASE | re.MULTILINE)
        if m:
            prices['prem'] = float(m.group(1))
            print(f'Campbell: prem={prices["prem"]} (fulltext)')
        m = re.search(r'Diesel.*?Clr.*?(\d+\.\d{4,6})\s*$', text, re.IGNORECASE | re.MULTILINE)
        if m:
            prices['dsl'] = float(m.group(1))
            print(f'Campbell: dsl={prices["dsl"]} (fulltext)')

    # Parse Start Date
    m = re.search(r'Start\s*Date[:\s]*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', text, re.IGNORECASE)
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
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print('Campbell: No Campbell Oil price update emails found, skipping')
        return

    all_data = {}
    debug_printed = False
    for msg in emails:
        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
            continue

        # Print PDF text for first PDF to diagnose parsing
        result = parse_pdf_prices(pdf_bytes, debug=(not debug_printed))
        if result is not None:
            debug_printed = True
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
