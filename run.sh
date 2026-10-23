#!/bin/bash

cd "$(dirname "$0")" || exit 1

echo
echo "TCG Card Tracker"
echo "----------------"
read -r -p "Launch dashboard? [y/N]: " answer

case "$answer" in
    y|Y|yes|YES|Yes)
        echo "Launching..."
        .venv/bin/python -m streamlit run app.py
        ;;
    *)
        echo "Cancelled."
        ;;
esac
