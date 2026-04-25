# 工具在 StateVerge 中的角色矩阵

| Tool | Role in StateVerge | Replaceable? | Why Needed | Output Produced | Evidence Value | IRS Relevance |
|------|-------------------|-------------|------------|-----------------|----------------|---------------|
| Cursor | Code generation, refactors, multi-file edits for pipelines & integrations | Partially (other IDEs) | High-velocity work on a large repo | Source, configs, `docs/`, `scripts/` | Commit history, scope of features | R&D / software (consult CPA) |
| Runway | AI video / lip-sync related generation (user workflow) | Yes (other vendors) | When licensed account supports API or manual | Scene MP4, lipsync I/O | Download paths, `ltx_scene_plan` / lipsync inputs | R&D / COGS- adjacent (consult CPA) |
| LTX | Main cinematic / scene block generation (user workflow) | Yes | Core look for `narrative_main` and scene plans | `ltx`-related outputs, batch prompts | `ltx_batch_helper` exports, `narrative_main` | R&D (consult CPA) |
| HeyGen | Presenter / host segment (when API path used) | Yes | Optional automated host layer | Host MP4s | `heygen` integration files (if present) | R&D (consult CPA) |
| ElevenLabs | Voice / narration synthesis | Yes | TTS for presenter or narration | WAV / MP3 | `audio/`, integration logs | R&D (consult CPA) |
| Envato | Licensed stock, music, SFX, title packages | Partially (other libraries) | Legal packaging for distributed video | Packaged `final_packaged` | `packaging_engine`, `assets/envato` | License + R&D (consult CPA) |
| OpenAI | Scripting, summarization, planning assistance | Yes | Text workflows not hard-coded in product | Text artifacts in `docs/`, `script/` | Prompt logs if retained (never commit secrets) | R&D (consult CPA) |
| FFmpeg | **Deterministic** render, norm, concat, audio staging | **Core engineering dependency** | Final reproducible output | `narrative_main`, packaged outputs, presenter concat | `ffmpeg` in repo tooling | R&D; capitalizable vs expense per CPA |
| Neon | Postgres (if used) | Yes | App data | DB rows | Migrations, schema | Hosting / R&D (consult CPA) |
| Vercel | Front-end or API deploy (if used) | Yes | Public or preview URLs | Web app | Vercel dashboard exports | R&D (consult CPA) |
| GitHub | Version control, review, issues | No for serious SW | Provenance and collaboration | All commits / PRs | `github.com/...` history | R&D default tool |
| CapCut | Optional rough editing / tests | Yes | Non-final iteration | Exports in Downloads / scratch | N/A or local files | R&D if business-related (CPA) |
| Hostinger | Domains, hosting, email (if used) | Yes | Public presence | Site | DNS, invoices | R&D or marketing (CPA) |
