# Assignment

Three small Python scripts for the AccuKnox screening. They take data from an API or a CSV, check it, and save it in SQLite (or draw a chart).

## What the scripts do

- assignment1_books.py: reads books from the Open Library API and saves them in a SQLite database. It prints the saved books at the end.
- assignment2_scores.py: gets student scores from an API, finds the average, and makes a bar chart. There is no real student scores API, so I made a small mock server (mock_scores_api.py) that serves the data in pages and needs an API key.
- assignment3_csv.py: reads a CSV of users (name, email) and puts it into SQLite. common.py is a helper for the API calls, used by Assignment 1 and Assignment 2.

## How to run

    pip install -r requirements.txt
    python assignment1_books.py

For Assignment 2, start the mock server in one terminal (python mock_scores_api.py). In another terminal set the API key and run the chart script:

    set API_KEY=secret123
    python assignment2_scores.py

(On Linux or Mac use export API_KEY=secret123; in PowerShell use $env:API_KEY="secret123".) The chart is saved as scores_chart.png.

    python assignment3_csv.py

Running Assignment 1 or Assignment 3 a second time doesn't add duplicates.

## Security things I cared about

- **Retry logic.** API calls have a 10 second timeout and retry 3 times if the server fails. If the API says "too many requests" (429), the script waits as long as it asks before trying again. Wrong key (401) or wrong URL (404) stop straight away, because retrying won't help.
- **Uniqueness with SHA-256.** In Assignment 1, books have no unique ID, so I hash the title and author and store that, which stops duplicates. In Assignment 3, I hash the whole CSV file, so the same file can't be imported twice. Emails are also unique in the table.
- **Validation with Pydantic.** Everything from an API or a CSV is checked before it is used. Bad records are skipped and logged, and the script keeps going.
- **Authentication.** The mock server needs an API key in a header. The key comes from an environment variable, not from the code. The[secret123] default in the mock server is a fake key just for local testing.
- **HTTPS.** Requests verify certificates and I never turn that off. The mock server is plain http because it only runs on localhost, and the script warns if any other http URL is used.
- **Other small things.** SQL always uses ?placeholders. Assignment 3 rejects names starting with =, +, - or @ (CSV formula injection) and refuses files over 5 MB or 10,000 rows. Logs only show row numbers and short reasons, never emails or keys.

## Assumptions

- Open Library has thousands of results for "python", so Assignment 1 only reads one page (MAX_PAGES = 1).
- Student scores are between 0 and 100. Anything else is skipped.
- The CSV is UTF-8. The delimiter is detected automatically.
- Emails are lowercased before saving.

## With more time

- Add role-based access (RBAC) to the mock server, for example a read-only key and an admin key. SQLite has no real users or roles, so it would have to be done in the code.
- Add tests with pytest.
- Move to PostgreSQL with proper migrations.
- If I add an LLM step later (for example, to suggest fixes for bad CSV rows), I would send it through a gateway like LiteLLM, so keys, limits and logging stay in one place. I would not send raw user emails to the model.

