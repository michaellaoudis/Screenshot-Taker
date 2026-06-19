#!/usr/bin/env python3
"""
Web App Screenshot & File Downloader
Built by Michael Laoudis

This tool looks through a list of URLs, takes full screenshots of web pages, and
downloads target file types (PDF, XLSX, ZIP, CSV). It also supports authenticated
sessions using Basic Auth and exported browser cookies.

Usage:
    python3 screenshotter.py urls.txt [options]

Examples:
    # Basic run — screenshots saved to a timestamped folder
    python3 screenshotter.py urls.txt

    # Custom output folder, 3-second page delay, full-page screenshots
    python3 screenshotter.py urls.txt -o recon_screenshots --delay 3 --full-page

    # Authenticated session using exported browser cookies
    python3 screenshotter.py urls.txt --cookies cookies.txt

    # Basic Auth credentials for an internal app
    python3 screenshotter.py urls.txt --auth admin:password123

    # Wider viewport and skip any URL containing 'logout'
    python3 screenshotter.py urls.txt --width 2560 --height 1440 --ignore-urls ignore.txt

    # Write a CSV log of every action taken
    python3 screenshotter.py urls.txt --log results.csv
"""

import os
import csv
import sys
import time
import argparse
import fnmatch
from datetime import datetime
from urllib.parse import urlparse

import urllib3
# Suppress SSL warnings that fire when verify=False is used against internal
# apps with self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import requests
except ImportError:
    sys.exit("[!] 'requests' not found. Run: pip3 install requests")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    sys.exit("[!] 'selenium' not found. Run: pip3 install selenium")

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    sys.exit("[!] 'webdriver_manager' not found. Run: pip3 install webdriver-manager")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False   # Full-page is optional; warn if --full-page is used without it

# Constants

# File extensions that should be downloaded rather than screenshotted.
DOWNLOAD_EXTENSIONS = ('.pdf', '.xlsx', '.zip', '.csv', '.docx', '.xls', '.txt', '.json', '.xml')

# ANSI color codes for terminal output
RESET  = "\033[0m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"

def c(colour, text):
    return f"{colour}{text}{RESET}"


# Argument parsing

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Web app screenshot and file downloader (authorized use only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Required
    parser.add_argument(
        "input_file",
        help="Text file containing URLs to process, one per line. Blank lines and lines starting with # are ignored."
    )

    # Output Structure
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output folder name. Defaults to a timestamp (e.g. 2025-06-01_14-30-00)."
    )
    parser.add_argument(
        "--log",
        default=None,
        metavar="FILE",
        help="Write a CSV log of every action (URL, status, output file, timestamp) to FILE."
    )

    # Browser views
    parser.add_argument(
        "--width",
        type=int,
        default=1920,
        help="Browser viewport width in pixels. Default: 1920."
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1080,
        help="Browser viewport height in pixels. Default: 1080."
    )
    parser.add_argument(
        "--full-page",
        action="store_true",
        help=(
            "Capture the entire page height, not just the visible viewport. "
            "Requires Pillow: pip3 install Pillow. "
            "Works by temporarily expanding the browser window to match the page's scroll height."
        )
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help=(
            "Seconds to wait after a page loads before taking the screenshot. "
            "Increase this for JS-heavy apps that render content after the initial load. Default: 2."
        )
    )

    # Authentication
    parser.add_argument(
        "--auth",
        default=None,
        metavar="USER:PASS",
        help=(
            "HTTP Basic Auth credentials in user:password format. "
            "Applied to both the Selenium browser session and file downloads. "
            "Example: --auth admin:secret123"
        )
    )
    parser.add_argument(
        "--cookies",
        default=None,
        metavar="FILE",
        help=(
            "Path to a Netscape-format cookies file (exported from your browser via an extension "
            "such as 'Cookie-Editor' or 'EditThisCookie'). Injected into both Selenium and "
            "requests sessions so authenticated pages are captured correctly."
        )
    )

    # Filtering
    parser.add_argument(
        "--ignore-urls",
        default=None,
        metavar="FILE",
        help=(
            "Text file of URL patterns to skip, one per line. "
            "Supports wildcards (e.g. '*/logout*', '*/admin/delete*'). "
            "Useful for avoiding destructive or session-ending endpoints."
        )
    )

    return parser.parse_args()


# File / URL helpers

def read_urls(file_path):
    if not os.path.isfile(file_path):
        sys.exit(c(RED, f"[!] Input file not found: {file_path}"))
    with open(file_path, 'r') as f:
        return [
            line.strip() for line in f
            if line.strip() and not line.strip().startswith('#')
        ]


def read_ignore_patterns(file_path):
    if not file_path:
        return []
    if not os.path.isfile(file_path):
        sys.exit(c(RED, f"[!] Ignore-URLs file not found: {file_path}"))
    with open(file_path, 'r') as f:
        return [
            line.strip() for line in f
            if line.strip() and not line.strip().startswith('#')
        ]


def is_ignored(url, patterns):
    for pattern in patterns:
        if fnmatch.fnmatch(url, pattern):
            return True
    return False


def create_output_folder(name=None):
    if not name:
        name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    os.makedirs(name, exist_ok=True)
    return name


def sanitize_filename(url, ext):
    parsed = urlparse(url)
    domain = parsed.netloc.replace('.', '_')
    path   = parsed.path.replace('/', '_').strip('_')

    # Include query string in the filename so two URLs that differ only by
    # query parameters get distinct filenames (e.g. ?id=1 vs ?id=2)
    query = parsed.query.replace('&', '_').replace('=', '-') if parsed.query else ''

    if not path:
        path = 'home'

    # Build base name from domain + path + optional query string
    base = f"{domain}_{path}{'_' + query if query else ''}"

    # Avoid double extension if the path already ends in the target extension
    if base.lower().endswith(f".{ext.lower()}"):
        return base
    return f"{base}.{ext}"

# Cookie setup

def load_netscape_cookies(file_path):
    cookies = []
    if not file_path:
        return cookies
    if not os.path.isfile(file_path):
        sys.exit(c(RED, f"[!] Cookies file not found: {file_path}"))

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip comments and the Netscape header line
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < 7:
                continue   # Malformed line — skip instead of crashing
            domain, flag, path, secure, expiry, name, value = parts[:7]
            cookies.append({
                "domain": domain,
                "path":   path,
                "secure": secure.upper() == "TRUE",
                "name":   name,
                "value":  value,
            })

    print(c(CYAN, f"[*] Loaded {len(cookies)} cookies from {file_path}"))
    return cookies

# Browser initialization

def init_browser(args, cookies):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")        # Helps prevent crashes in low-memory environments
    options.add_argument("--ignore-certificate-errors")    # Accept self-signed and expired SSL certs
    options.add_argument("--disable-web-security")         # Disable same-origin policy (SOP) for internal tools
    options.add_argument(f"--window-size={args.width},{args.height}")

    # If Basic Auth credentials were supplied, pass them in the URL
    # scheme (handled in screenshot_url).
    options.add_experimental_option("prefs", {
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    })
    # Suppress ChromeDriver version mismatch output in the terminal
    options.add_experimental_option('excludeSwitches', ['enable-logging'])

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    # Insert cookies into the browser session if provided.
    if cookies:
        # Extract domain from cookies to know where to navigate first
        first_domain = cookies[0].get("domain", "").lstrip(".")
        try:
            driver.get(f"https://{first_domain}")
            time.sleep(1)
            for cookie in cookies:
                try:
                    driver.add_cookie(cookie)
                except Exception as e:
                    # Some cookies may be rejected (wrong domain, expired..)
                    print(c(YELLOW, f"    [~] Skipping cookie '{cookie.get('name')}': {e}"))
            print(c(CYAN, f"[*] Injected cookies into browser session."))
        except Exception as e:
            print(c(YELLOW, f"[!] Could not pre-load cookies into browser: {e}"))

    return driver

# requests session setup

def init_requests_session(args, cookies):
    session = requests.Session()
    session.verify = False   # Accept self-signed SSL certs (same as browser)

    # Use Basic Auth if provided
    if args.auth:
        try:
            user, password = args.auth.split(':', 1)  # split on first ':' only
            session.auth = (user, password)
            print(c(CYAN, f"[*] Basic Auth configured for user: {user}"))
        except ValueError:
            sys.exit(c(RED, "[!] --auth must be in USER:PASS format (e.g. admin:secret)."))

    # Load cookies into the requests session so file downloads use the same
    # authenticated session as the Selenium screenshots.
    for cookie in cookies:
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))

    return session

# Screenshot logic

def take_screenshot(driver, url, output_path, args):
    """
    Navigate to a URL and save a screenshot.

    If --full-page is set, the browser is temporarily expand to the
    full height of the page before capturing, then is restored to the original
    size. 
    
    If --auth credentials were provided and the URL scheme is http/https, we
    embed them directly in the URL (http://user:pass@host/path).
    """
    try:
        # Embed Basic Auth in the URL if credentials were supplied.
        nav_url = url
        if args.auth:
            parsed = urlparse(url)
            user, password = args.auth.split(':', 1)
            nav_url = parsed._replace(netloc=f"{user}:{password}@{parsed.netloc}").geturl()

        driver.get(nav_url)

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(("tag name", "body"))
            )
        except Exception:
            pass   # If the wait times out, continue anyway — page may still be usable

        # Additional delay for JS-heavy apps (SPAs, dashboards, etc.) that
        # inject content into the DOM after the initial load event fires.
        time.sleep(args.delay)

        if args.full_page:
            if not PIL_AVAILABLE:
                print(c(YELLOW, "    [~] --full-page requires Pillow. Run: pip3 install Pillow. Falling back to viewport."))
            else:
                # Get the total scroll height of the page
                total_height = driver.execute_script("return document.body.scrollHeight")
                # Expand the window so the entire page is visible
                driver.set_window_size(args.width, total_height)
                time.sleep(0.5)   # Let the browser re-render at new size

        driver.save_screenshot(output_path)

        # Restore original size after a full page capture
        if args.full_page and PIL_AVAILABLE:
            driver.set_window_size(args.width, args.height)

        print(c(GREEN, f"    [+] Screenshot saved: {output_path}"))
        return "ok"

    except Exception as e:
        print(c(RED, f"    [-] Failed to screenshot {url}: {e}"))
        return f"error: {e}"


# File download

def download_file(session, url, output_path):
    
    try:
        print(c(CYAN, f"    [*] Downloading: {url}"))
        response = session.get(url, stream=True, timeout=30)
        response.raise_for_status()   # Raise an exception for 4xx / 5xx responses

        content_type = response.headers.get('Content-Type', '').lower()
      
        if 'html' in content_type:
            print(c(YELLOW, f"    [!] Server returned HTML instead of a file — likely a login redirect. URL: {url}"))
            return "html_redirect"

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        size_kb = os.path.getsize(output_path) // 1024
        print(c(GREEN, f"    [+] Downloaded ({size_kb} KB): {output_path}"))
        return "ok"

    except requests.exceptions.HTTPError as e:
        print(c(RED, f"    [-] HTTP error downloading {url}: {e}"))
        return f"http_error: {e}"
    except Exception as e:
        print(c(RED, f"    [-] Failed to download {url}: {e}"))
        return f"error: {e}"

# CSV log

def init_log(log_path):
    if not log_path:
        return None, None
    log_file = open(log_path, 'w', newline='')
    writer = csv.writer(log_file)
    writer.writerow(["timestamp", "url", "action", "status", "output_file"])
    return log_file, writer


def log_row(writer, url, action, status, output_file):
    if writer:
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            url,
            action,
            status,
            output_file,
        ])

def main():
    args = parse_arguments()

    # Load inputs
    urls            = read_urls(args.input_file)
    ignore_patterns = read_ignore_patterns(args.ignore_urls)
    cookies         = load_netscape_cookies(args.cookies)

    if not urls:
        sys.exit(c(RED, "[!] No URLs found in the input file."))

    # Set up output folder and optional CSV log
    output_folder = create_output_folder(args.output)
    log_file, log_writer = init_log(args.log)

    print(c(BOLD, f"\n[*] Output folder : {os.path.abspath(output_folder)}"))
    print(c(CYAN, f"[*] URLs to process: {len(urls)}"))
    if ignore_patterns:
        print(c(CYAN, f"[*] Ignore patterns: {len(ignore_patterns)}"))
    if args.full_page and not PIL_AVAILABLE:
        print(c(YELLOW, "[!] Pillow not installed — --full-page will fall back to viewport. Run: pip3 install Pillow"))
    print()

    # Initialize browser and download session
    driver  = init_browser(args, cookies)
    session = init_requests_session(args, cookies)

    # Counters for end-of-run summary
    count_ok       = 0
    count_skipped  = 0
    count_error    = 0

    # Process each URL
    for i, url in enumerate(urls, start=1):
        print(c(BOLD, f"[{i}/{len(urls)}] {url}"))

        # Check against ignore patterns before doing anything else
        if is_ignored(url, ignore_patterns):
            print(c(YELLOW, "    [~] Skipped (matches ignore pattern)."))
            log_row(log_writer, url, "skip", "ignored", "")
            count_skipped += 1
            continue

        url_lower = url.lower()

        # Decide whether to download or screenshot based on the URL extension
        if url_lower.endswith(DOWNLOAD_EXTENSIONS):
            ext      = url_lower.rsplit('.', 1)[-1]
            filename = sanitize_filename(url, ext)
            out_path = os.path.join(output_folder, filename)
            status   = download_file(session, url, out_path)
            log_row(log_writer, url, "download", status, out_path)
        else:
            filename = sanitize_filename(url, 'png')
            out_path = os.path.join(output_folder, filename)
            status   = take_screenshot(driver, url, out_path, args)
            log_row(log_writer, url, "screenshot", status, out_path)

        # Track pass/fail counts
        if status == "ok":
            count_ok += 1
        else:
            count_error += 1

    # Clean up
    driver.quit()
    if log_file:
        log_file.close()
        print(c(CYAN, f"\n[*] Log written to: {os.path.abspath(args.log)}"))

    # Print Summary
    print(c(BOLD, f"""
{'='*50}
  Run complete.
  Success  : {count_ok}
  Skipped  : {count_skipped}
  Errors   : {count_error}
  Output   : {os.path.abspath(output_folder)}
{'='*50}
"""))

if __name__ == "__main__":
    main()
