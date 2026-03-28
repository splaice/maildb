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
