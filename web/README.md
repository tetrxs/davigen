# davigen UI

React 19 + TypeScript + Vite, components from [shadcn/ui](https://ui.shadcn.com) (style *base-nova*, on Base UI),
Tailwind CSS 4, lucide icons, Geist and Instrument Serif.

```bash
npm install
npm run build   # → ../davigen/ui (served by davigen/server.py)
npm run dev     # hot reload; /api, /basic, /project and /catalog are proxied to 127.0.0.1:8765
```

- `src/lib/api.ts` – the server's JSON API and its types
- `src/lib/app-state.tsx` – routing (hash), the open project, running jobs, toasts
- `src/pages/` – one file per page; `src/components/` – shared pieces; `src/components/ui/` – shadcn components
