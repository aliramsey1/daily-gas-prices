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


def list_all_mailboxes(mail):
    """List all IMAP mailboxes and return their names."""
    rv, mailbox_data = mail.list()
    names = []
    for mb in (mailbox_data or []):
        mb_str = mb.decode('utf-8', errors='replace')
        # Parse the mailbox name from IMAP LIST response: (Flag) "/" "mailbox name"
        # Format: (HasChildren) "/" "Shared &JxQ-/ACCOUNTANT"
        m = re.search(r'"[^"]*"s+(.+)$', mb_str)
        if m:
            name = m.group(1).strip().strip('"')
            names.append(name)
    return names


def fetch_campbell_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    # List ALL mailboxes to find the right one
    all_mailboxes = list_all_mailboxes(mail)
    print(f"Campbell: Total mailboxes: {len(all_mailboxes)}")
    print("Campbell: All mailboxes:")
    for mb in all_mailboxes:
        print(f"  [{mb}]")

    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    results = []
    seen_ids = set()

    # Search ALL mailboxes for Campbell Oil price emails
    # Focus on ones that might contain the emails
    candidate_folders = []
    for mb in all_mailboxes:
        mb_lower = mb.lower()
        # Include: INBOX, All Mail, and any Sortd/custom labels
        if any(x in mb_lower for x in ['inbox', 'all mail', 'campbell', 'pop', 'gas', 'oil', 'price', 'fuel']):
            candidate_folders.append(mb)

    # Always include INBOX and All Mail
    for must_include in ['INBOX', '[Gmail]/All Mail']:
        if must_include not in candidate_folders:
            candidate_folders.insert(0, must_include)

    print(f"Campbell: Candidate folders: {candidate_folders}")

    # Search each candidate folder
    for folder in candidate_folders:
        # Quote the folder name if it contains spaces
        if ' ' in folder and not folder.startswith('"'):
            imap_folder = f'"{folder}"'
        elif folder.startswith('[Gmail]'):
            imap_folder = f'"{folder}"'
        else:
            imap_folder = folder

        try:
            rv, _ = mail.select(imap_folder, readonly=True)
            if rv != 'OK':
                print(f"Campbell: Cannot select {imap_folder}")
                continue

            # Broad search - just look for emails with PDF attachments from campbelloilco
            search = f'(SINCE {since_date} SUBJECT "Daily Price Update")'
            _, data = mail.search(None, search)
            ids = data[0].split() if data[0] else []
            if ids:
                print(f"Campbell: {imap_folder} SUBJECT 'Daily Price Update' => {len(ids)} ids")
                for eid in ids:
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        _, msg_data = mail.fetch(eid, '(RFC822)')
                        msg = email.message_from_bytes(msg_data[0][1])
                        frm = msg.get('From', '')
                        subj = msg.get('Subject', '')
                        print(f"  id={eid} From={frm[:80]} Subj={subj[:80]}")
                        try:
                            email_date = parsedate_to_datetime(msg['Date']).date()
                        except Exception:
                            email_date = datetime.date.today()
                        msg._campbell_date = email_date
                        results.append(msg)

        except Exception as e:
            print(f"Campbell: Error with {imap_folder}: {e}")

    # If still nothing found, try ALL mailboxes with broad search
    if not results:
        print("Campbell: No results from candidate folders, trying ALL mailboxes...")
        for folder in all_mailboxes[:60]:
            if folder in candidate_folders:
                continue
            if ' ' in folder and not folder.startswith('"'):
                imap_folder = f'"{folder}"'
            elif folder.startswith('[Gmail]') or folder.startswith('Shared'):
                imap_folder = f'"{folder}"'
            else:
                imap_folder = folder

            try:
                rv, _ = mail.select(imap_folder, readonly=True)
                if rv != 'OK':
                    continue
                search = f'(SINCE {since_date} SUBJECT "Daily Price Update")'
                _, data = mail.search(None, search)
                ids = data[0].split() if data[0] else []
                if ids:
                    print(f"Campbell: FOUND in {imap_folder}: {len(ids)} emails with 'Daily Price Update'")
                    for eid in ids:
                        if eid not in seen_ids:
                            seen_ids.add(eid)
                            _, msg_data = mail.fetch(eid, '(RFC822)')
                            msg = email.message_from_bytes(msg_data[0][1])
                            frm = msg.get('From', '')
                            subj = msg.get('Subject', '')
                            print(f"  Found: From={frm[:80]} Subj={subj[:80]}")
                            try:
                                email_date = parsedate_to_datetime(msg['Date']).date()
                            except Exception:
                                email_date = datetime.date.today()
                            msg._campbell_date = email_date
                            results.append(msg)
            except Exception:
                pass

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

    if not text:
        return None

    # Validate this is a Campbell Oil price quotation
    if 'campbelloilco' not in text.lower() and 'price quotation' not in text.lower():
        return None

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    prices = {}

    for line in lines:
        if re.search(r'Regular\s+87', line, re.IGNORECASE) or (re.search(r'Regular', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['reg'] = float(nums[-1])

        if re.search(r'Premium\s+93', line, re.IGNORECASE) or (re.search(r'Premium', line, re.IGNORECASE) and 'Eth' in line):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['prem'] = float(nums[-1])

        if re.search(r'Diesel\s+Clr', line, re.IGNORECASE) or re.search(r'^Diesel', line, re.IGNORECASE):
            nums = re.findall(r'\d+\.\d{4,6}', line)
            if nums:
                prices['dsl'] = float(nums[-1])

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
            return part.get_payload(decode=True)
    return None


def process_campbell_emails():
    emails = fetch_campbell_emails()
    if not emails:
        print("Campbell: No Campbell Oil emails found, skipping")
        return

    all_data = {}
    for msg in emails:
        subj = msg.get('Subject', '')
        frm = msg.get('From', '')

        # Only process emails from campbelloilco.com
        if 'campbelloilco' not in frm.lower():
            print(f"Campbell: Skipping non-Campbell sender: {frm[:60]}")
            continue

        pdf_bytes = get_pdf_from_msg(msg)
        if not pdf_bytes:
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
        print("Campbell: No valid Campbell Oil price data found")
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
        print("Campbell: prices.json saved")
    else:
        print("Campbell: No changes to prices.json")


if __name__ == '__main__':
    print("Starting Campbell Oil price fetch...")
    process_campbell_emails()
