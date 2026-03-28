# Bootstrap Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an idempotent bash script that bootstraps the full maildb development environment on a fresh macOS machine with Homebrew.

**Architecture:** A single `scripts/bootstrap.sh` script organized into sections — dependency installs, database setup, configuration, and validation. Visual output matches the neon cyberdeck style of `link.sh`. Two small code changes update default connection strings to use dedicated DB roles.

**Tech Stack:** Bash, Homebrew, PostgreSQL, pgvector, Ollama, uv

**Spec:** `docs/superpowers/specs/2026-03-28-bootstrap-script-design.md`

---

### Task 1: Update Code Defaults for Dedicated DB Roles

**Files:**
- Modify: `src/maildb/config.py:10`
- Modify: `tests/conftest.py:21`
- Modify: `tests/unit/test_config.py:19`

- [ ] **Step 1: Update config.py default database_url**

In `src/maildb/config.py`, change line 10 from:

```python
    database_url: str = "postgresql://localhost:5432/maildb"
```

to:

```python
    database_url: str = "postgresql://maildb@localhost:5432/maildb"
```

- [ ] **Step 2: Update conftest.py test database fallback**

In `tests/conftest.py`, change line 21 from:

```python
            "postgresql://postgres:postgres@localhost:5432/maildb_test",
```

to:

```python
            "postgresql://maildb_test@localhost:5432/maildb_test",
```

- [ ] **Step 3: Update test_config.py assertion to match new default**

In `tests/unit/test_config.py`, change line 19 from:

```python
    assert settings.database_url == "postgresql://localhost:5432/maildb"
```

to:

```python
    assert settings.database_url == "postgresql://maildb@localhost:5432/maildb"
```

- [ ] **Step 4: Run unit tests to verify**

Run: `uv run pytest tests/unit/test_config.py -v`

Expected: all tests pass, including `test_settings_defaults` with the new default URL.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/config.py tests/conftest.py tests/unit/test_config.py
git commit -m "fix: update default DB URLs to use dedicated maildb roles"
```

---

### Task 2: Bootstrap Script — Shell Skeleton, Colors, and Banner

**Files:**
- Create: `scripts/bootstrap.sh`

- [ ] **Step 1: Create the script with shebang, options, color palette, and banner**

Create `scripts/bootstrap.sh` with the shell skeleton. This establishes the visual foundation — all subsequent tasks add sections to this file.

```bash
#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════╗
# ║  MAILDB BOOTSTRAP v1.0 — Environment Setup               ║
# ║  Idempotent dev environment bootstrap for macOS           ║
# ╚═══════════════════════════════════════════════════════════╝
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Colors ───────────────────────────────────────────────────
R=$'\e[0m'
DIM=$'\e[2m'
BOLD=$'\e[1m'

CYAN=$'\e[38;2;125;207;255m'
MAGENTA=$'\e[38;2;187;154;247m'
PINK=$'\e[38;2;247;118;142m'
YELLOW=$'\e[38;2;224;175;104m'
GREEN=$'\e[38;2;158;206;106m'
BLUE=$'\e[38;2;122;162;247m'
GREY=$'\e[38;2;65;72;104m'
WHITE=$'\e[38;2;192;202;245m'

BG_GREEN=$'\e[48;2;20;50;30m'
BG_YELLOW=$'\e[48;2;50;40;15m'
BG_RED=$'\e[48;2;60;20;30m'

# ── Counters ────────────────────────────────────────────────
INSTALLED=0
SKIPPED=0
FAILED=0

# ── Glyphs ──────────────────────────────────────────────────
glyph_ok="${GREEN}◆${R}"
glyph_new="${YELLOW}◆${R}"
glyph_fail="${PINK}◆${R}"

# ── Helpers ─────────────────────────────────────────────────
spin() {
  local msg="$1"
  local frames=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
  for i in 0 1 2 3 4; do
    printf "\r  ${MAGENTA}${frames[$((i % ${#frames[@]}))]}${R} ${DIM}${WHITE}%s${GREY}...${R}   " "$msg"
    sleep 0.05
  done
  printf "\r  ${GREEN}✓${R} ${WHITE}%s${R}                                          \n" "$msg"
}

status_found() {
  printf "${BG_GREEN} ${GREEN}${BOLD}FOUND${R} "
}

status_installed() {
  printf "${BG_YELLOW} ${YELLOW}${BOLD}INSTALLED${R} "
}

status_created() {
  printf "${BG_YELLOW} ${YELLOW}${BOLD}CREATED${R} "
}

status_skipped() {
  printf "${BG_GREEN} ${GREEN}${BOLD}SKIPPED${R} "
}

status_failed() {
  printf "${BG_RED} ${PINK}${BOLD}FAILED${R} "
}

check_line() {
  local label="$1"
  printf "  ${BLUE}${BOLD}│${R}  ${DIM}${CYAN}▸ ${WHITE}%-45s${R}" "$label"
}

# ── Banner ──────────────────────────────────────────────────
banner() {
  echo ""
  echo "  ${MAGENTA}${BOLD}┌─────────────────────────────────────────────────────┐${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${CYAN}${BOLD}███╗   ███╗ █████╗ ██╗██╗     ██████╗ ██████╗${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${CYAN}████╗ ████║██╔══██╗██║██║     ██╔══██╗██╔══██╗${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${CYAN}██╔████╔██║███████║██║██║     ██║  ██║██████╔╝${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${CYAN}██║╚██╔╝██║██╔══██║██║██║     ██║  ██║██╔══██╗${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${CYAN}${BOLD}██║ ╚═╝ ██║██║  ██║██║███████╗██████╔╝██████╔╝${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${CYAN}╚═╝     ╚═╝╚═╝  ╚═╝╚═╝╚══════╝╚═════╝ ╚═════╝${R}"
  echo "  ${MAGENTA}${BOLD}│${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${DIM}${MAGENTA}Environment Bootstrap v1.0${R}"
  echo "  ${MAGENTA}${BOLD}│${R}  ${DIM}${GREY}Idempotent dev environment setup for macOS${R}"
  echo "  ${MAGENTA}${BOLD}└─────────────────────────────────────────────────────┘${R}"
  echo ""
}
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/bootstrap.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(bootstrap): add shell skeleton with colors and banner"
```

---

### Task 3: Bootstrap Script — Homebrew Gate and Dependency Checks

**Files:**
- Modify: `scripts/bootstrap.sh`

- [ ] **Step 1: Add the Homebrew prerequisite check**

Append after the `banner()` function:

```bash
# ── Homebrew Gate ───────────────────────────────────────────
require_homebrew() {
  if ! command -v brew &>/dev/null; then
    echo "  ${PINK}${BOLD}ERROR:${R} ${WHITE}Homebrew is required but not installed.${R}"
    echo ""
    echo "  ${DIM}${WHITE}Install it from ${CYAN}https://brew.sh${R}"
    echo "  ${DIM}${WHITE}Then re-run this script.${R}"
    echo ""
    exit 1
  fi
}
```

- [ ] **Step 2: Add the dependency install section**

Append after `require_homebrew`:

```bash
# ── Dependency Checks ──────────────────────────────────────
install_deps() {
  echo "  ${BLUE}${BOLD}┌─ DEPENDENCIES ──────────────────────────────────────${R}"
  echo "  ${BLUE}${BOLD}│${R}"

  # uv
  check_line "uv (Python package manager)"
  if command -v uv &>/dev/null; then
    status_found
    printf "${DIM}${GREY}$(uv --version)${R}\n"
    ((SKIPPED++)) || true
  else
    status_installed
    echo ""
    brew install uv
    ((INSTALLED++)) || true
  fi

  # PostgreSQL
  check_line "PostgreSQL"
  if command -v psql &>/dev/null; then
    local pg_ver
    pg_ver="$(psql --version | grep -oE '[0-9]+' | head -1)"
    if [[ "$pg_ver" -ge 16 ]]; then
      status_found
      printf "${DIM}${GREY}v${pg_ver}${R}\n"
      ((SKIPPED++)) || true
    else
      status_failed
      printf "${DIM}${PINK}v${pg_ver} < 16${R}\n"
      echo "  ${BLUE}${BOLD}│${R}    ${YELLOW}PostgreSQL 16+ required. Install manually or run:${R}"
      echo "  ${BLUE}${BOLD}│${R}    ${DIM}${WHITE}brew install postgresql@18${R}"
      ((FAILED++)) || true
      exit 1
    fi
  else
    status_installed
    echo ""
    brew install postgresql@18
    ((INSTALLED++)) || true
  fi

  # pgvector
  check_line "pgvector extension"
  if brew list pgvector &>/dev/null; then
    status_found
    echo ""
    ((SKIPPED++)) || true
  else
    status_installed
    echo ""
    brew install pgvector
    ((INSTALLED++)) || true
  fi

  # Ollama
  check_line "Ollama"
  if command -v ollama &>/dev/null; then
    status_found
    printf "${DIM}${GREY}$(ollama --version 2>/dev/null || echo "installed")${R}\n"
    ((SKIPPED++)) || true
  else
    status_installed
    echo ""
    brew install ollama
    ((INSTALLED++)) || true
  fi

  echo "  ${BLUE}${BOLD}│${R}"
  echo "  ${BLUE}${BOLD}└──────────────────────────────────────────────────────${R}"
  echo ""
}
```

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(bootstrap): add Homebrew gate and dependency checks"
```

---

### Task 4: Bootstrap Script — Service Management

**Files:**
- Modify: `scripts/bootstrap.sh`

- [ ] **Step 1: Add the service startup section**

Append after `install_deps`:

```bash
# ── Services ───────────────────────────────────────────────
start_services() {
  echo "  ${BLUE}${BOLD}┌─ SERVICES ──────────────────────────────────────────${R}"
  echo "  ${BLUE}${BOLD}│${R}"

  # PostgreSQL
  check_line "PostgreSQL service"
  if pg_isready -q 2>/dev/null; then
    status_found
    printf "${DIM}${GREY}running${R}\n"
  else
    status_created
    printf "${DIM}${GREY}starting${R}\n"
    brew services start postgresql@18 2>/dev/null \
      || brew services start postgresql 2>/dev/null \
      || true
    # Wait for it to be ready
    local retries=10
    while ! pg_isready -q 2>/dev/null; do
      ((retries--)) || true
      if [[ $retries -le 0 ]]; then
        echo "  ${BLUE}${BOLD}│${R}    ${PINK}${BOLD}PostgreSQL failed to start.${R}"
        echo "  ${BLUE}${BOLD}│${R}    ${DIM}${WHITE}Try: ${CYAN}brew services restart postgresql@18${R}"
        exit 1
      fi
      sleep 1
    done
  fi

  # Ollama
  check_line "Ollama service"
  if curl -sf http://localhost:11434/api/tags &>/dev/null; then
    status_found
    printf "${DIM}${GREY}running${R}\n"
  else
    status_created
    printf "${DIM}${GREY}starting${R}\n"
    brew services start ollama 2>/dev/null || true
    local retries=10
    while ! curl -sf http://localhost:11434/api/tags &>/dev/null; do
      ((retries--)) || true
      if [[ $retries -le 0 ]]; then
        echo "  ${BLUE}${BOLD}│${R}    ${PINK}${BOLD}Ollama failed to start.${R}"
        echo "  ${BLUE}${BOLD}│${R}    ${DIM}${WHITE}Try: ${CYAN}brew services restart ollama${R}"
        exit 1
      fi
      sleep 1
    done
  fi

  echo "  ${BLUE}${BOLD}│${R}"
  echo "  ${BLUE}${BOLD}└──────────────────────────────────────────────────────${R}"
  echo ""
}
```

- [ ] **Step 2: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(bootstrap): add service management for PostgreSQL and Ollama"
```

---

### Task 5: Bootstrap Script — Database Roles and Databases

**Files:**
- Modify: `scripts/bootstrap.sh`

- [ ] **Step 1: Add the database setup section**

Append after `start_services`:

```bash
# ── Database Setup ─────────────────────────────────────────
setup_database() {
  echo "  ${BLUE}${BOLD}┌─ DATABASE ──────────────────────────────────────────${R}"
  echo "  ${BLUE}${BOLD}│${R}"

  # Role: maildb
  check_line "Role: maildb"
  if psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='maildb'" | grep -q 1; then
    status_found
    echo ""
    ((SKIPPED++)) || true
  else
    psql -d postgres -c "CREATE ROLE maildb LOGIN CREATEDB;" &>/dev/null
    status_created
    echo ""
    ((INSTALLED++)) || true
  fi

  # Role: maildb_test
  check_line "Role: maildb_test"
  if psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='maildb_test'" | grep -q 1; then
    status_found
    echo ""
    ((SKIPPED++)) || true
  else
    psql -d postgres -c "CREATE ROLE maildb_test LOGIN CREATEDB;" &>/dev/null
    status_created
    echo ""
    ((INSTALLED++)) || true
  fi

  # Database: maildb
  check_line "Database: maildb"
  if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='maildb'" | grep -q 1; then
    status_found
    echo ""
    ((SKIPPED++)) || true
  else
    createdb -O maildb maildb
    status_created
    echo ""
    ((INSTALLED++)) || true
  fi

  # Extension: vector on maildb
  check_line "Extension: vector (maildb)"
  psql -d maildb -c "CREATE EXTENSION IF NOT EXISTS vector;" &>/dev/null
  status_found
  echo ""

  # Database: maildb_test
  check_line "Database: maildb_test"
  if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='maildb_test'" | grep -q 1; then
    status_found
    echo ""
    ((SKIPPED++)) || true
  else
    createdb -O maildb_test maildb_test
    status_created
    echo ""
    ((INSTALLED++)) || true
  fi

  # Extension: vector on maildb_test
  check_line "Extension: vector (maildb_test)"
  psql -d maildb_test -c "CREATE EXTENSION IF NOT EXISTS vector;" &>/dev/null
  status_found
  echo ""

  echo "  ${BLUE}${BOLD}│${R}"
  echo "  ${BLUE}${BOLD}└──────────────────────────────────────────────────────${R}"
  echo ""
}
```

- [ ] **Step 2: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(bootstrap): add database roles and database creation"
```

---

### Task 6: Bootstrap Script — Configuration, Model Pull, and Validation

**Files:**
- Modify: `scripts/bootstrap.sh`

- [ ] **Step 1: Add the configuration section**

Append after `setup_database`:

```bash
# ── Configuration ──────────────────────────────────────────
write_config() {
  echo "  ${BLUE}${BOLD}┌─ CONFIGURATION ─────────────────────────────────────${R}"
  echo "  ${BLUE}${BOLD}│${R}"

  # .env file
  check_line ".env file"
  if [[ -f "$PROJECT_DIR/.env" ]]; then
    status_skipped
    printf "${DIM}${GREY}already exists${R}\n"
    ((SKIPPED++)) || true
  else
    cat > "$PROJECT_DIR/.env" <<'DOTENV'
MAILDB_DATABASE_URL=postgresql://maildb@localhost:5432/maildb
MAILDB_TEST_DATABASE_URL=postgresql://maildb_test@localhost:5432/maildb_test
MAILDB_OLLAMA_URL=http://localhost:11434
MAILDB_EMBEDDING_MODEL=nomic-embed-text
DOTENV
    status_created
    echo ""
    ((INSTALLED++)) || true
  fi

  # Ollama model
  check_line "Model: nomic-embed-text"
  if ollama list 2>/dev/null | grep -q "nomic-embed-text"; then
    status_found
    echo ""
    ((SKIPPED++)) || true
  else
    status_installed
    printf "${DIM}${GREY}pulling...${R}\n"
    ollama pull nomic-embed-text
    ((INSTALLED++)) || true
  fi

  echo "  ${BLUE}${BOLD}│${R}"
  echo "  ${BLUE}${BOLD}└──────────────────────────────────────────────────────${R}"
  echo ""
}
```

- [ ] **Step 2: Add the validation section**

Append after `write_config`:

```bash
# ── Validation ─────────────────────────────────────────────
validate() {
  echo "  ${BLUE}${BOLD}┌─ VALIDATION ────────────────────────────────────────${R}"
  echo "  ${BLUE}${BOLD}│${R}"

  # uv sync
  check_line "Python dependencies (uv sync)"
  cd "$PROJECT_DIR"
  if uv sync --quiet 2>/dev/null; then
    status_found
    printf "${DIM}${GREY}synced${R}\n"
  else
    status_failed
    echo ""
    echo "  ${BLUE}${BOLD}│${R}    ${PINK}uv sync failed. Check output above.${R}"
    ((FAILED++)) || true
  fi

  # Unit test smoke test
  check_line "Unit tests (smoke test)"
  if uv run pytest tests/unit/ -q --no-header --tb=line 2>/dev/null; then
    status_found
    printf "${DIM}${GREY}passed${R}\n"
  else
    status_failed
    echo ""
    echo "  ${BLUE}${BOLD}│${R}    ${PINK}Unit tests failed. Run: ${WHITE}uv run just test-unit${R}"
    ((FAILED++)) || true
  fi

  echo "  ${BLUE}${BOLD}│${R}"
  echo "  ${BLUE}${BOLD}└──────────────────────────────────────────────────────${R}"
  echo ""
}
```

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(bootstrap): add configuration, model pull, and validation"
```

---

### Task 7: Bootstrap Script — Summary Panel and Main Execution

**Files:**
- Modify: `scripts/bootstrap.sh`

- [ ] **Step 1: Add the summary panel and main execution block**

Append after `validate`:

```bash
# ── Summary ────────────────────────────────────────────────
summary() {
  local total=$((INSTALLED + SKIPPED + FAILED))

  if [[ $FAILED -eq 0 ]]; then
    echo "  ${GREEN}${BOLD}┌─ ALL SYSTEMS NOMINAL ───────────────────────────────${R}"
    echo "  ${GREEN}${BOLD}│${R}"
    [[ $SKIPPED -gt 0 ]]   && echo "  ${GREEN}${BOLD}│${R}  ${glyph_ok} ${GREEN}${BOLD}${SKIPPED}${R} ${WHITE}already in place${R}" || true
    [[ $INSTALLED -gt 0 ]] && echo "  ${GREEN}${BOLD}│${R}  ${glyph_new} ${YELLOW}${BOLD}${INSTALLED}${R} ${WHITE}newly installed${R}" || true
    echo "  ${GREEN}${BOLD}│${R}"
    echo "  ${GREEN}${BOLD}│${R}  ${DIM}${WHITE}Ready to go:${R}"
    echo "  ${GREEN}${BOLD}│${R}    ${DIM}${CYAN}uv run just dev${R}    ${DIM}${GREY}# start dev server${R}"
    echo "  ${GREEN}${BOLD}│${R}    ${DIM}${CYAN}uv run just check${R}  ${DIM}${GREY}# fmt + lint + test${R}"
    echo "  ${GREEN}${BOLD}│${R}"
    echo "  ${GREEN}${BOLD}└──────────────────────────────────────────────────────${R}"
  else
    echo "  ${YELLOW}${BOLD}┌─ BOOTSTRAP INCOMPLETE ──────────────────────────────${R}"
    echo "  ${YELLOW}${BOLD}│${R}"
    [[ $SKIPPED -gt 0 ]]   && echo "  ${YELLOW}${BOLD}│${R}  ${glyph_ok} ${GREEN}${BOLD}${SKIPPED}${R} ${WHITE}already in place${R}" || true
    [[ $INSTALLED -gt 0 ]] && echo "  ${YELLOW}${BOLD}│${R}  ${glyph_new} ${YELLOW}${BOLD}${INSTALLED}${R} ${WHITE}newly installed${R}" || true
    [[ $FAILED -gt 0 ]]    && echo "  ${YELLOW}${BOLD}│${R}  ${glyph_fail} ${PINK}${BOLD}${FAILED}${R} ${WHITE}failed${R}" || true
    echo "  ${YELLOW}${BOLD}│${R}"
    echo "  ${YELLOW}${BOLD}│${R}  ${DIM}${WHITE}Fix the failures above and re-run this script.${R}"
    echo "  ${YELLOW}${BOLD}│${R}"
    echo "  ${YELLOW}${BOLD}└──────────────────────────────────────────────────────${R}"
  fi

  echo ""
}

# ── Run ─────────────────────────────────────────────────────
banner
spin "Initializing bootstrap"
spin "Detecting environment"

require_homebrew
install_deps
start_services
setup_database
write_config
validate
summary

exit $((FAILED > 0 ? 1 : 0))
```

- [ ] **Step 2: Verify the full script runs end-to-end**

```bash
./scripts/bootstrap.sh
```

Expected: banner displays, all sections run, summary shows results. On this machine (which already has PostgreSQL and no pgvector), it should install pgvector, create the roles/databases, generate `.env`, and pass unit tests.

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(bootstrap): add summary panel and main execution flow"
```

---

### Task 8: Final Integration Test

**Files:**
- No new files — this is a validation task

- [ ] **Step 1: Run the bootstrap script from scratch**

To test idempotency, run it twice:

```bash
./scripts/bootstrap.sh
```

Expected: first run creates roles, databases, `.env`, and pulls model. All checks should pass.

- [ ] **Step 2: Run it again to verify idempotency**

```bash
./scripts/bootstrap.sh
```

Expected: second run shows everything as `FOUND` or `SKIPPED`. No new installs. Summary shows "ALL SYSTEMS NOMINAL".

- [ ] **Step 3: Run the full check suite**

```bash
uv run just check
```

Expected: format, lint, and all tests (unit + integration) pass. This confirms the database roles, databases, and connection strings are all wired correctly.

- [ ] **Step 4: Final commit if any tweaks were needed**

```bash
git add -A
git commit -m "feat: complete bootstrap script for maildb dev environment"
```
