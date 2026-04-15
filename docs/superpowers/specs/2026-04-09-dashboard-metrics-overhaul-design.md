# Dashboard Metrics Overhaul — Design Spec

**Date:** 2026-04-09
**Status:** Draft
**Scope:** Add Executive Summary tab, enrich Marketing + Sales tabs with new API data, add period-over-period comparisons across all metrics.

---

## Problem

The dashboard currently shows marketing activity (traffic, ads, social, email, forms) and basic pipeline counts, but has four critical blind spots:

1. **No revenue visibility** — leadership can't see how much money was closed
2. **No channel attribution** — marketing can't tell which source produces paying customers
3. **No sales process metrics** — sales can't see response times, follow-up gaps, or outbound activity
4. **No trend comparisons** — nobody can tell if things are getting better or worse vs last period

## Audiences

| Audience | Primary Questions |
|----------|-------------------|
| Leadership/Owners | Is the business growing? What's our revenue? What's our margin? |
| Sales Team | Am I following up fast enough? What's in my pipeline? What's my close rate? |
| Marketing Team | Which channels drive real leads? What's our cost per lead? Which content converts? |

## Solution

### Tab Structure

| Tab | Default? | Audience | Purpose |
|-----|----------|----------|---------|
| **Executive Summary** | Yes (landing page) | Leadership | 6 hero KPIs + trends + mini funnel |
| **Marketing** | No (existing, enhanced) | Marketing | Channel performance, content, attribution |
| **Sales & Pipeline** | No (existing, enhanced) | Sales | Pipeline, appointments, response time, activity |

---

## Executive Summary Tab (NEW)

### Hero KPI Row (6 cards, full width)

| KPI | Source | Format | Color Logic |
|-----|--------|--------|-------------|
| Revenue Closed | JobTread `invoiceTotal` / `estimateTotal` on sold jobs | `$XXK` / `$X.XM` with sparkline | Green |
| Active Pipeline Value | JobTread estimates on quoted/active jobs | `$XXK` | Blue (accent) |
| Total Leads | GHL submissions + Gravity Forms + Ad conversions | Integer with source mini-bar | Default |
| Cost Per Lead | (Google Ads spend + Meta Ads spend) / Total Leads | `$XX.XX` | Green if < $50, yellow if $50-100, red if > $100 (configurable) |
| Win Rate | JobTread sold / (sold + lost) | `XX%` | Green >= 40%, yellow 25-39%, red < 25% |
| Appointments Set | GHL `/calendars/events` booked count | Integer with show rate subtitle | Default |

### Period Comparison Row

A compact row below the hero KPIs showing the delta for each metric vs the previous period of equal length.

- Format: `+12%` (green up arrow) or `-5%` (red down arrow) or `--` if no prior data
- Each tool runs twice: once for the current date range, once for the previous period

### Mini Visualizations (two half-width cards below)

1. **Sales Funnel** — horizontal bar chart: New Leads → Appointment Set → Quoted → Sold (from JobTread Lead Status counts)
2. **Lead Sources** — horizontal bar showing leads by channel: Organic, Paid, Social, Email, Direct, Referral (from GHL source classification + GA4 traffic sources)

---

## Marketing Tab Enhancements

### New Metrics to Add (to existing cards or new cards)

| Metric | Source | Card Placement |
|--------|--------|----------------|
| Landing page conversion rates | GA4: `landingPage` dimension + `conversions` metric | New card: "Top Landing Pages" (span 3) |
| New vs returning visitors | GA4: `newVsReturning` dimension | Add to existing Web Analytics card as stat row |
| Search terms (top 10) | Google Ads: `search_term_view` resource | New card: "Top Search Terms" (span 2) |
| Geographic performance (top cities) | Google Ads: `geographic_view` resource | New card: "Top Locations" (span 2) |
| Phone calls from ads | Google Ads: `call_view` resource / `metrics.phone_calls` | Add to existing Google Ads card |
| Meta Ads placement breakdown | Meta API: `breakdowns: ["publisher_platform", "platform_position"]` | Add to existing Meta Ads card as breakdown table |
| Meta Ads frequency + reach | Meta API: `frequency`, `reach` fields | Add to existing Meta Ads card as stat rows |
| Email per-link click details | Constant Contact: click details endpoint | Add to existing Email card or new expandable detail |
| Period-over-period trends | All sources (previous period comparison) | Trend arrows on every metric card |

### No Changes To

- YouTube card (already solid for now)
- Instagram card (already solid for now)
- Form Fills card (already solid)

---

## Sales & Pipeline Tab Enhancements

### New Metrics to Add

| Metric | Source | Card Placement |
|--------|--------|----------------|
| Revenue Closed | JobTread financials | Already added this session — "Revenue & Pipeline" card |
| Pipeline Value | JobTread financials | Already added this session |
| Avg Job Value | JobTread financials | Already added this session |
| Win Rate | JobTread Lead Status counts | Already added this session |
| Profit Margin | JobTread `estimateTotal - costTotal` per job | New stat in "Revenue & Pipeline" card |
| Payments vs Invoiced (A/R) | JobTread `amountPaid` vs `invoiceTotal` on documents | New stat in "Revenue & Pipeline" card |
| Appointments + Show Rate | GHL `/calendars/events` | Already added this session |
| Avg Response Time (improved) | GHL `/conversations/messages/export` — time from first inbound to first outbound | Replace current rough proxy in GHL card |
| Outbound Activity | GHL `/conversations/messages/export` — count of outbound SMS, email, calls per period | New card: "Sales Activity" (span 2) |
| Overdue Follow-ups | GHL `/contacts/{id}/tasks` — tasks past due date | New card or stat row in Sales Activity |
| Phone Calls from Ads | Google Ads `call_view` | New stat row in Appointments card |
| Lead Source Quality | Cross-source: leads by source that reach "Sold" in JobTread | New card: "Channel ROI" (span 2) |

### Production Pipeline

Already separated into its own card this session. No further changes needed.

---

## New Python Tools

| File | API Source | What It Pulls |
|------|-----------|---------------|
| `tools/pull_ghl_conversations.py` | GHL `/conversations/messages/export` | All messages for location, computes: avg response time (first inbound → first outbound), outbound counts by type (SMS/email/call), activity volume by day |
| `tools/pull_ghl_tasks.py` | GHL `/contacts/{contactId}/tasks` | Open tasks, overdue tasks, completion rate |
| `tools/pull_google_ads_search_terms.py` | Google Ads `search_term_view` | Top 15 search terms by clicks with impressions, CTR, conversions, cost |
| `tools/pull_google_ads_geo.py` | Google Ads `geographic_view` | Top 10 cities/zip codes by conversions with cost and CPC |

### Expanded Existing Tools

| File | What's Added |
|------|-------------|
| `pull_ga4.py` | Landing page conversion rates (top 10 by conversions), new vs returning visitor split |
| `pull_google_ads.py` | Phone call metrics (`phone_calls`, `phone_impressions`), call extension data |
| `pull_meta_ads.py` | `frequency`, `reach`, `cpm`, placement breakdown (`publisher_platform` + `platform_position`) |
| `pull_jobtread.py` | Profit margin (`estimateTotal - costTotal`), payments received vs invoiced (`amountPaid`), document-level financials |
| `pull_constant_contact.py` | Per-link click details for top campaigns |

---

## Period-Over-Period Comparison System

### How It Works

1. `pull_all.py` receives `--days 30` (or `--start`/`--end`)
2. It calculates two ranges: `current` (the requested range) and `previous` (same length, immediately before)
3. Each tool's `pull()` function is called twice — once per range
4. `transform_for_dashboard()` computes deltas: `((current - previous) / previous) * 100`
5. Every metric in `data.json` gets a `trend` field: `"+12.3%"` and a `dir` field: `"up"` / `"down"` / `"neutral"`

### Edge Cases

- If previous period has zero data: trend = `"--"`, dir = `"neutral"`
- If metric is a rate (CTR, win rate): show absolute point change, not percentage of percentage
- First-ever pull with no historical data: all trends show `"--"`

---

## Data Flow

```
pull_all.py --days 30
  ├── calculate current_range + previous_range
  ├── for each brand (rc, rnr, wl):
  │   ├── pull_ga4(current) + pull_ga4(previous)
  │   ├── pull_youtube(current) + pull_youtube(previous)
  │   ├── pull_google_ads(current) + pull_google_ads(previous)
  │   ├── pull_google_ads_search_terms(current)  # no trend needed
  │   ├── pull_google_ads_geo(current)            # no trend needed
  │   ├── pull_meta_ads(current) + pull_meta_ads(previous)
  │   ├── pull_instagram(current) + pull_instagram(previous)
  │   ├── pull_constant_contact(current) + pull_constant_contact(previous)
  │   ├── pull_gravity_forms(current) + pull_gravity_forms(previous)
  │   ├── pull_ghl(current) + pull_ghl(previous)
  │   ├── pull_ghl_conversations(current) + pull_ghl_conversations(previous)
  │   ├── pull_ghl_tasks(current)                 # snapshot, no trend
  │   └── pull_jobtread(current) + pull_jobtread(previous)
  ├── transform_for_dashboard() with trend calculation
  └── write .tmp/data.json
```

## Dashboard HTML Changes

### New Tab: Executive Summary

- Add `executive` tab button to subtab bar (first position, active by default)
- New `renderExecutive(brand)` function
- 6 hero KPI cards using existing `bentoCard()` with large font sizes
- Period comparison row using trend badges
- Mini funnel + lead sources cards

### Enhanced Marketing Tab

- Add new cards for search terms, geo performance, landing pages
- Add trend arrows to all existing metric cards
- Add new stat rows to existing Google Ads and Meta Ads cards

### Enhanced Sales Tab

- Add Sales Activity card (outbound counts, overdue tasks)
- Add Channel ROI card
- Add profit margin + A/R stats to Revenue & Pipeline card
- Improve response time with real conversation data

### Styling

- No new CSS framework or design system changes
- All new cards use existing `bentoCard()`, `pipeline-bar-row`, `stat-row`, `card-trend` classes
- Trend arrows use existing `trendClass()` and `trendIcon()` helpers
- Executive Summary hero cards get slightly larger font via inline style (28-32px) consistent with current Revenue & Pipeline card

---

## What's NOT In Scope

- Google Business Profile integration (requires separate API setup)
- YouTube Analytics API (requires separate OAuth scope — future enhancement)
- Instagram Stories/Reels deep metrics (requires webhook setup)
- Constant Contact A/B test results (not available via API)
- GHL reputation/reviews (no API endpoint)
- Historical data storage / database (dashboard remains point-in-time with period comparison)
- User authentication / role-based access (single HTML file, no auth)

---

## Implementation Order

1. Period-over-period infrastructure in `pull_all.py`
2. Expand existing tools (GA4, Google Ads, Meta Ads, JobTread, Constant Contact)
3. New tools (GHL conversations, GHL tasks, search terms, geo)
4. Executive Summary tab (HTML/JS)
5. Marketing tab enhancements (HTML/JS)
6. Sales tab enhancements (HTML/JS)
