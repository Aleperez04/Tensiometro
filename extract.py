import urllib.request
import subprocess
import sys
import os

url = "https://raw.githubusercontent.com/extremecoders-re/pyinstxtractor/master/pyinstxtractor.py"
extractor_path = "pyinstxtractor.py"

print("Downloading pyinstxtractor.py...")
try:
    urllib.request.urlretrieve(url, extractor_path)
    print("Downloaded successfully.")
except Exception as e:
    print(f"Error downloading: {e}")
    sys.exit(1)

exe_path = r"C:\Users\alepe\Downloads\tensiometro-final (1)\tensiometro-final (1).exe"
print(f"Extracting {exe_path}...")
subprocess.run([sys.executable, extractor_path, exe_path], check=True)
print("Extraction finished.")
