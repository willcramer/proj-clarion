# Clarion UI

React + TypeScript + Vite frontend for Proj Clarion.

See the [project README](../README.md) for the full story. Quickstart:

```bash
# from the repo root
just up           # Postgres
just api          # backend (port 8765)

cd ui
npm install
npm run dev       # Vite on port 5173, proxies /api/* to :8765
```

The first time you open http://127.0.0.1:5173, the **setup wizard** appears
and asks for your Anthropic + Grafana Cloud credentials. After saving,
the app loads.

## Layout

```
ui/
├── src/
│   ├── App.tsx               # Router + providers
│   ├── components/           # Shared UI primitives
│   │   ├── Logo.tsx          # Clarion mark
│   │   ├── KpiCard.tsx       # Stat tile w/ drilldown
│   │   ├── LogView.tsx       # Severity-tinted, ANSI-stripping log viewer
│   │   ├── PipelineStepper.tsx
│   │   ├── SetupGate.tsx     # Wraps the app, redirects to /setup when needed
│   │   ├── Toast.tsx
│   │   └── ...
│   ├── pages/                # Top-level routes
│   │   ├── Setup.tsx         # First-run wizard
│   │   ├── Dashboard.tsx
│   │   ├── NewDemo.tsx       # Build runner
│   │   ├── Pipelines.tsx
│   │   └── ...
│   └── lib/
│       ├── api.ts            # Backend client (typed)
│       ├── setup-api.ts      # /api/setup/* client
│       └── PipelineContext.tsx
└── public/favicon.svg        # Clarion mark
```

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server with HMR (port 5173) |
| `npm run build` | Type-check + production build to `dist/` |
| `npm run preview` | Serve the production build on port 4173 |
| `npx tsc --noEmit` | Type-check only |
