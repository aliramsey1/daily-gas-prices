import requests
import re
import json
import os
import datetime
from bs4 import BeautifulSoup

GUILLORY_URL = 'https://www.guilloryoil.net'
GUILLORY_USERNAME = os.environ.get('GUILLORY_USERNAME', '')
GUILLORY_PASSWORD = os.environ.get('GUILLORY_PASSWORD', '')

GUILLORY_STORES = {
    '000002080': 'gw',
    '000002555': 'ge',
    '000005310': 'gr',
    '000005315': 'gc',
    '000004644': 'gh',
    '000005295': 'gb',
}

GUILLORY_PRODUCTS = {
    'REGULAR E10': 'reg',
    'REGULAR': 'reg_pure',
    'PLUS E10': 'mid',
    'SUPER E10': 'sup',
    'ULTRA L/S CLEAR DIESEL FUEL': 'die',
}

def guillory_login():
    """Log in and return (session, user_id) ready for price-history POSTs."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    })

    # GET login page - grab CSRF token
    login_url = GUILLORY_URL + '/account/?login'
    r1 = session.get(login_url)
    print('  Step1 GET login: status=' + str(r1.status_code))
    soup = BeautifulSoup(r1.text, 'html.parser')
    csrf_input = soup.find('input', {'name': 'csrf_token'})
    csrf_value = csrf_input['value'] if csrf_input else ''
    user_app_id_input = soup.find('input', {'name': 'user_app_id'})
    user_app_id_value = user_app_id_input['value'] if user_app_id_input else '0'

    # POST login
    session.headers.update({'Referer': login_url, 'Origin': GUILLORY_URL})
    login_payload = {
        'user_app_id': user_app_id_value,
        'account_number': '',
        'csrf_token': csrf_value,
        'redirect_uri': '',
        'user_name': GUILLORY_USERNAME,
        'user_password': GUILLORY_PASSWORD,
        'user_remember': 'on',
    }
    r2 = session.post(GUILLORY_URL + '/account/', data=login_payload, allow_redirects=True)
    print('  Step2 POST login: status=' + str(r2.status_code) + ', URL=' + str(r2.url))

    # GET price-history page to grab the user_id hidden field
    session.headers.update({'Referer': GUILLORY_URL + '/account/'})
    for h in ('Content-Type', 'X-Requested-With'):
        session.headers.pop(h, None)

    r3 = session.get(GUILLORY_URL + '/account/price-history')
    print('  Step3 GET price-history: status=' + str(r3.status_code) + ', len=' + str(len(r3.text)))

    is_login = ('login-container' in r3.text or 'user_password' in r3.text)
    if is_login:
        print('  ERROR: Session not persisting - got login page back')
        return session, ''

    # Extract user_id from the hidden form field
    soup3 = BeautifulSoup(r3.text, 'html.parser')
    uid_input = soup3.find('input', {'name': 'user_id'})
    user_id = uid_input['value'] if uid_input else ''
    print('  Extracted user_id: ' + str(user_id))
    print('  Login + session: SUCCESS')
    return session, user_id

def fetch_guillory_prices(session, user_id, account_number, days=90):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days)
    date_start = start.strftime('%m/%d/%Y')
    date_end = today.strftime('%m/%d/%Y')

    payload = {
        'search': '1',
        'results': '9999',
        'user_id': user_id,
        'account_number': account_number,
        'date_start': date_start,
        'date_end': date_end,
        'location_number': '',
        'supplier_id': '',
        'terminal_id': '',
        'product_id': '',
    }

    post_headers = {
        'Referer': GUILLORY_URL + '/account/price-history',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': GUILLORY_URL,
    }
    r = session.post(
        GUILLORY_URL + '/account/price-history',
        data=payload,
        headers=post_headers
    )
    print('  POST ' + account_number + ': status=' + str(r.status_code) + ', len=' + str(len(r.text)))

    is_login = ('login-container' in r.text or 'user_password' in r.text)
    if is_login:
        print('  Got login page - session expired for ' + account_number)
        return ''

    return r.text

def parse_guillory_html(html):
    if not html:
        return {}

    # The Guillory site uses a server-rendered HTML table with DataTables
    # Columns: Date | Time | Product | Base | Surcharge | Taxes | Total | Change
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    if tables:
        print('  Found ' + str(len(tables)) + ' table(s), parsing...')
        result = _tables_to_result(tables)
        if result:
            print('  Table parse success: ' + str(len(result)) + ' dates')
            return result

    # Fallback: DataTable single-quote JS pattern
    m = re.search(r"'data':\s*(\[\[.*?\]\])", html, re.DOTALL)
    if m:
        print('  Matched single-quote DataTable')
        try:
            return _rows_to_result(json.loads(m.group(1)))
        except Exception as e:
            print('  Error: ' + str(e))

    # Fallback: DataTable double-quote JS pattern
    m = re.search(r'"data":\s*(\[\[.*?\]\])', html, re.DOTALL)
    if m:
        print('  Matched double-quote DataTable')
        try:
            return _rows_to_result(json.loads(m.group(1)))
        except Exception as e:
            print('  Error: ' + str(e))

    print('  No data found. First 600 chars: ' + html[:600])
    return {}

def _rows_to_result(data):
    # Columns: [date, time, loc_id, loc_name, prod_id, product, base, surcharge, taxes, total, change]
    result = {}
    for row in data:
        try:
            date_raw = row[0]
            product = row[5]
            total = float(row[9])
            d = datetime.datetime.strptime(date_raw, '%m/%d/%Y').date()
            date_str = d.isoformat()
            prod_key = GUILLORY_PRODUCTS.get(product)
            if not prod_key:
                continue
            if date_str not in result:
                result[date_str] = {}
            result[date_str][prod_key] = total
        except Exception as e:
            print('  Row error: ' + str(e))
    return result

def _tables_to_result(tables):
    # Table columns (from live inspection): Date | Time | Product | Base | Surcharge | Taxes | Total | Change
    result = {}
    for table in tables:
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue
        headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(['th', 'td'])]
        print('  Table headers: ' + str(headers))

        # Find column indices by header name
        date_idx = next((i for i, h in enumerate(headers) if 'date' in h or 'eff' in h), None)
        prod_idx = next((i for i, h in enumerate(headers) if 'product' in h), None)
        # Total is 7th column (index 6) in this table: Date|Time|Product|Base|Surcharge|Taxes|Total|Change
        total_idx = next((i for i, h in enumerate(headers) if 'total' in h), None)

        if date_idx is None or prod_idx is None or total_idx is None:
            # Try fixed column positions (Date=0, Product=2, Total=6)
            if len(headers) >= 7:
                date_idx = 0
                prod_idx = 2
                total_idx = 6
                print('  Using fixed column positions: date=0, prod=2, total=6')
            else:
                print('  Cannot determine columns from headers: ' + str(headers))
                continue

        data_rows = rows[1:]
        print('  Parsing ' + str(len(data_rows)) + ' data rows')
        for row in data_rows:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) <= max(date_idx, prod_idx, total_idx):
                continue
            try:
                date_raw = cells[date_idx]
                product = cells[prod_idx].upper()
                total_str = cells[total_idx].replace('$', '').replace(',', '').strip()
                total = float(total_str)
                d = datetime.datetime.strptime(date_raw, '%m/%d/%Y').date()
                date_str = d.isoformat()
                prod_key = GUILLORY_PRODUCTS.get(product)
                if not prod_key:
                    continue
                if date_str not in result:
                    result[date_str] = {}
                result[date_str][prod_key] = total
            except Exception as e:
                print('  Table row error: ' + str(e) + ' cells=' + str(cells[:4]))
    return result

def fetch_all_guillory():
    session, user_id = guillory_login()
    all_data = {}
    for account_number, store_key in GUILLORY_STORES.items():
        print('  Fetching ' + store_key + ' (' + account_number + ')...')
        try:
            html = fetch_guillory_prices(session, user_id, account_number)
            prices = parse_guillory_html(html)
            print('  Got ' + str(len(prices)) + ' dates for ' + store_key)
            for date_str, prods in prices.items():
                if date_str not in all_data:
                    all_data[date_str] = {}
                all_data[date_str][store_key] = prods
        except Exception as e:
            print('  Error fetching ' + store_key + ': ' + str(e))
    return all_data

def update_guillory_in_prices_json(guillory_data):
    prices_file = 'prices.json'
    if os.path.exists(prices_file):
        with open(prices_file, 'r') as f:
            existing = json.load(f)
    else:
        existing = {}
    added = 0
    for date_str, store_data in guillory_data.items():
        if date_str not in existing:
            existing[date_str] = {}
        for store_key, prods in store_data.items():
            if store_key not in existing[date_str]:
                existing[date_str][store_key] = prods
                added += 1
                print('  Added ' + date_str + ' ' + store_key)
    with open(prices_file, 'w') as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    print('Guillory: ' + str(added) + ' new entries added to prices.json')
    return existing

def build_gd_js(prices_data):
    guillory_keys = set(GUILLORY_STORES.values())
    lines = ['var GD={']
    date_keys = sorted(k for k in prices_data.keys())
    guillory_dates = [dk for dk in date_keys if any(sk in prices_data[dk] for sk in guillory_keys)]
    for i, dk in enumerate(guillory_dates):
        store_parts = []
        for sk in sorted(guillory_keys):
            if sk in prices_data[dk]:
                prods = prices_data[dk][sk]
                prod_parts = ['"' + pk + '":' + str(pv) for pk, pv in sorted(prods.items())]
                store_parts.append('"' + sk + '":{' + ','.join(prod_parts) + '}')
        if store_parts:
            comma = ',' if i < len(guillory_dates) - 1 else ''
            lines.append('"' + dk + '":{' + ','.join(store_parts) + '}' + comma)
    lines.append('};')
    return '\n'.join(lines)

def update_gd_in_index_html(prices_data):
    gd_js = build_gd_js(prices_data)
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r'var GD=\{[\s\S]*?\};', gd_js, html)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('Guillory: Updated var GD= in index.html')

def main():
    print('Starting Guillory Oil price fetch...')
    if not GUILLORY_USERNAME or not GUILLORY_PASSWORD:
        print('Guillory: Missing credentials - skipping')
        return
    guillory_data = fetch_all_guillory()
    if not guillory_data:
        print('No Guillory data fetched - aborting')
        return
    prices_data = update_guillory_in_prices_json(guillory_data)
    update_gd_in_index_html(prices_data)
    print('Guillory: Done!')

if __name__ == '__main__':
    main()
