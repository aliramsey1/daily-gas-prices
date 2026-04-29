import imaplib
import email
import re
import json
import os
import datetime
import io
from email.utils import parsedate_to_datetime
from pdfminer.high_level import extract_text as pdf_extract_text


GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']

CAMPBELL_STORE_KEY = 'pn'


def fetch_campbell_emails():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    print(f"Campbell: Logged in as: {GMAIL_ADDRESS}")

    since_date = (datetime.date.today() - datetime.timedelta(days=14)).strftime('%d-%b-%Y')
    results = []

    # Search INBOX for "Daily Price Update" (confirmed to be there from Gmail web)
    rv, _ = mail.select('INBOX', readonly=True)
    print(f"Campbell: INBOX select: {rv}")

    _, data = mail.search(None, f'(SINCE {since_date} SUBJECT "Daily Price Update")')
    ids = data[0].split() if data[0] else []
    print(f"Campbell: INBOX SUBJECT 'Daily Price Update' => {len(ids)} ids")

    # Print ALL matching email senders/subjects
    for eid in ids:
        try:
            _, hdr_data = mail.fetch(eid, '(BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])')
            hdr = email.message_from_bytes(hdr_data[0][1])
            frm = hdr.get('From', 'NONE')
            subj = hdr.get('Subject', 'NONE')
            dt = hdr.get('Date', '')
            print(f"Campbell: INBOX id={eid} From=[{frm}] Subj=[{subj[:60]}]")
        except Exception as e:
            print(f"Campbell: Error {eid}: {e}")

    # Also search with X-GM-RAW using gmail operator
    try:
        _, data2 = mail.search(None, f'(SINCE {since_date} X-GM-RAW "Campbell oil Daily Price Update")')
        ids2 = data2[0].split() if data2[0] else []
        print(f"Campbell: X-GM-RAW 'Campbell oil Daily Price Update' => {len(ids2)} ids")
        for eid in ids2:
            _, hdr_data = mail.fetch(eid, '(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])')
            hdr = email.message_from_bytes(hdr_data[0][1])
            print(f"  id={eid} From=[{hdr.get('From','')}] Subj=[{hdr.get('Subject','')[:60]}]")
    except Exception as e:
        print(f"Campbell: X-GM-RAW error: {e}")

    # Try to fetch the actual email body for an ID we know
    if ids:
        eid = ids[0]
        print(f"Campbell: Fetching full message for id={eid}")
        _, msg_data = mail.fetch(eid, '(RFC822)')
        msg = email.message_from_bytes(msg_data[0][1])
        frm = msg.get('From', '')
        subj = msg.get('Subject', '')
        print(f"Campbell: FULL MSG From=[{frm}] Subj=[{subj}]")
        # Print all headers
        for key in msg.keys():
            print(f"  Header {key}: {msg[key]}")

    mail.logout()
    print("Campbell: Diagnostic complete")
    return []


def process_campbell_emails():
    fetch_campbell_emails()
    print("Campbell: Diagnostic run - no prices stored")


if __name__ == '__main__':
    print("Starting Campbell Oil diagnostic...")
    process_campbell_emails()
