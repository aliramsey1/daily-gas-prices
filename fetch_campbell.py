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
    since_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    results = []

    # Try [Gmail]/All Mail with broad search
    try:
        mail.select('"[Gmail]/All Mail"', readonly=True)
        # Try multiple search strategies
        searches = [
            f'(SINCE {since_date} SUBJECT "Campbell oil")',
            f'(SINCE {since_date} SUBJECT "Campbell")',
            f'(SINCE {since_date} FROM "campbelloilco")',
            f'(SINCE {since_date} SUBJECT "Daily Price Update")',
        ]
        all_ids = set()
        for search in searches:
            _, data = mail.search(None, search)
            ids = data[0].split()
            print(f"Campbell: search '{search}' found {len(ids)} emails")
            for i in ids:
                all_ids.add(i)

        print(f"Campbell: Total unique emails from all searches: {len(all_ids)}")

        for eid in list(all_ids)[:60]:
            try:
                # Fetch headers only first
                _, hdr_data = mail.fetch(eid, '(BODY[HEADER.FIELDS (SUBJECT FROM DATE)])')
                hdr = email.message_from_bytes(hdr_data[0][1])
                subj = hdr.get('Subject', '')
                frm = hdr.get('From', '')
                date_raw = hdr.get('Date', '')
                print(f"Campbell: Email {eid} - From: {frm[:50]} | Subject: {subj[:60]} | Date: {date_raw[:30]}")

                # Only process Campbell emails
                if 'campbell' not in subj.lower() and 'campbelloilco' not in frm.lower():
                    continue

                # Fetch full message
                _, msg_data = mail.fetch(eid, '(RFC822)')
                msg = email.message_from_bytes(msg_data[0][1])
                try:
                    email_date = parsedate_to_datetime(msg['Date']).date()
                except:
                    email_date = datetime.date.today()
                date_str = email_date.strftime('%Y-%m-%d')
                msg['date_str'] = date_str
                results.append(msg)
            except Exception as e:
                print(f"Campbell: Error fetching email {eid}: {e}")
    except Exception as e:
        print(f"Campbell: Error searching [Gmail]/All Mail: {e}")

    mail.logout()
    print(f"Campbell: Total matching emails: {len(results)}")
    return results


def get_pdf_attachment(msg):
    pdf_data = None
    for part in msg.walk():
        ct = part.get_content_type()
        fn = part.get_filename() or ''
        disp = str(part.get('Content-Disposition', ''))
        print(f"Campbell PDF search: ct={ct}, fn={fn}, disp={disp[:50]}")
        # Try various ways to detect PDF
        if ct == 'application/pdf':
            pdf_data = part.get_payload(decode=True)
            break
        elif fn.lower().endswith('.pdf'):
            pdf_data = part.get_payload(decode=True)
            break
        elif 'attachment' in disp and '.pdf' in disp.lower():
            pdf_data = part.get_payload(decode=True)
            break
        elif ct in ('application/octet-stream', 'application/x-pdf') and (fn.lower().endswith('.pdf') or '.pdf' in disp.lower()):
            pdf_data = part.get_payload(decode=True)
            break
    return pdf_data


def parse_campbell_pdf(pdf_bytes):
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes))
        print(f"Campbell PDF text (first 1000 chars):")
        print(repr(text[:1000]))
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        prices = {}
        for line in lines:
            line_lower = line.lower()
            m = re.search(r'(\d+\.\d{2,5})', line)
            if not m:
                continue
            val = float(m.group(1))
            if val < 1.0 or val > 10.0:
                continue
            if 'diesel' in line_lower or 'dsl' in line_lower or 'ulsd' in line_lower:
                prices['die'] = val
            elif 'premium' in line_lower or 'prem' in line_lower or 'super' in line_lower or 'plus' in line_lower:
                prices['prem'] = val
            elif 'mid' in line_lower or 'midgrade' in line_lower:
                prices['mid'] = val
            elif 'regular' in line_lower or 'reg' in line_lower or 'unl' in line_lower or 'unleaded' in line_lower:
                prices['reg'] = val
        if not prices:
            all_vals = re.findall(r'\b(\d+\.\d{3,5})\b', text)
            price_vals = [float(p) for p in all_vals if 1.0 < float(p) < 10.0]
            print(f"Campbell PDF fallback prices: {price_vals[:8]}")
            if len(price_vals) >= 1:
                prices['reg'] = price_vals[0]
            if len(price_vals) >= 2:
                prices['mid'] = price_vals[1]
            if len(price_vals) >= 3:
                prices['prem'] = price_vals[2]
            if len(price_vals) >= 4:
                prices['die'] = price_vals[3]
        return prices if prices else None
    except Exception as e:
        print(f"Campbell: PDF parse error: {e}")
        return None


def process_campbell_emails(emails):
    all_data = {}
    seen_dates = set()
    for msg in emails:
        try:
            date_str = msg.get('date_str', '')
            if not date_str or date_str in seen_dates:
                continue
            pdf_bytes = get_pdf_attachment(msg)
            if not pdf_bytes:
                print(f"Campbell: No PDF in email dated {date_str}")
                continue
            prices = parse_campbell_pdf(pdf_bytes)
            if not prices:
                print(f"Campbell: Could not parse prices from PDF dated {date_str}")
                continue
            seen_dates.add(date_str)
            all_data.setdefault(date_str, {})
            all_data[date_str][CAMPBELL_STORE_KEY] = prices
            print(f"Campbell: Saved prices for {date_str}: {prices}")
        except Exception as e:
            print(f"Campbell: Error processing email: {e}")
    return all_data


def update_campbell_in_prices_json(campbell_data):
    existing = {}
    try:
        with open('prices.json', 'r') as f:
            existing = json.load(f)
    except Exception:
        pass
    for d, stores in campbell_data.items():
        existing.setdefault(d, {}).update(stores)
    with open('prices.json', 'w') as f:
        json.dump(existing, f, separators=(',', ':'))
    print(f"Updated prices.json with {len(campbell_data)} Campbell dates")


def build_cd_js(campbell_data):
    lines = []
    lines.append('var CD={')
    date_keys = sorted(campbell_data.keys())
    for d in date_keys:
        stores = campbell_data[d]
        store_parts = []
        for sc, prods in stores.items():
            prod_parts = []
            for k, v in prods.items():
                prod_parts.append(f"{k}:{v}")
            store_parts.append(f"{sc}:{{{','.join(prod_parts)}}}")
        lines.append(f'"{d}":{{{",".join(store_parts)}}},')
    lines.append('};')
    return '\n'.join(lines)


def update_cd_in_index_html(campbell_data):
    cd_js = build_cd_js(campbell_data)
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    if 'var CD=' in html:
        html = re.sub(r'var CD=\{[\s\S]*?\};', cd_js, html)
    else:
        html = re.sub(r'(var GD=\{[\s\S]*?\};)', r'\1\n' + cd_js, html)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('Updated var CD= in index.html')


def main():
    print("Starting Campbell Oil price fetch...")
    campbell_emails = fetch_campbell_emails()
    campbell_data = process_campbell_emails(campbell_emails) if campbell_emails else {}
    if not campbell_data:
        print("Campbell: No data found, skipping update")
        return
    update_campbell_in_prices_json(campbell_data)
    update_cd_in_index_html(campbell_data)
    print("Campbell fetch done!")


if __name__ == '__main__':
    main()
