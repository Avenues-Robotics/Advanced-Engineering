#!/bin/bash
# Double-click this file in Finder to rebuild and deploy the site.
cd "$(dirname "$0")"
python3 build_site.py --deploy
echo
read -n 1 -s -r -p "Done. Press any key to close this window..."
