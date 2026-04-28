import requests
import re
import json
import os
import datetime
from bs4 import BeautifulSoup

GUILLORY_URL = 'https://www.guilloryoil.net'
GUILLORY_USERNAME = os.environ['GUILLORY_USERNAME']
GUILLORY_PASSWORD = os.environ['GUILLORY_PASSWORD']

# Store account numbers and their calendar keys
GUILLORY_STORES = {
    '000002080': 'gw',   # West Congress Chevron (ESHAAN INTERNATIONAL INC)
    '000002555': 'ge',   # Eunice Corner Express (SUNRAYS INTERNATIONAL LLC)
    '000005310': 'gr',   # Rice City Chevron (SILVER OVERSEAS INC)
    '000005315': 'gc',   # Crowley Corner Express (CROWLEY EXPRESS / SILVER STORE HOLDING)
    '000004644': 'gh',   # Henderson Complete Stop (MARKS STORE LLC)
    '000005295': 'gb',   # Bagley Express Mart (SILVER BARN STORES LLC)
    # 000000425 = MNR HOLDING (Johnston Corner Express) - excluded, no price history
}

# Product column index 5 -> key mapping
GUILLORY_PRODUCTS = {
    'REGULAR E10': 'reg',
    'REGULAR': 'reg_pure',
    'PLUS E10': 'mid',
    'SUPER E10': 'sup',
    'ULTRA L/S CLEAR DIESEL FUEL': 'die',
}

def guillory_login():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': GUILLORY_URL + '/account/?login',
    })
    # GET login page to grab CSRF token
    r = session.get(GUILLORY_URL + '/account/?login')
    soup = BeautifulSoup(r.text, 'html.parser')
    csrf = soup.find('input', {'name': 'csrf_token'})
    csrf_value = csrf['value'] if csrf else ''
    user_app_id = soup.find('input', {'name': 'user_app_id'})
    user_app_id_value = user_app_id['value'] if user_app_id else '0'
    # POST login
    payload = {
        'user_app_id': user_app_id_value,
        'account_number': '',
        'csrf_token': csrf_value,
        'redirect_uri': '',
        'user_name': GUILLORY_USERNAME,
        'user_password': GUILLORY_PASSWORD,
        'user_remember': 'on',
    }
    r2 = session.post(GUILLORY_URL + '/account/', data=payload, allow_redirects=True)
    if 'Price History' in r2.text or 'Account Summary' in r2.text or 'Fuel Price' in r2.text:
        print('Guillory: Logged in successfully')
        return session
    else:
        print(f'Guillory: Login may have failed. Status={r2.status_code}')
        return session

def fetch_guillory_prices(session, account_number, days=90):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days)
    date_start = start.strftime('%m/%d/%Y')
    date_end = today.strftime('%m/%d/%Y')
    payload = {
        'search': '1',
        'results': '9999',
        'user_id': '',
        'account_number': account_number,
        'date_start': date_start,
        'date_end': date_end,
        'location_number': '',
        'supplier_id': '',
        'terminal_id': '',
        'product_id': '',
    }
    r = session.post(GUILLORY_URL + '/account/price-history', data=payload)
    return r.text

def parse_guillory_html(html):
    # Extract the DataTable data array from the HTML
    # Pattern: 'data': [["date","time","loc_id","loc_name","prod_id","product","base","surcharge","taxes","total","change"],...]
    m = re.search(r"'data':s*([[.*?]])", html, re.DOTALL)
    if not m:
        print('  Could not find data array in HTML')
        return {}
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        print(f'  JSON parse error: {e}')
        return {}
    # data columns: [date, time, loc_id, loc_name, prod_id, product, base, surcharge, taxes, total, change]
    # We want: date (col 0), product name (col 5), total price (col 9)
    result = {}
    for row in data:
        date_raw = row[0]   # "4/27/2026"
        product = row[5]    # "REGULAR E10"
        total = float(row[9])
        # Convert date to ISO format
        try:
            d = datetime.datetime.strptime(date_raw, '%m/%d/%Y').date()
            date_str = d.isoformat()
        except Exception:
            continue
        prod_key = GUILLORY_PRODUCTS.get(product)
        if not prod_key:
            continue
        if date_str not in result:
            result[date_str] = {}
        result[date_str][prod_key] = total
    return result

def fetch_all_guillory():
    session = guillory_login()
    all_data = {}
    for account_number, store_key in GUILLORY_STORES.items():
        print(f'  Fetching {store_key} ({account_number})...')
        try:
            html = fetch_guillory_prices(session, account_number)
            prices = parse_guillory_html(html)
            print(f'    Got {len(prices)} dates')
            for date_str, prods in prices.items():
                if date_str not in all_data:
                    all_data[date_str] = {}
                all_data[date_str][store_key] = prods
        except Exception as e:
            print(f'    Error fetching {store_key}: {e}')
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
                print(f'  Added {date_str} {store_key}: {prods}')
    with open(prices_file, 'w') as f:
        json.dump(existing, f, indent=2, sort_keys=True)
    print(f'Guillory: {added} new entries added to prices.json')
    return existing

def build_gd_js(prices_data):
    # Build var GD={...} from prices.json for Guillory store keys only
    guillory_keys = set(GUILLORY_STORES.values())
    lines = []
    lines.append('var GD={')
    date_keys = sorted(k for k in prices_data.keys())
    # Only include dates that have at least one Guillory store
    guillory_dates = [dk for dk in date_keys if any(sk in prices_data[dk] for sk in guillory_keys)]
    for i, dk in enumerate(guillory_dates):
        store_parts = []
        for sk in sorted(guillory_keys):
            if sk in prices_data[dk]:
                prods = prices_data[dk][sk]
                prod_parts = [f'"{pk}":{pv}' for pk, pv in sorted(prods.items())]
                store_parts.append(f'"{sk}":{{{",".join(prod_parts)}}}')
        if store_parts:
            comma = ',' if i < len(guillory_dates) - 1 else ''
            lines.append(f'"{dk}":{{{",".join(store_parts)}}}{comma}')
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
    guillory_data = fetch_all_guillory()
    if not guillory_data:
        print('No Guillory data fetched - aborting')
        return
    prices_data = update_guillory_in_prices_json(guillory_data)
    update_gd_in_index_html(prices_data)
    print('Guillory: Done!')

if __name__ == '__main__':
    main()
