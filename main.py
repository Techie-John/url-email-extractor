import os
import imapclient
import smtplib
from email.message import EmailMessage
from email.header import decode_header
from email import message_from_bytes # Specific parser for received emails
import re
import ssl # For secure SMTP connection

# --- Configuration (Get from GitHub Secrets, with Gmail defaults) ---
# IMPORTANT: These are placeholders. You MUST set these as GitHub Secrets.
# For GMAIL_USER and GMAIL_PASS, ensure you use an App Password if 2FA is enabled.
GMAIL_USER = os.getenv("EMAIL_USER") # Your Gmail address (e.g., your_service_email@gmail.com)
GMAIL_PASS = os.getenv("EMAIL_PASS") # Your Gmail App Password

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = GMAIL_USER # The email address your service sends from

# --- Regular Expressions for Extraction ---
# URL Regex: Catches http/https and www. prefixed URLs.
# This is a robust-enough regex for most common URLs in plain text.
URL_REGEX = r"(?:https?://|www\.)(?:[a-zA-Z0-9.\-]+(?:\.[a-zA-Z]{2,})?|localhost)(?::\d{1,5})?(?:/[^\s()]*)?" \
            r"(?:\([^\s()]*\))*" # Added to allow parentheses in URLs like Wikipedia

# Email Regex: Catches standard email formats.
EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

# --- Email Connection Functions ---
def connect_to_imap():
    """Establishes a secure IMAP connection to Gmail."""
    try:
        server = imapclient.IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True)
        server.login(GMAIL_USER, GMAIL_PASS)
        print(f"Successfully connected to IMAP server {IMAP_SERVER}")
        return server
    except Exception as e:
        print(f"Error connecting to IMAP: {e}")
        raise # Re-raise the exception to be caught by the main processing try-except

def connect_to_smtp():
    """Establishes a secure SMTP connection to Gmail."""
    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls(context=context) # Use TLS for secure connection
        server.login(GMAIL_USER, GMAIL_PASS)
        print(f"Successfully connected to SMTP server {SMTP_SERVER}")
        return server
    except Exception as e:
        print(f"Error connecting to SMTP: {e}")
        raise # Re-raise the exception

def get_email_body(msg):
    """Extracts plain text body from an email. Handles multipart emails."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get('Content-Disposition'))
            # Look for plain text body, ignoring attachments and HTML parts
            if ctype == 'text/plain' and 'attachment' not in cdispo:
                try:
                    # Decode payload using its specified charset or utf-8/latin-1 fallback
                    charset = part.get_content_charset()
                    return part.get_payload(decode=True).decode(charset if charset else 'utf-8', errors='ignore')
                except (UnicodeDecodeError, LookupError):
                    return part.get_payload(decode=True).decode('latin-1', errors='ignore') # Fallback
    else:
        try:
            charset = msg.get_content_charset()
            return msg.get_payload(decode=True).decode(charset if charset else 'utf-8', errors='ignore')
        except (UnicodeDecodeError, LookupError):
            return msg.get_payload(decode=True).decode('latin-1', errors='ignore')
    return ""

def send_email(to_email, subject, body):
    """Sends an email using the configured SMTP server."""
    smtp_server = None
    try:
        smtp_server = connect_to_smtp()
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        smtp_server.send_message(msg)
        print(f"Sent email to {to_email} with subject '{subject}'")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        # Consider logging this failure more prominently or notifying an admin
    finally:
        if smtp_server:
            smtp_server.quit()

# --- Extraction Logic ---
def extract_urls(text):
    """Finds and returns unique URLs from a given text."""
    found_urls = re.findall(URL_REGEX, text)
    # Filter out potential false positives or malformed matches (e.g., very short matches)
    cleaned_urls = [url for url in found_urls if len(url) > 5 and '.' in url] # Basic sanity check
    return sorted(list(set(cleaned_urls))) # Remove duplicates and sort alphabetically

def extract_emails(text):
    """Finds and returns unique email addresses from a given text."""
    found_emails = re.findall(EMAIL_REGEX, text)
    return sorted(list(set(found_emails))) # Remove duplicates and sort alphabetically

# --- Main Processing Logic ---
def process_emails():
    """Connects to IMAP, processes unread emails, and sends replies."""
    imap_server = None # Initialize to None for finally block
    try:
        imap_server = connect_to_imap()
        imap_server.select_folder('INBOX')

        # --- DEBUG PRINT 1 ---
        print("Searching for UNSEEN emails...")
        messages = imap_server.search('UNSEEN') # Look for unread emails

        if not messages:
            print("No new unread emails found. Exiting.")
            return

        print(f"Found {len(messages)} unread email(s). Processing...")

        for uid, message_data in imap_server.fetch(messages, ['RFC822']).items():
            raw_email = message_data[b'RFC822']
            msg = message_from_bytes(raw_email) # Parse the raw email content

            # Extract sender's email address
            sender_email_raw = msg['From']
            match = re.search(r'<(.*?)>', sender_email_raw)
            sender_email = match.group(1) if match else sender_email_raw
            print(f"\n--- Processing Email UID: {uid} from: {sender_email} ---") # DEBUG PRINT 2

            # Extract subject
            subject = ""
            try:
                subject_header = decode_header(msg['Subject'])
                decoded_parts = []
                for s, charset in subject_header:
                    if isinstance(s, bytes):
                        # Decode based on charset, or utf-8/latin-1 fallback, ignore errors for robustness
                        decoded_parts.append(s.decode(charset if charset else 'utf-8', errors='ignore'))
                    else:
                        decoded_parts.append(s)
                subject = "".join(decoded_parts)
            except Exception as e:
                print(f"Could not decode subject for UID {uid}: {e}") # DEBUG PRINT
                pass # Subject might be empty or malformed

            body = get_email_body(msg)

            # Determine the text to process (prioritize body, then subject)
            text_to_process = body.strip()
            if not text_to_process and subject.strip(): # If body is empty, use subject
                 text_to_process = subject.strip()
                 print(f"UID {uid}: No body text found, processing subject.") # DEBUG PRINT
            elif text_to_process: # If body has text
                print(f"UID {uid}: Processing email body.") # DEBUG PRINT
            else: # If both are empty
                print(f"UID {uid}: No text found in email body or subject.") # DEBUG PRINT

            # --- DEBUG PRINT 3 ---
            print(f"UID {uid}: Text to process (first 200 chars): '{text_to_process[:200]}'...")
            print(f"UID {uid}: Full text length: {len(text_to_process)}")


            if text_to_process:
                extracted_urls = extract_urls(text_to_process)
                extracted_emails = extract_emails(text_to_process)

                # --- DEBUG PRINT 4 ---
                print(f"UID {uid}: Extracted URLs: {extracted_urls}")
                print(f"UID {uid}: Extracted Emails: {extracted_emails}")

                # Prepare the response email body
                response_parts = []
                response_parts.append("Hello from your Extractor Bot!")
                response_parts.append("\nHere are the extracted items