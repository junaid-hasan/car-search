import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
import streamlit as st


DATA_PATH = Path(__file__).resolve().parent / "data" / "cars.json"
CRAIGSLIST_SEARCH_BASE = "https://seattle.craigslist.org/search/cta"
REQUEST_TIMEOUT_SECONDS = 20
MAX_LINKS_PER_CAR = 5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

V6_HINT_RE = re.compile(r"\b(v6|6\s*cyl|6-cylinder|3\.5l?)\b", re.I)


@st.cache_data
def load_cars() -> list[dict[str, Any]]:
    with DATA_PATH.open() as f:
        payload = json.load(f)
    return payload["cars"]


def parse_year_range(spec: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{4})", spec.strip())
    if match:
        start, end = map(int, match.groups())
        return start, end
    year = int(spec.strip())
    return year, year


def parse_max_miles(spec: str) -> int | None:
    text = spec.strip().lower().replace(",", "")
    match = re.fullmatch(r"(\d+)(k?)", text)
    if not match:
        return None
    value = int(match.group(1))
    if match.group(2) == "k":
        value *= 1000
    return value


def parse_price_value(spec: str) -> int | None:
    text = spec.strip().lower().replace(",", "")
    text = text.replace("$", "")

    if not text:
        return None

    k_match = re.search(r"(\d+(?:\.\d+)?)\s*k\b", text)
    if k_match:
        return int(float(k_match.group(1)) * 1000)

    num_match = re.search(r"\d+", text)
    if num_match:
        return int(num_match.group(0))

    return None


def query_for_car(car_name: str) -> str:
    raw = re.sub(r"\(.*?\)", "", car_name)
    raw = re.sub(r"\s+", " ", raw).strip()

    tokens: list[str] = []
    for token in raw.split(" "):
        if "/" not in token:
            tokens.append(token)
            continue

        parts = [part for part in token.split("/") if part]
        if not parts:
            continue

        if all(part.isdigit() for part in parts):
            continue

        tokens.append(parts[0])

    query = " ".join(tokens)
    query = re.sub(r"\s+", " ", query).strip()
    return query.lower()


def build_autotempest_url(
    car: dict[str, Any],
    postal: str,
    min_price: int,
    budget: int,
    distance_miles: int,
) -> str:
    min_year, max_year = parse_year_range(car["years"])
    query = query_for_car(car["car"])
    parts = query.split()

    params: dict[str, Any] = {
        "zip": postal,
        "radius": distance_miles,
        "minprice": min_price,
        "maxprice": budget,
        "minyear": min_year,
        "maxyear": max_year,
    }

    if parts:
        params["make"] = parts[0]
    if len(parts) > 1:
        params["model"] = " ".join(parts[1:])

    max_miles = parse_max_miles(car.get("maxMiles", ""))
    if max_miles is not None:
        params["maxmiles"] = max_miles

    req = requests.Request("GET", "https://www.autotempest.com/results", params=params)
    return req.prepare().url


def build_search_url(
    car: dict[str, Any],
    postal: str,
    min_price: int,
    budget: int,
    distance_miles: int,
    clean_title_only: bool,
) -> str:
    min_year, max_year = parse_year_range(car["years"])
    params: dict[str, Any] = {
        "postal": postal,
        "search_distance": distance_miles,
        "query": query_for_car(car["car"]),
        "srchType": "T",
        "min_price": min_price,
        "max_price": budget,
        "min_auto_year": min_year,
        "max_auto_year": max_year,
    }

    max_miles = parse_max_miles(car.get("maxMiles", ""))
    if max_miles is not None:
        params["max_auto_miles"] = max_miles

    if clean_title_only:
        params["auto_title_status"] = 1

    req = requests.Request("GET", CRAIGSLIST_SEARCH_BASE, params=params)
    return req.prepare().url


def extract_listings(page_html: str) -> list[dict[str, str]]:
    blocks = re.findall(r'<li class="cl-static-search-result".*?</li>', page_html, re.S)
    results: list[dict[str, str]] = []
    for block in blocks:
        href_match = re.search(r'<a href="([^"]+)"', block)
        title_match = re.search(r'<div class="title">(.*?)</div>', block, re.S)
        price_match = re.search(r'<div class="price">(.*?)</div>', block, re.S)

        if not href_match:
            continue

        title = ""
        if title_match:
            title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()

        price = ""
        if price_match:
            price = html.unescape(re.sub(r"<[^>]+>", "", price_match.group(1))).strip()

        results.append(
            {
                "url": href_match.group(1),
                "title": title,
                "price": price,
            }
        )
    return results


def filter_listings_by_price(
    listings: list[dict[str, str]],
    max_price: int,
) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    for listing in listings:
        listing_price = parse_price_value(listing.get("price", ""))
        if listing_price is None or listing_price <= max_price:
            filtered.append(listing)
    return filtered


def car_requires_v6_filter(car: dict[str, Any]) -> bool:
    return "(v6)" in car.get("car", "").lower()


def engine_prefixes_from_spec(engine_spec: str) -> list[str]:
    prefixes: list[str] = []
    for token in re.split(r"[^A-Za-z0-9]+", engine_spec.upper()):
        if not token:
            continue
        if not (re.search(r"[A-Z]", token) and re.search(r"\d", token)):
            continue
        prefix_match = re.match(r"([A-Z]?\d{1,3}[A-Z]{1,3})", token)
        if prefix_match:
            prefix = prefix_match.group(1)
            if prefix not in prefixes:
                prefixes.append(prefix)
    return prefixes


def extract_cylinder_count(listing_html: str) -> int | None:
    vin_pattern = re.search(r'"Engine Number of Cylinders","(\d+)"', listing_html)
    if vin_pattern:
        return int(vin_pattern.group(1))

    attr_pattern = re.search(
        r'<span class="attr">\s*<b>([^<]+)</b>\s*cylinders\s*</span>',
        listing_html,
        re.I,
    )
    if attr_pattern:
        digits = re.search(r"\d+", attr_pattern.group(1))
        if digits:
            return int(digits.group(0))

    return None


def extract_engine_model(listing_html: str) -> str | None:
    vin_pattern = re.search(r'"Engine Model","([^"]+)"', listing_html)
    if vin_pattern:
        return vin_pattern.group(1).upper()
    return None


def fetch_listing_specs(url: str) -> tuple[int | None, str | None]:
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        response.raise_for_status()
        cylinders = extract_cylinder_count(response.text)
        engine_model = extract_engine_model(response.text)
        return cylinders, engine_model
    except Exception:  # noqa: BLE001
        return None, None


def filter_v6_listings(
    listings: list[dict[str, str]],
    engine_prefixes: list[str],
) -> list[dict[str, str]]:
    keep_map: dict[int, bool] = {}
    candidates_for_lookup: list[tuple[int, dict[str, str]]] = []

    for idx, listing in enumerate(listings):
        title = listing.get("title", "")
        if V6_HINT_RE.search(title):
            keep_map[idx] = True
            continue
        candidates_for_lookup.append((idx, listing))

    if not candidates_for_lookup:
        return [
            listing for idx, listing in enumerate(listings) if keep_map.get(idx, False)
        ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_listing = {
            executor.submit(fetch_listing_specs, listing["url"]): (idx, listing)
            for idx, listing in candidates_for_lookup
        }
        for future in as_completed(future_to_listing):
            idx, _ = future_to_listing[future]
            cylinders, engine_model = future.result()

            if cylinders is not None:
                keep_map[idx] = cylinders == 6
                continue

            if engine_model and engine_prefixes:
                # Least aggressive behavior: do not exclude on engine-model mismatch.
                keep_map[idx] = True
                continue

            keep_map[idx] = True

    return [listing for idx, listing in enumerate(listings) if keep_map.get(idx, False)]


def search_car(
    index: int,
    car: dict[str, Any],
    postal: str,
    min_price: int,
    budget: int,
    distance_miles: int,
    clean_title_only: bool,
    aggressive_mode: bool,
) -> dict[str, Any]:
    effective_max_price = budget
    model_max_price = parse_price_value(car.get("maxPrice", ""))
    if not aggressive_mode and model_max_price is not None:
        effective_max_price = min(budget, int(model_max_price * 1.1))

    if effective_max_price < min_price:
        autotempest_url = build_autotempest_url(
            car,
            postal,
            min_price,
            min_price,
            distance_miles,
        )
        return {
            "index": index,
            "car": car["car"],
            "years": car.get("years", ""),
            "carComplaintsPage": car.get("carComplaintsPage", ""),
            "autotempestUrl": autotempest_url,
            "listings": [],
            "totalListings": 0,
            "error": "",
            "searchUrl": "",
        }

    url = build_search_url(
        car,
        postal,
        min_price,
        effective_max_price,
        distance_miles,
        clean_title_only,
    )
    autotempest_url = build_autotempest_url(
        car,
        postal,
        min_price,
        effective_max_price,
        distance_miles,
    )
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        response.raise_for_status()
        listings = extract_listings(response.text)
        if not aggressive_mode:
            listings = filter_listings_by_price(listings, effective_max_price)
        if car_requires_v6_filter(car):
            engine_prefixes = engine_prefixes_from_spec(car.get("engine", ""))
            listings = filter_v6_listings(listings, engine_prefixes)
        total_listings = len(listings)
        listings = listings[:MAX_LINKS_PER_CAR]
        final_search_url = response.url
    except Exception as exc:  # noqa: BLE001
        return {
            "index": index,
            "car": car["car"],
            "years": car.get("years", ""),
            "carComplaintsPage": car.get("carComplaintsPage", ""),
            "autotempestUrl": autotempest_url,
            "listings": [],
            "totalListings": 0,
            "error": str(exc),
            "searchUrl": url,
        }

    return {
        "index": index,
        "car": car["car"],
        "years": car.get("years", ""),
        "carComplaintsPage": car.get("carComplaintsPage", ""),
        "autotempestUrl": autotempest_url,
        "listings": listings,
        "totalListings": total_listings,
        "error": "",
        "searchUrl": final_search_url,
    }


def run_search(
    cars: list[dict[str, Any]],
    postal: str,
    min_price: int,
    budget: int,
    distance_miles: int,
    clean_title_only: bool,
    aggressive_mode: bool,
) -> list[dict[str, Any]]:
    jobs: list[Any] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for index, car in enumerate(cars):
            jobs.append(
                executor.submit(
                    search_car,
                    index,
                    car,
                    postal,
                    min_price,
                    budget,
                    distance_miles,
                    clean_title_only,
                    aggressive_mode,
                )
            )

        results = [job.result() for job in as_completed(jobs)]

    return sorted(results, key=lambda row: row["index"])


def render_rows(
    rows: list[dict[str, Any]],
    show_complaints: bool,
    show_autotempest: bool,
) -> None:
    if show_complaints and show_autotempest:
        header_cols = st.columns([2, 1.5, 6, 4, 4])
        header_labels = [
            "**Car**",
            "**Year Range**",
            "**Listings**",
            "**Browse autotempest**",
            "**Browse complaints**",
        ]
    elif show_complaints:
        header_cols = st.columns([2, 1.5, 8, 4])
        header_labels = [
            "**Car**",
            "**Year Range**",
            "**Listings**",
            "**Browse complaints**",
        ]
    elif show_autotempest:
        header_cols = st.columns([2, 1.5, 8, 4])
        header_labels = [
            "**Car**",
            "**Year Range**",
            "**Listings**",
            "**Browse autotempest**",
        ]
    else:
        header_cols = st.columns([2, 1.5, 10])
        header_labels = ["**Car**", "**Year Range**", "**Listings**"]

    for col, label in zip(header_cols, header_labels):
        with col:
            st.markdown(label)

    st.divider()

    for row in rows:
        if show_complaints and show_autotempest:
            left, year_col, middle, right, far_right = st.columns([2, 1.5, 6, 4, 4])
        elif show_complaints:
            left, year_col, middle, far_right = st.columns([2, 1.5, 8, 4])
            right = None
        elif show_autotempest:
            left, year_col, middle, right = st.columns([2, 1.5, 8, 4])
            far_right = None
        else:
            left, year_col, middle = st.columns([2, 1.5, 10])
            right = None
            far_right = None

        with left:
            st.markdown(f"**{row['car']}**")

        with year_col:
            st.write(row["years"])

        with middle:
            for listing in row["listings"]:
                label = listing["title"] or "listing"
                if listing["price"]:
                    label = f"{listing['price']} - {label}"
                st.markdown(f"- [{label}]({listing['url']})")

        if show_autotempest and right is not None:
            with right:
                st.write(row["autotempestUrl"])

        if show_complaints and far_right is not None:
            with far_right:
                st.write(row["carComplaintsPage"])

        st.divider()


def main() -> None:
    st.set_page_config(page_title="Unbeatable Cars", layout="wide")
    st.markdown(
        """
        <style>
        div[data-testid="stFormSubmitButton"] > button {
            min-height: 3rem;
            font-size: 1.05rem;
            font-weight: 700;
            background: #16a34a;
            color: #ffffff;
            border: 1px solid #15803d;
            border-radius: 0.6rem;
        }
        div[data-testid="stFormSubmitButton"] > button:hover {
            background: #15803d;
            color: #ffffff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Unbeatable Cars")
    st.caption("Find cars that dont complain")

    cars = load_cars()
    car_types = sorted({car.get("type", "Unknown") for car in cars})

    with st.form("search-form"):
        st.markdown("#### Required Filters")
        req_col1, req_col2 = st.columns([3, 3])
        with req_col1:
            st.markdown("### Postal (required)")
            postal = st.text_input(
                "Postal (required)",
                value="10274",
                label_visibility="collapsed",
            )
        with req_col2:
            st.markdown("### Budget (USD)")
            budget = st.number_input(
                "Budget (USD)",
                min_value=500,
                value=10000,
                step=500,
                label_visibility="collapsed",
            )

        with st.expander("Advanced Filters"):
            adv_col1, adv_col2, adv_col3 = st.columns([2, 2, 2])
            with adv_col1:
                min_price = st.number_input(
                    "Min Price (USD)",
                    min_value=0,
                    value=1000,
                    step=100,
                )
            with adv_col2:
                distance_miles = st.number_input(
                    "Search Distance (miles)",
                    min_value=1,
                    value=25,
                    step=5,
                )
            with adv_col3:
                selected_type = st.selectbox(
                    "Car Type",
                    ["All"] + car_types,
                    index=0,
                )

            adv_col4, adv_col5, adv_col6, adv_col7 = st.columns([2, 2, 2, 2])
            with adv_col4:
                clean_title_only = st.checkbox("Clean title", value=True)
            with adv_col5:
                aggressive_mode = st.checkbox(
                    "Aggressive",
                    value=False,
                    help=(
                        "When off, each car is capped at its JSON maxPrice +10%. "
                        "When on, only your Budget is used."
                    ),
                )
            with adv_col6:
                show_complaints = st.checkbox("Browse complaints", value=False)
            with adv_col7:
                show_autotempest = st.checkbox("Browse autotempest", value=False)

        submitted = st.form_submit_button("Search Listings", use_container_width=True)

    if submitted:
        trimmed_postal = postal.strip()
        if not trimmed_postal:
            st.error("Please enter a postal code.")
            return

        if int(min_price) > int(budget):
            st.error("Min Price must be less than or equal to Budget.")
            return

        selected_cars = cars
        if selected_type != "All":
            selected_cars = [car for car in cars if car.get("type") == selected_type]

        with st.spinner(f"Searching Craigslist for {len(selected_cars)} cars..."):
            results = run_search(
                selected_cars,
                trimmed_postal,
                int(min_price),
                int(budget),
                int(distance_miles),
                clean_title_only,
                aggressive_mode,
            )

        st.session_state["search_results"] = [row for row in results if row["listings"]]
        st.session_state["search_errors"] = [row for row in results if row["error"]]
        st.session_state["search_count"] = len(selected_cars)
        st.session_state["total_listings"] = sum(
            row.get("totalListings", 0) for row in results
        )

    rows = st.session_state.get("search_results", [])
    errors = st.session_state.get("search_errors", [])
    searched_count = st.session_state.get("search_count")
    total_listings = st.session_state.get("total_listings", 0)

    if searched_count is not None:
        st.subheader("Results")
        st.write(f"Total listings found: {total_listings}")

        if not rows:
            st.info("No matching listings found for the current inputs.")
        else:
            render_rows(
                rows,
                show_complaints=show_complaints,
                show_autotempest=show_autotempest,
            )

        if errors:
            with st.expander(f"Show fetch errors ({len(errors)})"):
                for row in errors:
                    st.write(f"{row['car']}: {row['error']}")


if __name__ == "__main__":
    main()
