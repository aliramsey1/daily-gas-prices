import imaplib
import email
import re
import json
import os
import datetime

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']

STORE_MAP = {
    'Acadian Express': 'ae',
    'Acadiana Mart': 'am',
    'Moss Bluff Chevron': 'mb',
}
PRODUCT_MAP = {
    'E10 Regular Unleaded': 'reg',
    'E10 Super Unleaded': 'sup',
    'Highway Ultra Low Sulfur Diesel': 'die',
}

def fetch_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select('inbox')
    # Search last 60 days
    since = (datetime.date.today() - datetime.timedelta(days=60)).strftime('%d-%b-%Y')
    status, data = mail.search(None, f'(FROM "evans.no.reply@gmail.com" SUBJECT "Latest prices from Evans Oil Company" SINCE {since})')
    ids = data[0].split()
    emails = []
    for eid in ids:
        _, msg_data = mail.fetch(eid, '(RFC822)')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        emails.append(msg)
    mail.logout()
    return emails

def parse_date(msg):
    date_str = msg['Date']
    for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z']:
        try:
            dt = datetime.datetime.strptime(date_str.strip(), fmt)
            return dt.date()
        except ValueError:
            pass
    # fallback: try parsing with email.utils
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).date()
    except Exception:
        return None

def get_body(msg):
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                break
            elif ct == 'text/html' and not body:
                body = part.get_payload(decode=True).decode('utf-8', errors='replace')
    else:
        body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
    return body

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
        # Match: ProductName, unitprice, tax, freight, total
        pattern = re.escape(product) + r'[,\s]+([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)'
        m = re.search(pattern, body)
        if m:
            prices[key] = float(m.group(4))  # total price
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

    # Build the JS data object
    js_lines = ['var D={']
    for date_str in sorted(all_prices.keys()):
        stores = all_prices[date_str]
        store_parts = []
        for sk, pdata in stores.items():
            prod_parts = [f'{pk}:{pv}' for pk, pv in pdata.items()]
            store_parts.append('{' + ','.join(prod_parts) + '}')
        js_lines.append(f'  "{date_str}":{{{",".join([f"{sk}:{{{",".join([f\"{pk}\":{pv} for pk,pv in stores[sk].items()])}}}" for sk in stores])}}},')
    js_lines.append('};')

    # Build proper JS
    entries = []
    for date_str in sorted(all_prices.keys()):
        stores = all_prices[date_str]
        store_entries = []
        for sk, pdata in stores.items():
            prod_entries = [f'"{pk}":{pv}' for pk, pv in pdata.items()]
            store_entries.append(f'"{sk}":{{{",".join(prod_entries)}}}')
        entries.append(f'"{date_str}":{{{",".join(store_entries)}}}')
    new_data = 'var D={' + ','.join(entries) + '};'

    # Replace existing var D={...}; in the HTML
    html_new = re.sub(r'var D=\{[^;]*\};', new_data, html, flags=re.DOTALL)
    with open('index.html', 'w') as f:
        f.write(html_new)

def main():
    all_prices = load_existing()
    msgs = fetch_emails()
    for msg in msgs:
        date = parse_date(msg)
        if not date:
            continue
        date_str = date.strftime('%Y-%m-%d')
        body = get_body(msg)
        store_name, prices = parse_prices(body)
        if not store_name or not prices:
            continue
        store_key = STORE_MAP[store_name]
        if date_str not in all_prices:
            all_prices[date_str] = {}
        all_prices[date_str][store_key] = prices

    save_prices(all_prices)
    update_index_html(all_prices)
    print(f"Updated {len(all_prices)} date entries.")

if __name__ == '__main__':
    main()
