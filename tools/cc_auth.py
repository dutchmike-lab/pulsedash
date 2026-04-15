"""
Constant Contact OAuth2 Authorization Flow
Run this once to get your access_token and refresh_token.
"""
import os
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("CONSTANT_CONTACT_API_KEY")
CLIENT_SECRET = os.getenv("CONSTANT_CONTACT_CLIENT_SECRET")
REDIRECT_URI = "https://localhost"
AUTH_URL = "https://authz.constantcontact.com/oauth2/default/v1/authorize"
TOKEN_URL = "https://authz.constantcontact.com/oauth2/default/v1/token"
SCOPES = "contact_data campaign_data offline_access"


def get_auth_url():
    params = {
        "response_type": "code",
        "client_id": API_KEY,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "marketing-dashboard",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code):
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(API_KEY, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    url = get_auth_url()
    print("\n1. Opening browser for authorization...")
    print(f"   If it doesn't open, go to:\n   {url}\n")
    webbrowser.open(url)

    print("2. After you authorize, you'll be redirected to a URL like:")
    print("   https://localhost?code=XXXXX&state=marketing-dashboard")
    print("   (The page will show an error — that's normal!)\n")

    code = input("3. Paste the 'code' value from the URL here: ").strip()

    print("\n4. Exchanging code for tokens...")
    tokens = exchange_code(code)

    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")

    print("\n✅ Success! Add these to your .env file:\n")
    print(f"CONSTANT_CONTACT_ACCESS_TOKEN={access_token}")
    print(f"CONSTANT_CONTACT_REFRESH_TOKEN={refresh_token}")
    print(f"\nToken expires in {tokens.get('expires_in', '?')} seconds.")
    print("The refresh token can be used to get a new access token when it expires.")
