# Web Content Finder

![Screenshot of frontend displaying data from recent searches.](https://hosting.photobucket.com/bbcfb0d4-be20-44a0-94dc-65bff8947cf2/517ce960-11ff-49e5-a150-df7b42be0d6a.png)

Searches Google's Programmable Search Engine for your query, visits each result's landing page, scrapes the readable text and saves it all to an `output` folder.

## Application Overview

A command-line tool which authenticates against Google's Programmable Search Engine using your API key and search-engine ID. The application then runs each query you provide requesting as many result pages as you specify. The collected result links are normalized and de-duplicated so the same URL is never fetched twice.

From there, a number of concurrent workers visits each landing page, confirming the response is HTML before reading it and parses the markup with `BeautifulSoup` to extract the page title, meta description and the main readable body text. Pages which are blocked by domain, return errors, aren't HTML or yield no text are recorded as skipped. Everything is written to a single `output` folder. The project also includes a frontend UI for reviewing what each run collected.

## Basic Setup Instructions

Below are the required software programs and instructions for installing and using this application on a Linux machine.

### Programs Needed

- [Git](https://git-scm.com/downloads)

- [Python](https://www.python.org/downloads/)

### Steps For Use

1. Install the above programs

2. Open a terminal

3. Clone this repository: `git clone git@github.com:devbret/web-content-finder.git`

4. Navigate to the repo's directory: `cd web-content-finder`

5. Create a virtual environment: `python3 -m venv venv`

6. Activate your virtual environment: `source venv/bin/activate`

7. Install the needed dependencies: `pip install -r requirements.txt`

8. Create your configuration file from the template: `cp .env.template .env`

9. Open `.env` and set values for `API_KEY`, `CX` and so on

10. Run the program: `python3 app.py`

11. Start a local web server from the repo's root directory: `python3 -m http.server 8000`

12. Visit the frontend UI in your browser: `http://localhost:8000`

13. When finished, stop the web server: `Ctrl+C`

14. Exit the virtual environment: `deactivate`

## Other Considerations

This project repo is intended to demonstrate an ability to do the following:

- Query Google's Programmable Search Engine for one or more search terms and scrape the readable text from every result's landing page

- Normalize result URLs by stripping tracking parameters and rely on a pool of concurrent workers to fetch each page

- Use `BeautifulSoup` to extract the page title, meta description and main body text, saving each page as an individual `.txt` file

- Record each result's search rank, HTTP status, content hash, word count and skip reason

If you have any questions or would like to collaborate, please reach out either on GitHub or via [my website](https://bretbernhoft.com/).
