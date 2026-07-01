# Web Content Finder

Searches Google's Programmable Search Engine for your query, visits each result's landing page, scrapes the readable text and saves it all to an `output` folder.

## Application Overview

A command-line tool which authenticates against Google's Programmable Search Engine using your API key and search-engine ID. The application then runs each query you provide requesting as many result pages as you specify. The collected result links are normalized and de-duplicated so the same URL is never fetched twice.

From there, a number of concurrent workers visits each landing page, confirming the response is HTML before reading it and parses the markup with `BeautifulSoup` to extract the page title, meta description and the main readable body text. Pages which are blocked by domain, return errors, aren't HTML or yield no text are recorded as skipped. Everything is written to a single `output` folder.
