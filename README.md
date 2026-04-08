---
title: Unbeatable Cars
emoji: 🚗
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8501
tags:
- streamlit
pinned: false
short_description: Curated reliable used-car search helper
---

# Unbeatable Cars

A Craigslist-focused used-car finder powered by a curated list of cars.

## What This Is

- A practical search tool built on a curated dataset of generally reliable used cars.
- It searches listings by ZIP code, budget, distance, and optional filters.
- It is not a universal or fully objective reliability score system (yet).

## Live Links

- Code: [junaid-hasan/car-search](https://github.com/junaid-hasan/car-search)
- Website: [junaid-hasan/car-search](https://huggingface.co/spaces/junaid-hasan/car-search)

## Data

- Primary dataset: `data/cars.json`
- The current list is curated manually and will evolve toward a hybrid (automated + reviewed) pipeline.

## Deploy With Your Own Car List

Yes, the core workflow is simple: update `data/cars.json` with your own curated list, then deploy.

Minimum useful fields per car entry:

- `car`
- `years` (for query year range)
- `maxMiles` (for mileage cap)
- `maxPrice` (used by non-aggressive filtering)
- `type` (for UI filter)
- `carComplaintsPage` (optional UI link, recommended)
- `engine` (recommended for better V6 filtering)

Then deploy by pushing your branch:

```bash
# Hugging Face Space
git push hf main

# GitHub repo
git push origin main
```

## Run Locally

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/streamlit run app.py
```
