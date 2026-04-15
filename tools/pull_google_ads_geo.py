"""
Pull Google Ads geographic performance. Shows which cities/regions produce conversions.

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
            SELECT geographic_view.country_criterion_id, geographic_view.location_type,
                   campaign_criterion.location.geo_target_constant,
                   metrics.impressions, metrics.clicks, metrics.conversions, metrics.cost_micros
            FROM geographic_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
                AND geographic_view.location_type = 'LOCATION_OF_PRESENCE'
            ORDER BY metrics.conversions DESC
            LIMIT 10
        """

        response = ga_service.search_stream(customer_id=customer_id, query=query)
        locations = []
        for batch in response:
            for row in batch.results:
                cost = row.metrics.cost_micros / 1_000_000
                clicks = row.metrics.clicks
                cpc = cost / clicks if clicks > 0 else 0
                geo_resource = row.campaign_criterion.location.geo_target_constant or ""
                locations.append({
                    "geo_id": geo_resource.split("/")[-1] if "/" in geo_resource else geo_resource,
                    "impressions": row.metrics.impressions,
                    "clicks": clicks,
                    "conversions": int(row.metrics.conversions),
                    "cost": f"${cost:.2f}",
                    "cpc": f"${cpc:.2f}",
                })
        return {"locations": locations}
    except Exception as exc:
        return {"error": f"Google Ads Geo error: {exc}"}


if __name__ == "__main__":
    cid = os.getenv("GOOGLE_ADS_CUSTOMER_ID_RC", "")
    token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    result = pull(cid, token, "2026-03-01", "2026-03-31")
    print(json.dumps(result, indent=2))
