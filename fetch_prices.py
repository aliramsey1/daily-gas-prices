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
    'Iberia Stores': 'ib',
    'Bayou Stores': 'bs',
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
            rv, _ = mail.select(folder, readonly=True)
            if rv != 'OK':
                continue
            cutoff = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
            _, data = mail.search(None, f'(SINCE {cutoff} SUBJECT "Latest prices")')
            ids = data[0].split()
            used_folder = folder
            print(f"Found {len(ids)} emails in {folder}")
            break
        except Exception as e:
            print(f"Error searching {folder}: {e}")
    if not ids:
        print("No emails found")
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


def extract_prices_from_section(section):
    prices = {}
    section_lower = section.lower()
    for product, key in PRODUCT_MAP.items():
        product_lower = product.lower()
        for sep in [r'[,\s]+', r'[\t ]+', r'\s*,\s*']:
            pattern = re.escape(product_lower) + sep + r'([\d.]+)' + sep + r'([\d.]+)' + sep + r'([\d.]+)' + sep + r'([\d.]+)'
            m = re.search(pattern, section_lower)
            if m:
                prices[key] = float(m.group(4))
                break
    return prices


def parse_all_stores(body):
    body_lower = body.lower()
    store_results = {}
    store_positions = []
    for name, code in STORE_MAP.items():
        pos = body_lower.find(name.lower())
        if pos != -1:
            store_positions.append((pos, name, code))
    store_positions.sort(key=lambda x: x[0])
    for i, (pos, name, code) in enumerate(store_positions):
        next_pos = store_positions[i + 1][0] if i + 1 < len(store_positions) else len(body)
        section = body[pos:next_pos]
        prices = extract_prices_from_section(section)
        if prices:
            store_results[code] = prices
            print(f"  Parsed {name}: {prices}")
        else:
            print(f"  No prices found for {name} in section")
    return store_results


def update_prices_json(all_data):
    prices_file = 'prices.json'
    if os.path.exists(prices_file):
        with open(prices_file, 'r') as f:
            existing = json.load(f)
    else:
        existing = {}
    added = 0
    for date_str, store_data in all_data.items():
        if date_str not in existing:
            existing[date_str] = {}
        for store_code, prices in store_data.items():
            if store_code not in existing[date_str]:
                existing[date_str][store_code] = prices
                added += 1
                print(f"Added {date_str} {store_code}: {prices}")
    with open(prices_file, 'w') as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    print(f"Total new entries added: {added}")
    return existing


def build_js_data(prices_data):
    lines = []
    lines.append('var D={')
    date_keys = sorted(prices_data.keys())
    for i, dk in enumerate(date_keys):
        stores = prices_data[dk]
        store_parts = []
        for sc, prods in stores.items():
            prod_parts = []
            for pk, pv in prods.items():
                prod_parts.append(f'"{pk}":{pv}')
            store_parts.append(f'"{sc}":{{{",".join(prod_parts)}}}')
        comma = ',' if i < len(date_keys) - 1 else ''
        lines.append(f'"{dk}":{{{",".join(store_parts)}}}{comma}')
    lines.append('};')
    return '\n'.join(lines)


def update_index_html(prices_data):
    js_data = build_js_data(prices_data)
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r'var D=\{[\s\S]*?\};', js_data, html)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Updated index.html with new price data")


def main():
    print("Starting price fetch...")
    emails = fetch_emails()
    if not emails:
        print("No emails to process")
        return
    all_data = {}
    for msg in emails:
        date = parse_date(msg)
        if not date:
            continue
        date_str = date.isoformat()
        body = get_body(msg)
        if not body:
            continue
        store_results = parse_all_stores(body)
        if store_results:
            if date_str not in all_data:
                all_data[date_str] = {}
            all_data[date_str].update(store_results)
    prices_data = update_prices_json(all_data)
    update_index_html(prices_data)
    print("Done!")


if __name__ == '__main__':
    main()
