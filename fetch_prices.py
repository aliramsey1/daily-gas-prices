import imaplib
import email
import re
import json
import os
import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import io

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']


def fetch_lavigne_emails_debug():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select('"[Gmail]/All Mail"', readonly=True)
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%d-%b-%Y')
    _, data = mail.search(None, f'(SINCE {cutoff} SUBJECT "LAVIGNE OIL PRICE NOTIFICATIONS")')
    ids = data[0].split()
    print(f"Found {len(ids)} Lavigne emails")
    for eid in ids[:3]:  # Only check first 3
        _, msg_data = mail.fetch(eid, '(RFC822)')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        print(f"--- EMAIL ---")
        print(f"Subject: {msg['Subject']}")
        print(f"Date: {msg['Date']}")
        print(f"From: {msg['From']}")
        # List attachments
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            fn = part.get_filename()
            print(f"  Part: {ct}, disposition: {cd[:30]}, filename: {fn}")
            if fn and fn.lower().endswith('.pdf'):
                pdf_data = part.get_payload(decode=True)
                print(f"  PDF size: {len(pdf_data)} bytes")
                # Try to extract text with pdfminer
                try:
                    from pdfminer.high_level import extract_text
                    text = extract_text(io.BytesIO(pdf_data))
                    print(f"  PDF TEXT (first 2000 chars):")
                    print(text[:2000])
                except ImportError:
                    print("  pdfminer not available")
                except Exception as e:
                    print(f"  PDF extract error: {e}")
    mail.logout()


if __name__ == '__main__':
    fetch_lavigne_emails_debug()
