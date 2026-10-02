import os
import urllib.parse
import requests

# Fill in your details from Upstox Developer Console
API_KEY = os.environ.get("UPSTOX_API_KEY")
API_SECRET = os.environ.get("UPSTOX_API_SECRET")
if not API_KEY or not API_SECRET:
    raise SystemExit("Set UPSTOX_API_KEY and UPSTOX_API_SECRET environment variables.")
REDIRECT_URI = "https://127.0.0.1"  # Must match exact URL in Upstox Developer App

# 1. Generate the Auth URL
params = {
    'response_type': 'code',
    'client_id': API_KEY,
    'redirect_uri': REDIRECT_URI
}
auth_url = f"https://api.upstox.com/v2/login/authorization/dialog?{urllib.parse.urlencode(params)}"

print("=== UPSTOX DAILY AUTHENTICATION ===")
print("1. Open this URL in your web browser:\n")
print(auth_url)
print("\n2. Log in with your mobile number, OTP, and 6-digit PIN.")
print("3. After login, your browser will redirect to a page that fails/shows 'Can't reach this page'.")
print("4. Copy the entire redirected URL from your browser address bar and paste it below.\n")

redirected_url = input("Paste the full redirected URL here: ").strip()

# 2. Extract 'code' from redirected URL
parsed_url = urllib.parse.urlparse(redirected_url)
code = urllib.parse.parse_qs(parsed_url.query).get('code', [None])[0]

if not code:
    print("Error: Could not extract authorization code from the URL.")
    exit()

# 3. Exchange Code for Access Token
token_url = 'https://api.upstox.com/v2/login/authorization/token'
headers = {
    'accept': 'application/json',
    'Content-Type': 'application/x-www-form-urlencoded'
}
data = {
    'code': code,
    'client_id': API_KEY,
    'client_secret': API_SECRET,
    'redirect_uri': REDIRECT_URI,
    'grant_type': 'authorization_code'
}

response = requests.post(token_url, headers=headers, data=data)
res_json = response.json()

if 'access_token' in res_json:
    access_token = res_json['access_token']
    print("\nSUCCESS! Your Access Token for today is:\n")
    print(access_token)
    
    # Save token locally to a text file for your screener script
    with open("upstox_token.txt", "w") as f:
        f.write(access_token)
    print("\nSaved token to 'upstox_token.txt'.")
else:
    print("Failed to fetch access token:", res_json)