# Screenshot-Taker

### Disclaimer

This tool is provided for educational and authorized security testing purposes only. The author, Michael Laoudis, accepts no responsibility for misuse. Always obtain written permission before testing any target.

<hr>

This tool looks through a list of URLs, takes full screenshots of web pages, and
downloads target file types (PDF, XLSX, ZIP, CSV). It also supports authenticated
sessions using Basic Auth and exported browser cookies.

### Dependencies:

    pip3 install -r requirements.txt

### Usage:

    python3 screenshotter.py urls.txt [options]

### Examples:

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
