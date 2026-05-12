import requests
import re
import json
import os
import datetime
import gzip
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

ALL_STORE_KEYS = list(GUILLORY_STORES.values())

def decode_html(response):
        """Decode response to HTML string, handling gzip if needed."""
        try:
                    content = response.content
                    if content[:2] == b'\x1f\x8b':
                                    return gzip.decompress(content).decode('utf-8', errors='replace')
                                return content.decode('utf-8', errors='replace')
except Exception:
        return response.text

def guillory_login():
        """Log in and return session."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

    # Step 1: GET login page for CSRF token
        login_url = GUILLORY_URL + '/account/?login'
        r1 = session.get(login_url)
        html1 = decode_html(r1)
        print(' Step1 GET login: status=' + str(r1.status_code) + ', len=' + str(len(html1)))

    soup1 = BeautifulSoup(html1, 'html.parser')
    csrf_input = soup1.find('input', {'name': 'csrf_token'})
    csrf_value = csrf_input['value'] if csrf_input else ''
    user_app_id_input = soup1.find('input', {'name': 'user_app_id'})
    user_app_id_value = user_app_id_input['value'] if user_app_id_input else '0'
    redirect_uri_input = soup1.find('input', {'name': 'redirect_uri'})
    redirect_uri_value = redirect_uri_input['value'] if redirect_uri_input else '/account/?login'

    print(' CSRF found: ' + str(bool(csrf_value)) + ', csrf_len=' + str(len(csrf_value)))
    print(' user_app_id: ' + str(user_app_id_value))
    print(' redirect_uri: ' + str(redirect_uri_value))
    print(' USERNAME set: ' + str(bool(GUILLORY_USERNAME)) + ', len=' + str(len(GUILLORY_USERNAME)))

    # Step 2: POST login
    session.headers.update({'Referer': login_url, 'Origin': GUILLORY_URL})
    login_payload = {
                'user_app_id': user_app_id_value,
                'account_number': '',
                'csrf_token': csrf_value,
                'redirect_uri': redirect_uri_value,
                'user_name': GUILLORY_USERNAME,
                'user_password': GUILLORY_PASSWORD,
                'user_remember': '1',
    }
    r2 = session.post(GUILLORY_URL + '/account/', data=login_payload, allow_redirects=True)
    html2 = decode_html(r2)
    print(' Step2 POST login: status=' + str(r2.status_code) + ', final_url=' + str(r2.url) + ', len=' + str(len(html2)))

    # Check login success - authenticated page does NOT have user_password in it
    has_user_password = 'user_password' in html2
    has_logout = 'logout' in html2.lower()
    has_account_summary = 'Account Summary' in html2
    print(' has_user_password=' + str(has_user_password) + ', has_logout=' + str(has_logout) + ', has_account_summary=' + str(has_account_summary))

    if has_user_password:
                # Login failed - the page still has the login form
                soup2 = BeautifulSoup(html2, 'html.parser')
                err = soup2.find(class_='alert-danger') or soup2.find(class_='error') or soup2.find(class_='alert')
                err_text = err.get_text(strip=True)[:100] if err else 'no error msg found'
                print(' LOGIN FAILED. Error: ' + err_text)
                return None

    print(' Login SUCCESS')
    return session

def fetch_guillory_price_history(session, days=90):
        """GET price-history page and return HTML response."""
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days)

    # Use GET request - the authenticated session will return the price history
        r = session.get(
                    GUILLORY_URL + '/account/price-history',
                    headers={
                                    'Referer': GUILLORY_URL + '/account/',
                    },
        )
    html = decode_html(r)
    print(' GET price-history: status=' + str(r.status_code) + ', final_url=' + str(r.url) + ', len=' + str(len(html)))

    # Check if we got the login page back
    is_login = 'user_password' in html and 'logout' not in html.lower()
    if is_login:
                print(' Got login page! Session lost.')
                return ''

    return html

def parse_guillory_html(html):
        """Parse price-history HTML into {date_str: {prod_key: price}}."""
        if not html:
                    return {}

        # Primary: look for inline DataTables data in script tag
        # New site format: 'data': [["date","time","loc_num","loc_name","prod_code","product","base","surcharge","taxes","total","change"],...]
    for pattern in [r"'data':\s*(\[\[.*?\]\])", r'"data":\s*(\[\[.*?\]\])']:
                m = re.search(pattern, html, re.DOTALL)
                if m:
                                try:
                                                    data = json.loads(m.group(1))
                                                    result = _rows_to_result(data)
                                                    if result:
                                                                            print(' Parsed via JS data: ' + str(len(result)) + ' dates, ' + str(sum(len(v) for v in result.values())) + ' products')
                                                                            return result
                                except Exception as e:
                                                    print(' JS data parse error: ' + str(e))

                        # Fallback: HTML table parser
                        soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    if tables:
                result = _tables_to_result(tables)
        if result:
                        return result

    print(' No data found. HTML length=' + str(len(html)))
    print(' Snippet: ' + html[:200].encode('ascii', errors='replace').decode())
    return {}

def _rows_to_result(data):
        # New site row format: [date, time, loc_num, loc_name, prod_code, product_name, base, surcharge, taxes, total, change]
        # Old site row format: [date, time, loc_num, loc_name, supplier?, product, base, surcharge, taxes, total, change]
        # Both have product at index 5 and total at index 9
        result = {}
    skipped = 0
    for row in data:
                try:
                                if len(row) < 10:
                                                    skipped += 1
                                                    continue
                                                d = datetime.datetime.strptime(row[0].replace('\\/', '/'), '%m/%d/%Y').date()
            prod_key = GUILLORY_PRODUCTS.get(row[5])
            if not prod_key:
                                # Try index 4 for older format
                                prod_key = GUILLORY_PRODUCTS.get(row[4])
            if not prod_key:
                                skipped += 1
                continue
            date_str = d.isoformat()
            if date_str not in result:
                                result[date_str] = {}
            result[date_str][prod_key] = float(row[9])
except Exception:
            skipped += 1
    if skipped:
                print(' Skipped ' + str(skipped) + ' rows (product not in map or parse error)')
    return result

def _tables_to_result(tables):
        result = {}
    for table in tables:
                rows = table.find_all('tr')
        if len(rows) < 2:
                        continue
        headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(['th', 'td'])]
        date_idx = next((i for i, h in enumerate(headers) if 'date' in h or 'eff' in h), 0)
        prod_idx = next((i for i, h in enumerate(headers) if 'product' in h), 2)
        total_idx = next((i for i, h in enumerate(headers) if 'total' in h), 6)
        if len(headers) < 4:
                        continue
        print(' Table: ' + str(len(rows)-1) + ' rows, headers=' + str(headers[:6]))
        parsed = 0
        for row in rows[1:]:
                        cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) <= max(date_idx, prod_idx, total_idx):
                                continue
            try:
                                d = datetime.datetime.strptime(cells[date_idx], '%m/%d/%Y').date()
                prod_key = GUILLORY_PRODUCTS.get(cells[prod_idx].upper())
                if not prod_key:
                                        continue
                                    total = float(cells[total_idx].replace('$', '').replace(',', '').strip())
                date_str = d.isoformat()
                if date_str not in result:
                                        result[date_str] = {}
                                    result[date_str][prod_key] = total
                parsed += 1
except Exception:
                pass
        if parsed > 0:
                        print(' Parsed ' + str(parsed) + ' records, ' + str(len(result)) + ' dates')
    return result

def fetch_all_guillory():
        session = guillory_login()
    if not session:
                print(' Login failed - aborting')
        return {}

    print(' Fetching price history...')
    html = fetch_guillory_price_history(session)
    prices = parse_guillory_html(html)
    print(' Got ' + str(len(prices)) + ' dates')

    if not prices:
                return {}

    # Apply the prices to all store keys (new site shows one price for all stores)
    all_data = {}
    for date_str, prods in prices.items():
                if date_str not in all_data:
                                all_data[date_str] = {}
        for store_key in ALL_STORE_KEYS:
                        all_data[date_str][store_key] = prods

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
                                            print(' Added ' + date_str + ' ' + store_key)

    with open(prices_file, 'w') as f:
                json.dump(existing, f, indent=2, sort_keys=True)
    print('Guillory: ' + str(added) + ' new entries added')
    return existing

def build_gd_js(prices_data):
        guillory_keys = set(GUILLORY_STORES.values())
    lines = ['var GD={']
    guillory_dates = sorted(dk for dk in prices_data if any(sk in prices_data[dk] for sk in guillory_keys))
    for i, dk in enumerate(guillory_dates):
                parts = []
        for sk in sorted(guillory_keys):
                        if sk in prices_data[dk]:
                                            pp = ['"' + k + '":' + str(v) for k, v in sorted(prices_data[dk][sk].items())]
                                            parts.append('"' + sk + '":{' + ','.join(pp) + '}')
                                    if parts:
                                                    comma = ',' if i < len(guillory_dates) - 1 else ''
                                                    lines.append('"' + dk + '":{' + ','.join(parts) + '}' + comma)
                                            lines.append('};')
    return '\n'.join(lines)

def update_gd_in_index_html(prices_data):
        gd_js = build_gd_js(prices_data)
    with open('index.html', 'r', encoding='utf-8') as f:
                html = f.read()
    html = re.sub(r'var GD=\{[\s\S]*?\};', gd_js, html)
    with open('index.html', 'w', encoding='utf-8') as f:
                f.write(html)
    print('Updated var GD= in index.html')

def main():
        print('Starting Guillory Oil price fetch...')
    if not GUILLORY_USERNAME or not GUILLORY_PASSWORD:
                print('Missing credentials - skipping')
        return
    guillory_data = fetch_all_guillory()
    if not guillory_data:
                print('No Guillory data fetched - aborting')
        return
    prices_data = update_guillory_in_prices_json(guillory_data)
    update_gd_in_index_html(prices_data)
    print('Done!')

if __name__ == '__main__':
        main()
