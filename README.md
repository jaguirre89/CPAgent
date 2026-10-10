# Tax Dashboard

A local web application for managing personal tax filing as a self-employed business owner with multiple LLCs (Schedule C). Parses financial source files (CSVs, PDFs), generates Schedule C workpapers, tracks document completeness, and surfaces tax strategies.

## Prerequisites

- Python 3.9+
- pip

## Installation

```bash
cd tax_dashboard
pip install -r requirements.txt
```

## First Launch

```bash
python app.py
```

Open http://localhost:5050 in your browser. On first launch, you'll see a setup screen:

1. Set the **root folder path** (the parent directory containing your `YYYY Tax Year` folders)
2. Select your **filing status**
3. Add your **primary entity** (LLC name, type, state, accounting software)
4. Click "Save & Launch Dashboard"

You can add more entities (up to 3) from the Settings tab after setup.

### Custom Port

```bash
python app.py --port 8080
```

## How It Works

### Adding Source Files

Drop files into the correct subfolder within your tax year directory. The app auto-detects file types:

- **P&L CSV** → entity folder (e.g., `03_Cressio_LLC/`)
- **Bank statement PDFs** → entity folder
- **1099 PDFs** → entity folder or `Contractors_1099s/`
- **W-2 PDFs** → `Wife_W2/` or `Spouse_Income/`

The file watcher detects changes in real time and refreshes the dashboard automatically.

### Folder Structure

The app auto-discovers folders by name pattern — no hardcoded numbering required:

```
Tax Agent/
├── 2025 Tax Year/
│   ├── 01_Personal/
│   ├── 02_Wife_W2/
│   ├── 03_Cressio_LLC/        ← Wave P&L CSV, bank PDFs, 1099s
│   ├── 04_Invenst_Labs_LLC/   ← QBO P&L CSV, bank PDFs
│   ├── 05_Vehicle/
│   ├── 06_Home_Office/
│   ├── ...
│   └── 12_Final_Worksheets/
│       └── dashboard_notes.json
└── tax_dashboard/              ← this app
```

### Starting a New Tax Year

Click **"+ New Year"** in the header. This:
- Creates the folder structure (copied from the most recent year)
- Copies tax constants (IRS rates) for the new year
- Creates an empty notes file
- Does NOT copy source files

### Updating IRS Constants

Go to **Settings → Tax Constants** to update mileage rates, deduction limits, and other IRS rates for the selected year.

### Adding a New Entity

Go to **Settings → Entities → + Add Entity**. Configure the entity name, type (LLC/S-Corp/C-Corp), accounting software, bank accounts, and credit cards. The app will auto-discover the entity's folder by matching the name.

## Dashboard Tabs

1. **Overview** — KPI cards and charts (revenue, expenses, net profit, SE tax)
2. **Schedule C** — Per-entity workpapers with line-by-line detail, matching IRS filing structure
3. **Findings** — Auto-generated issues (uncategorized expenses, missing docs, intercompany mismatches)
4. **Bank Rec** — Monthly reconciliation per bank account from parsed statements
5. **Checklist** — Document completeness tracker with progress bar
6. **Strategy** — Tax optimization recommendations (S-Corp election, retirement, QBI deduction)
7. **Settings** — Entity config, tax constants, app preferences

## Troubleshooting

### Parse Errors
Check the error badge in the header. Parse errors are logged but don't crash the app — affected sections show "Not yet available" placeholders.

### Port Conflicts
If port 5050 is in use: `python app.py --port 5051`

### Missing Data
If a P&L CSV isn't detected, check the CSV format. The app auto-detects Wave and QBO formats. For other formats, a mapping modal will appear asking you to identify the columns.

### File Permissions
Notes are saved to `dashboard_notes.json` in the year's Final Worksheets folder. Ensure the app has write access to that directory.

### Slow Initial Load
First load parses all PDFs (bank statements, 1099s) which can take 10-20 seconds. Subsequent loads use cached data until files change.
