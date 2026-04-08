#!/usr/bin/env python3
"""Build and refresh `data/cars.json` from CarComplaints pages.

The script now pulls CarComplaints' native complaint counts by model year and
uses them to rank the worst years, best years, and headline selection.
"""

from __future__ import annotations

import csv
import html as htmlmod
import json
import re
import urllib.request
from pathlib import Path


HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_page(url: str, cache: dict[str, str]) -> str:
    if url in cache:
        return cache[url]

    req = urllib.request.Request(url, headers=HEADERS)
    text = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    cache[url] = text
    return text


def fetch_headlines(
    url: str,
    page_cache: dict[str, str],
    headline_cache: dict[str, list[str]],
) -> list[str]:
    if url in headline_cache:
        return headline_cache[url]

    text = fetch_page(url, page_cache)
    match = re.search(
        r'"name":"Worst [^"]+ Problems".*?"itemListElement":\[(.*?)\]\s*\}',
        text,
        re.S,
    )
    if not match:
        headline_cache[url] = []
        return []

    block = match.group(1)
    headlines = [
        htmlmod.unescape(h) for h in re.findall(r'"headline":"([^"]+)"', block)
    ]
    headline_cache[url] = headlines
    return headlines


def fetch_year_counts(
    model_url: str,
    page_cache: dict[str, str],
) -> dict[int, int]:
    text = fetch_page(model_url, page_cache)
    matches = re.findall(
        r'<a href="[^"]+/((?:19|20)\d{2})/" title="[^"]+">\s*'
        r'<span class="label">\d{4}</span>\s*'
        r'<span class="bar"[^>]*>&nbsp;</span>\s*'
        r'<span class="count">([\d,]+)</span>',
        text,
        re.S,
    )
    counts: dict[int, int] = {}
    for year, count in matches:
        counts[int(year)] = int(count.replace(",", ""))
    return counts


def parse_years(spec: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{4})", spec.strip())
    if match:
        start, end = map(int, match.groups())
        return start, end
    year = int(spec.strip())
    return year, year


def clean_problem(headline: str) -> str:
    text = htmlmod.unescape(headline)
    text = re.sub(r"\s+(?:in|of|for) the \d{4}\b.*$", "", text, flags=re.I)
    text = re.sub(r"\s+-\s*\d{4}\b.*$", "", text, flags=re.I)
    text = re.sub(r"^The\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text.lower()


def year_entry(year: int, count: int) -> dict[str, int]:
    return {"year": year, "complaints": count}


def to_cell(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True)


def main() -> None:
    data_dir = Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "cars.json"
    with path.open() as f:
        data = json.load(f)

    page_cache: dict[str, str] = {}
    headline_cache: dict[str, list[str]] = {}

    for car in data["cars"]:
        source_url = car["verification"]["sourceUrl"]
        car["carComplaintsPage"] = source_url
        car.pop("bestYears", None)
        start, end = parse_years(car["years"])

        year_counts = fetch_year_counts(source_url, page_cache)
        in_range_years = [year for year in range(start, end + 1) if year in year_counts]
        count_order = sorted(
            in_range_years,
            key=lambda y: (year_counts.get(y, -1), y),
            reverse=True,
        )
        car["complaintCountsByYear"] = [
            year_entry(year, year_counts[year]) for year in sorted(in_range_years)
        ]
        car["worstYears"] = [
            year_entry(year, year_counts[year]) for year in count_order[:3]
        ]

        year_headlines: list[str] = []
        year_pages: list[str] = []
        selected_years = count_order or list(range(end, start - 1, -1))
        for year in selected_years:
            url = source_url.rstrip("/") + f"/{year}/"
            try:
                headlines = fetch_headlines(url, page_cache, headline_cache)
            except Exception:
                continue
            if not headlines:
                continue
            for headline in headlines:
                if headline not in year_headlines:
                    year_headlines.append(headline)
                    year_pages.append(url)
                if len(year_headlines) >= 10:
                    break
            if len(year_headlines) >= 10:
                break

        car["verification"]["sourceTopHeadlines"] = year_headlines[:10]
        car["verification"]["sourceYearPages"] = year_pages[:10]

        problems: list[str] = []
        seen: set[str] = set()

        def add_from(headlines: list[str]) -> bool:
            for headline in headlines:
                problem = clean_problem(headline)
                if problem and problem not in seen:
                    seen.add(problem)
                    problems.append(problem)
                if len(problems) >= 10:
                    return True
            return len(problems) >= 10

        if add_from(year_headlines):
            car["commonProblems"] = problems[:10]
            continue

        try:
            overview = fetch_headlines(source_url, page_cache, headline_cache)
        except Exception:
            overview = []
        if add_from(overview):
            car["commonProblems"] = problems[:10]
            continue

        offset = 1
        while len(problems) < 10 and (start - offset >= 1980 or end + offset <= 2025):
            candidates: list[str] = []
            hi = end + offset
            lo = start - offset
            if hi <= 2025:
                candidates.append(source_url.rstrip("/") + f"/{hi}/")
            if lo >= 1980 and lo != hi:
                candidates.append(source_url.rstrip("/") + f"/{lo}/")

            for url in candidates:
                try:
                    headlines = fetch_headlines(url, page_cache, headline_cache)
                except Exception:
                    headlines = []
                if add_from(headlines):
                    break

            offset += 1
            if offset > 20:
                break

        car["commonProblems"] = problems[:10]

    data["note"] = (
        "Normalized issue labels. CarComplaints year counts are now stored in "
        "complaintCountsByYear, with worstYears derived from those counts. "
        "commonProblems is year-free and derived from CarComplaints headlines, "
        "while verification.sourceTopHeadlines remains year-filtered."
    )

    with path.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    csv_path = path.with_suffix(".csv")
    fieldnames = [
        "rank",
        "car",
        "years",
        "engine",
        "transmission",
        "type",
        "maxMiles",
        "maxPrice",
        "carComplaintsPage",
        "complaintCountsByYear",
        "worstYears",
        "commonProblems",
        "sourceTopHeadlines",
    ]
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for car in data["cars"]:
            row = {key: to_cell(car.get(key, "")) for key in fieldnames}
            row["complaintCountsByYear"] = to_cell(car.get("complaintCountsByYear", []))
            row["worstYears"] = to_cell(car.get("worstYears", []))
            row["commonProblems"] = to_cell(car.get("commonProblems", []))
            row["sourceTopHeadlines"] = to_cell(
                car.get("verification", {}).get("sourceTopHeadlines", [])
            )
            writer.writerow(row)

    normalized_path = path.with_name("cars_by_year.csv")
    normalized_fieldnames = [
        "rank",
        "car",
        "years",
        "engine",
        "transmission",
        "type",
        "maxMiles",
        "maxPrice",
        "carComplaintsPage",
        "year",
        "complaints",
    ]
    with normalized_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=normalized_fieldnames)
        writer.writeheader()
        for car in data["cars"]:
            base = {
                "rank": car.get("rank", ""),
                "car": car.get("car", ""),
                "years": car.get("years", ""),
                "engine": car.get("engine", ""),
                "transmission": car.get("transmission", ""),
                "type": car.get("type", ""),
                "maxMiles": car.get("maxMiles", ""),
                "maxPrice": car.get("maxPrice", ""),
                "carComplaintsPage": car.get("carComplaintsPage", ""),
            }
            for item in car.get("complaintCountsByYear", []):
                row = dict(base)
                row["year"] = item.get("year", "")
                row["complaints"] = item.get("complaints", "")
                writer.writerow(row)


if __name__ == "__main__":
    main()
