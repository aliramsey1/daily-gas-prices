#!/usr/bin/env python3
"""
fetch_prices.py - Fetches Evans Oil daily price emails from Gmail
and updates the price data in index.html

Required GitHub Secrets:
  GMAIL_CREDENTIALS - Base64-encoded Google OAuth2 credentials.json
  GMAIL_TOKEN       - Base64-encoded Gmail OAuth2 token.json

Setup instructions in README.md
"""

import os
import re
import json
import base64
import pickle
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    """Authenticate with Gmail API using stored credentials."""
    creds = None
    
    # Load credentials from environment (base64-encoded JSON)
    creds_b64 = os.environ.get('GMAIL_CREDENTIALS')
    token_b64 = os.environ.get('GMAIL_TOKEN')
    
    if token_b64:
        token_data = json.loads(base64.b64decode(token_b64).decode())
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("Gmail credentials not valid. Please re-authorize.")
    
    return build('gmail', 'v1', credentials=creds)

def search_evans_oil_emails(service, days_back=3):
    """Search for Evans Oil price emails from the last N days."""
    after_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y/%m/%d')
    query = f'from:evans.no.reply@gmail.com subject:"Latest prices from Evans Oil Company" after:{after_date}'
    
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    return messages

def parse_email_body(service, msg_id):
    """Get email body and parse gas prices."""
    msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    
    # Get email date
    headers = msg['payload']['headers']
    date_str = next((h['value'] for h in headers if h['name'] == 'Date'), None)
    
    # Parse date
    from email.utils import parsedate_to_datetime
    try:
        email_date = parsedate_to_datetime(date_str)
        date_key = email_date.strftime('%Y-%m-%d')
    except:
        date_key = datetime.now().strftime('%Y-%m-%d')
    
    # Get body text
    body = ''
    if 'parts' in msg['payload']:
        for part in msg['payload']['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                break
    else:
        data = msg['payload']['body'].get('data', '')
        body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    
    return date_key, body

def extract_prices(body):
    """Extract gas prices from email body text.
    
    Email format:
    Store Name
    Product Name, Unit Price, Tax, Freight, Total
    """
    stores = {
        'Acadian Express': 'ae',
        'Acadiana Mart': 'am', 
        'Moss Bluff Chevron': 'mb',
        'Moss Bluff': 'mb'
    }
    
    products = {
        'E10 Regular Unleaded': 'reg',
        'E10 Super Unleaded': 'sup',
        'Highway Ultra Low Sulfur Diesel': 'die',
        'Highway Ultra Low Sulfur': 'die'
    }
    
    prices = {}
    current_store = None
    
    for line in body.split('\n'):
        line = line.strip()
        
        # Check if line is a store name
        for store_name, store_key in stores.items():
            if store_name.lower() in line.lower():
                current_store = store_key
                if current_store not in prices:
                    prices[current_store] = {}
                break
        
        # Check if line contains price data
        if current_store and ',' in line:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                # Try to identify product and extract total price
                line_lower = line.lower()
                for prod_name, prod_key in products.items():
                    if prod_name.lower() in line_lower:
                        try:
                            # Last number is the total price
                            total = float(parts[-1])
                            prices[current_store][prod_key] = total
                        except ValueError:
                            pass
                        break
    
    return prices if all(len(v) == 3 for v in prices.values() if v) else prices

def update_index_html(date_prices):
    """Update the priceData object in index.html with new prices."""
    with open('index.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the priceData variable
    pattern = r'(var D=\{)(.*?)(\};)'
    match = re.search(pattern, content, re.DOTALL)
    
    if not match:
        print("Could not find priceData in index.html")
        return False
    
    # Parse existing data
    existing_json_str = '{' + match.group(2) + '}'
    try:
        existing_data = json.loads(existing_json_str)
    except:
        existing_data = {}
    
    # Merge new prices
    for date_key, store_prices in date_prices.items():
        if store_prices:
            existing_data[date_key] = store_prices
            print(f"Updated prices for {date_key}: {store_prices}")
    
    # Sort by date
    sorted_data = dict(sorted(existing_data.items()))
    
    # Serialize back (compact format)
    new_json = json.dumps(sorted_data, separators=(',', ':'))
    new_data_str = new_json[1:-1]  # Remove outer braces
    
    # Replace in content
    new_content = content[:match.start()] + 'var D={' + new_data_str + '};' + content[match.end():]
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return True

def main():
    print("Fetching Evans Oil price emails...")
    
    service = get_gmail_service()
    messages = search_evans_oil_emails(service, days_back=5)
    
    if not messages:
        print("No new Evans Oil emails found.")
        return
    
    print(f"Found {len(messages)} emails to process.")
    
    date_prices = {}
    
    for msg in messages:
        date_key, body = parse_email_body(service, msg['id'])
        prices = extract_prices(body)
        
        if prices:
            # If date already seen, merge (multiple emails = multiple stores)
            if date_key in date_prices:
                for store_key, store_prices in prices.items():
                    if store_prices:
                        date_prices[date_key][store_key] = store_prices
            else:
                date_prices[date_key] = prices
            print(f"Parsed prices for {date_key}")
        else:
            print(f"No prices found in email for {date_key}")
    
    if date_prices:
        success = update_index_html(date_prices)
        if success:
            print("Successfully updated index.html with new prices!")
        else:
            print("Failed to update index.html")
    else:
        print("No valid price data extracted.")

if __name__ == '__main__':
    main()
