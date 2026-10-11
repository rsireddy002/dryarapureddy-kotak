import re
import pyotp

line = ""
for l in open("kotak_creds.txt", encoding="utf-8-sig"):
    if l.startswith("KOTAK_TOTP_SECRET="):
        line = l.split("=", 1)[1]
raw = line.strip().strip('"').strip("'")
sec = re.sub(r"[\s-]", "", raw).upper()
print("length:", len(sec), "(real keys are usually 16-32 characters)")
bad = sorted(set(c for c in sec if c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"))
if not sec:
    print("KOTAK_TOTP_SECRET line is missing or empty")
elif bad:
    print("These characters are not allowed in a TOTP key:", bad)
    print("Re-copy the key from Kotak exactly, with no label, quotes or spaces.")
else:
    print("current code:", pyotp.TOTP(sec).now())
