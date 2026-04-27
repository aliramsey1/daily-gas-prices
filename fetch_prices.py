import imaplib
import email
import re
import json
import os
import datetime
import io
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser


GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']


# ── Evans Oil stores ─────────────────────────────────────────────────────────
EVANS_STORE_MAP = {
    'Acadian Express': 'ae',
    'Acadiana Mart': 'am',
    'Moss Bluff Chevron': 'mb',
    'Iberia Stores': 'ib',
    'Bayou Stores': 'bs',
}

# ── Lavigne Oil stores (matched from email subject) ───────────────────────────
LAVIGNE_STORE_MAP = {
    'Complete Stop': 'cs',
    'Congress One Stop': 'co',
    'Star Stores': 'ss',
}

# ── Evans product codes ───────────────────────────────────────────────────────
EVANS_PRODUCT_MAP = {
    'E10 Regular Unleaded': 'reg',
    'E10 Super Unleaded': 'sup',
    'Highway Ultra Low Sulfur Diesel': 'die',
}

# ── Lavigne product codes (order matches PDF rows) ───────────────────────────
# Row order per rack: ULSD, Midgrade89, Premium93, Unleaded87
LAVIGNE_PRODUCT_ORDER = ['die', 'mid', 'prem', 'reg']


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


# ═══════════════════════════════════════════════════════════════════════════════
# EVANS OIL FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_evans_emails():
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
            print(f"Evans: Found {len(ids)} emails in {folder}")
            break
        except Exception as e:
            print(f"Error searching {folder}: {e}")
    if not ids:
        print("Evans: No emails found")
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
    print(f"Evans: Fetched {len(emails)} emails")
    return emails


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


def extract_evans_prices_from_section(section):
    prices = {}
    section_lower = section.lower()
    for product, key in EVANS_PRODUCT_MAP.items():
        product_lower = product.lower()
        for sep in [r'[,\s]+', r'[\t ]+', r'\s*,\s*']:
            pattern = re.escape(product_lower) + sep + r'([\d.]+)' + sep + r'([\d.]+)' + sep + r'([\d.]+)' + sep + r'([\d.]+)'
            m = re.search(pattern, section_lower)
            if m:
                prices[key] = float(m.group(4))
                break
    return prices


def parse_evans_all_stores(body):
    body_lower = body.lower()
    store_results = {}
    store_positions = []
    for name, code in EVANS_STORE_MAP.items():
        pos = body_lower.find(name.lower())
        if pos != -1:
            store_positions.append((pos, name, code))
    store_positions.sort(key=lambda x: x[0])
    for i, (pos, name, code) in enumerate(store_positions):
        next_pos = store_positions[i + 1][0] if i + 1 < len(store_positions) else len(body)
        section = body[pos:next_pos]
        prices = extract_evans_prices_from_section(section)
        if prices:
            store_results[code] = prices
            print(f"  Parsed {name}: {prices}")
        else:
            print(f"  No prices found for {name}")
    return store_results


def process_evans_emails(emails):
    all_data = {}
    for msg in emails:
        date_str_raw = msg['Date']
        try:
            date = parsedate_to_datetime(date_str_raw).date()
        except Exception:
            continue
        date_str = date.isoformat()
        body = get_body(msg)
        if not body:
            continue
        store_results = parse_evans_all_stores(body)
        if store_results:
            if date_str not in all_data:
                all_data[date_str] = {}
            all_data[date_str].update(store_results)
    return all_data


# ═══════════════════════════════════════════════════════════════════════════════
# LAVIGNE OIL FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_lavigne_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select('"[Gmail]/All Mail"', readonly=True)
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    _, data = mail.search(None, f'(SINCE {cutoff} SUBJECT "LAVIGNE OIL PRICE NOTIFICATIONS")')
    ids = data[0].split()
    print(f"Lavigne: Found {len(ids)} emails")
    emails = []
    for eid in ids:
        try:
            _, msg_data = mail.fetch(eid, '(RFC822)')
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            emails.append(msg)
        except Exception as e:
            print(f"Error fetching Lavigne email {eid}: {e}")
    mail.logout()
    return emails


def get_pdf_attachment(msg):
    for part in msg.walk():
        fn = part.get_filename()
        if fn and fn.lower().endswith('.pdf'):
            return part.get_payload(decode=True)
    return None


def extract_lavigne_store_from_subject(subject):
    subject = subject or ''
    subject_upper = subject.upper()
    for name, code in LAVIGNE_STORE_MAP.items():
        if name.upper() in subject_upper:
            return name, code
    return None, None


def parse_lavigne_pdf(pdf_bytes):
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f"  PDF parse error: {e}")
        return None, None

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Find rack headers - look for "Buckeye Opel" and "XOM BR" or "Chevron/Texaco"
    rack1_idx = None
    rack2_idx = None
    for i, line in enumerate(lines):
        line_up = line.upper()
        if 'BUCKEYE' in line_up and ('OPEL' in line_up or 'OPE' in line_up):
            rack1_idx = i
        elif ('XOM BR' in line_up or 'BR, LA' in line_up) and rack1_idx is not None:
            rack2_idx = i

    if rack1_idx is None or rack2_idx is None:
        print(f"  Could not find rack headers. rack1={rack1_idx}, rack2={rack2_idx}")
        # Debug: print lines
        for i, l in enumerate(lines[:30]):
            print(f"    [{i}] {l}")
        return None, None

    # Find "Total" and "Change" headers row
    total_idx = None
    for i in range(rack2_idx, len(lines)):
        if lines[i].upper() == 'TOTAL':
            total_idx = i
            break

    if total_idx is None:
        print(f"  Could not find Total header")
        return None, None

    # After "Total" and "Change" headers, price rows come
    # Skip "Change" line then collect 4 rows for rack1 then 4 rows for rack2
    # Actually looking at the log: lines are paired like "2.914020  0.052440"
    # Let's find rows after "Change"
    change_idx = None
    for i in range(total_idx, min(total_idx + 5, len(lines))):
        if lines[i].upper() == 'CHANGE':
            change_idx = i
            break

    if change_idx is None:
        print(f"  Could not find Change header")
        return None, None

    # Collect price rows after "Change" header
    price_rows = []
    for i in range(change_idx + 1, len(lines)):
        line = lines[i]
        # Pattern: two numbers separated by whitespace like "2.914020  0.052440"
        m = re.match(r'^(\d+\.\d+)\s+(\d+\.\d+)$', line)
        if m:
            price_rows.append(float(m.group(1)))
        elif price_rows:
            break  # Stop after first non-matching line after prices start

    if len(price_rows) < 8:
        print(f"  Only found {len(price_rows)} price rows (need 8)")
        return None, None

    # Rack 1 (Opelousas Buckeye): rows 0-3 = die, mid, prem, reg
    rack1 = {}
    for j, code in enumerate(LAVIGNE_PRODUCT_ORDER):
        rack1[code] = price_rows[j]

    # Rack 2 (Baton Rouge XOM): rows 4-7 = die, mid, prem, reg
    rack2 = {}
    for j, code in enumerate(LAVIGNE_PRODUCT_ORDER):
        rack2[code] = price_rows[4 + j]

    print(f"  Rack1 (Opelousas): {rack1}")
    print(f"  Rack2 (BR): {rack2}")
    return rack1, rack2


def get_lavigne_date(msg):
    date_str_raw = msg['Date']
    try:
        return parsedate_to_datetime(date_str_raw).date()
    except Exception:
        return None


def process_lavigne_emails(emails):
    all_data = {}
    for msg in emails:
        subject = msg['Subject'] or ''
        store_name, store_code = extract_lavigne_store_from_subject(subject)
        if not store_code:
            continue
        date = get_lavigne_date(msg)
        if not date:
            continue
        date_str = date.isoformat()
        pdf_bytes = get_pdf_attachment(msg)
        if not pdf_bytes:
            continue
        rack1, rack2 = parse_lavigne_pdf(pdf_bytes)
        if rack1 is None:
            continue
        if date_str not in all_data:
            all_data[date_str] = {}
        # Store prices per store per rack
        # Key format: store_code + '_op' (Opelousas) and store_code + '_br' (Baton Rouge)
        all_data[date_str][store_code + '_op'] = rack1
        all_data[date_str][store_code + '_br'] = rack2
        print(f"  Added {date_str} {store_name} (op+br)")
    return all_data


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

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

    # Evans Oil
    evans_emails = fetch_evans_emails()
    evans_data = process_evans_emails(evans_emails) if evans_emails else {}

    # Lavigne Oil
    lavigne_emails = fetch_lavigne_emails()
    lavigne_data = process_lavigne_emails(lavigne_emails) if lavigne_emails else {}

    # Merge
    all_data = {}
    for d, stores in evans_data.items():
        all_data.setdefault(d, {}).update(stores)
    for d, stores in lavigne_data.items():
        all_data.setdefault(d, {}).update(stores)

    prices_data = update_prices_json(all_data)
    update_index_html(prices_data)
    print("Done!")


if __name__ == '__main__':
    main()
