import json
import re

def build_js_data(prices_data):
    date_keys = sorted(prices_data.keys())
    lines = ['var D={']
    for i, dk in enumerate(date_keys):
        stores = prices_data[dk]
        store_parts = []
        for sc, prods in stores.items():
            prod_parts = ['"{}":{}' .format(pk, pv) for pk, pv in prods.items()]
            store_parts.append('"{}":{{{}}}' .format(sc, ','.join(prod_parts)))
        comma = ',' if i < len(date_keys) - 1 else ''
        lines.append('"{}":{{{}}}{}' .format(dk, ','.join(store_parts), comma))
    lines.append('};')
    return '\n'.join(lines)

def update_index_html(prices_data):
    js_data = build_js_data(prices_data)
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r'var D=\{[\s\S]*?\};', js_data, html)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('Rebuilt index.html with all price data including Pop N Go')

if __name__ == '__main__':
    with open('prices.json', 'r') as f:
        prices_data = json.load(f)
    update_index_html(prices_data)
    print('Done! Total dates: {}'.format(len(prices_data)))
