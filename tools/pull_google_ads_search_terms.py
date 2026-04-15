"""
Pull Google Ads search term report. Shows what people actually searched.

.env keys: GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_ADS_CUSTOMER_ID_{brand}, GOOGLE_ADS_DEVELOPER_TOKEN
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()


def pull(customer_id: str, developer_token: str, start_date: str, end_date: str) -> dict:
    if not customer_id or not developer_token:
        return {"error": "Missing Google Ads credentials"}

    try:
        from google.ads.googleads.client import GoogleAdsClient

        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        client = GoogleAdsClient.load_from_dict({
            "developer_token": developer_token,
            "json_key_file_path": credentials_path,
            "login_customer_id": customer_id,
        })
        ga_service = client.get_service("GoogleAdsService")

        query = f"""
            SELECT search_term_view.search_term, metrics.impressions, metrics.clicks,
                   metrics.ctr, metrics.conversions, metrics.cost_micros
            FROM search_term_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY metrics.clicks DESC
            LIMIT 15
        """

        response = ga_service.search_stream(customer_id=customer_id, query=query)
        terms = []
        for batch in response:
            for row in batch.results:
                cost = row.metrics.cost_micros / 1_000_000
                terms.append({
                    "term": row.search_term_view.search_term,
                    "impressions": row.metrics.impressions,
                    "clicks": row.metrics.clicks,
                    "ctr": f"{row.metrics.ctr * 100:.1f}%",
                    "conversions": int(row.metrics.conversions),
                    "cost": f"${cost:.2f}",
                })
        return {"terms": terms}
    except Exception as exc:
        return {"error": f"Google Ads Search Terms error: {exc}"}


if __name__ == "__main__":
    cid = os.getenv("GOOGLE_ADS_CUSTOMER_ID_RC", "")
    token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    result = pull(cid, token, "2026-03-01", "2026-03-31")
    print(json.dumps(result, indent=2))
