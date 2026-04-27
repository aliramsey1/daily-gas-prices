import imaplib
import email
import re
import json
import os
import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']

STORE_MAP = {
    'Acadian Express': 'ae',
    'Acadiana Mart': 'am',
    'Moss Bluff Chevron': 'mb',
    'Moss Bluff': 'mb',
}
PRODUCT_MAP = {
    'E10 Regular Unleaded': 'reg',
    'E10 Super Unleaded': 'sup',
    'Highway Ultra Low Sulfur Diesel': 'die',
}

class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def get_text(self):
        return ' '.join(self.parts)

def strip_html(html):
    s = HTMLStripper()
    s.feed(html)
    return s.get_text()

def fetch_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    folders_to_try = ['"[Gmail]/All Mail"', 'INBOX']
    ids = []
    used_folder = None

    for folder in folders_to_try:
        try:
            status, _ = mail.select(folder, readonly=True)
            if status != 'OK':
                continue
            since = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
            status2, data = mail.search(None, '(FROM "evans.no.reply@gmail.com" SINCE ' + since + ')')
            if status2 == 'OK' and data[0]:
                ids = data[0].split()
                used_folder = folder
                print(f"Found {len(ids)} emails in {folder}")
                break
        except Exception as e:
            print(f"Folder {folder} error: {e}")
            continue

    if not ids:
        print("No emails found in any folder")
        mail.logout()
        return []

    emails = []
    for eid in ids:
        try:
            _, msg_data = mail.fetch(eid, '(RFC822)')
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append(msg)
        except Exception as e:
            print(f"Error fetching email {eid}: {e}")

    mail.logout()
    print(f"Fetched {len(emails)} emails from {used_folder}")
    return emails

def parse_date(msg):
    date_str = msg['Date']
    try:
        return parsedate_to_datetime(date_str).date()
    except Exception:
        return None

def get_body(msg):
    """Get plain text body, stripping HTML if needed."""
    plain = ''
    html = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            if 'attachment' in cd:
                continue
            if ct == 'text/plain':
                plain = part.get_payload(decode=True).decode('utf-8', errors='replace')
            elif ct == 'text/html':
                html = part.get_payload(decode=True).decode('utf-8', errors='replace')
    else:
        body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
        if '<html' in body.lower() or '<table' in body.lower():
            html = body
        else:
            plain = body

    if plain:
        return plain
    if html:
        return strip_html(html)
    return ''

def parse_prices(body):
    store_name = None
    for name in STORE_MAP:
        if name in body:
            store_name = name
            break
    if not store_name:
        return None, {}

    prices = {}
    for product, key in PRODUCT_MAP.items():
        # Try various delimiters: comma+spaces, tabs, multiple spaces
        for sep in [r'[,\s]+', r'[\t ]+', r'\s*,\s*']:
            pattern = re.escape(product) + sep + r'([\d.]+)' + sep + r'([\d.]+)' + sep + r'([\d.]+)' + sep + r'([\d.]+)'
            m = re.search(pattern, body)
            if m:
                prices[key] = float(m.group(4))
                break
    return store_name, prices

def load_existing():
    if os.path.exists('prices.json'):
        with open('prices.json') as f:
            return json.load(f)
    return {}

def save_prices(data):
    with open('prices.json', 'w') as f:
        json.dump(data, f, indent=2)

def update_index_html(all_prices):
    with open('index.html', 'r') as f:
        html = f.read()

    entries = []
    for date_str in sorted(all_prices.keys()):
        stores = all_prices[date_str]
        store_entries = []
        for sk, pdata in stores.items():
            prod_entries = []
            for pk, pv in pdata.items():
                prod_entries.append('"' + pk + '":' + str(pv))
            store_entries.append('"' + sk + '":{' + ','.join(prod_entries) + '}')
        entries.append('"' + date_str + '":{' + ','.join(store_entries) + '}')
    new_data = 'var D={' + ','.join(entries) + '};'

    html_new = re.sub(r'var D=\{[^;]*\};', new_data, html, flags=re.DOTALL)
    with open('index.html', 'w') as f:
        f.write(html_new)
    print(f"Updated index.html with {len(entries)} date entries")

def main():
    all_prices = load_existing()
    msgs = fetch_emails()
    new_count = 0
    for msg in msgs:
        date = parse_date(msg)
        if not date:
            continue
        date_str = date.strftime('%Y-%m-%d')
        subject = msg.get('Subject', '')
        if 'Evans Oil' not in subject and 'Latest prices' not in subject:
            continue
        body = get_body(msg)
        store_name, prices = parse_prices(body)
        if not store_name or not prices:
            continue
        store_key = STORE_MAP[store_name]
        if date_str not in all_prices:
            all_prices[date_str] = {}
        all_prices[date_str][store_key] = prices
        new_count += 1
        print(f"  Added {date_str} {store_name}: {prices}")

    save_prices(all_prices)
    update_index_html(all_prices)
    print(f"Done. Added {new_count} new entries. Total date entries: {len(all_prices)}")

if __name__ == '__main__':
    main()
