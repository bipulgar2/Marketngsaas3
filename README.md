# SEO Agency Platform

AI-powered SEO task management platform for agencies.

## Architecture

This project follows a 3-layer architecture:

1. **Directives** (`directives/`) - SOPs and instructions
2. **Orchestration** - AI agents (you) making decisions
3. **Execution** (`execution/`) - Deterministic Python scripts

## Directory Structure

```
├── api/                    # Flask API
│   ├── index.py           # Main routes
│   ├── auth.py            # Authentication
│   └── agents/            # AI agent modules
├── public/                 # Frontend HTML/CSS/JS
├── execution/              # Deterministic scripts
├── directives/             # SOPs and instructions
├── supabase/
│   └── migrations/        # Database migrations
├── .tmp/                   # Temporary files (gitignored)
├── .env                    # Environment variables
└── requirements.txt
```

## Setup

1. Copy `.env.example` to `.env` and fill in credentials
2. Create Supabase project and run migrations
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python api/index.py`

## Tech Stack

- **Backend**: Flask + Python
- **Database**: Supabase (PostgreSQL)
- **Auth**: Supabase Auth
- **AI**: Gemini, Perplexity
- **SEO Data**: DataForSEO
- **Slides**: Google Slides API
